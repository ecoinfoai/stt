# NixOS VM test of `services.stt` (quickstart B, steps 1-11; contracts/module.md sections 1-3).
#
# There is no GPU and there are no models in the VM, so every transcribe job the
# worker runs ends `failed(gpu_unavailable)`: the engine checks the GPU before the
# models (contracts/cli.md section 1.1). That is enough to exercise intake, the
# queue, the worker, the GPU lock, deletion and the units' isolation on real systemd.
#
# Nodes: `server` runs the module without a token (no model unit); `fetcher` runs it
# with a dummy token file, only to check the model unit's settings. Users on `server`:
# `caller` is in `stt-requests` (the bhu-llm service account stands in), `operator`
# is not (the operator holding the GPU lock from the command line).
{
  pkgs,
  module,
  package,
}:
let
  audio = pkgs.runCommand "stt-vm-audio" { nativeBuildInputs = [ pkgs.ffmpeg ]; } ''
    mkdir $out
    ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=2" \
      -ac 1 -ar 16000 $out/two-seconds.wav
    # Silent and low bit rate, just over the 3-hour limit (10800 s).
    ffmpeg -hide_banner -loglevel error -f lavfi -i "anullsrc=r=8000:cl=mono" -t 10830 \
      -c:a libopus -b:a 6k $out/over-three-hours.ogg
    printf 'this is not audio\n' > $out/corrupt.mp3
  '';

  # Stands in for the NVIDIA driver library at /run/opengl-driver/lib: loading it never
  # returns, so the engine stays in its GPU check and the job stays `running`.
  hangingCuda = pkgs.runCommandCC "stt-vm-hanging-libcuda" { } ''
    mkdir -p $out/lib
    printf '#include <unistd.h>\n__attribute__((constructor)) static void hang(void) { sleep(600); }\n' > hang.c
    $CC -shared -fPIC -o $out/lib/libcuda.so.1 hang.c
    ln -s libcuda.so.1 $out/lib/libcuda.so
  '';

  # Leaves what a worker killed mid-job leaves: a `running` record, its recording and
  # a work folder with a temporary WAV, with no worker holding worker.lock.
  deadWorker = pkgs.writeText "stt-dead-worker.py" ''
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    from stt.server import deadlines
    from stt.server.layout import Layout, hold
    from stt.server.records import Audio, Job, load_jobs, next_seq, save_audio, save_job

    request_id = sys.argv[1]
    layout = Layout(Path("/var/lib/stt"))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with hold(layout.state_lock):
        recording = layout.audio / request_id
        recording.mkdir(mode=0o700)
        (recording / "audio.wav").write_bytes(b"RIFF")
        save_audio(layout, Audio(
            audio_id=request_id, original_name="lecture.wav", owner="dead-worker-caller",
            received_at=now, hard_deadline=deadlines.hard_deadline(now),
            policy={"after_job": request_id}, users=(request_id,),
        ))
        save_job(layout, Job(
            request_id=request_id, requested_by="dead-worker-caller", seq=next_seq(load_jobs(layout)),
            accepted_at=now, audio_id=request_id, language="en", diarize=False, hints=None,
            delete_audio="after_job", duration_s=2.0, state="running",
            expires_at=deadlines.expires_at(now), started_at=now,
        ))
        scratch = layout.work / request_id / "tmp"
        scratch.mkdir(mode=0o700, parents=True)
        (scratch / "diarize.wav").write_bytes(bytes(4096))
  '';
in
pkgs.testers.runNixOSTest {
  name = "stt-server";

  nodes.server = {
    imports = [ module ];
    services.stt = {
      enable = true;
      inherit package;
    };
    # The test moves the clock itself (midnight, the 23h58m retention).
    services.timesyncd.enable = false;
    users.users.caller = {
      isNormalUser = true;
      extraGroups = [ "stt-requests" ];
    };
    users.users.operator.isNormalUser = true;
    environment.systemPackages = [ pkgs.acl ];
    virtualisation = {
      memorySize = 2048;
      cores = 2;
    };
  };

  nodes.fetcher = {
    imports = [ module ];
    services.stt = {
      enable = true;
      inherit package;
      huggingFaceTokenFile = "/etc/stt-test/hf-token";
    };
    environment.etc."stt-test/hf-token".text = "HF_TOKEN=hf_vmtestdummytoken\n";
    virtualisation.memorySize = 1024;
  };

  testScript = ''
    import base64
    import json
    import shlex
    import time
    import uuid
    from datetime import datetime, timedelta, timezone

    STATE = "/var/lib/stt"
    INBOX = f"{STATE}/requests/inbox"
    OUTBOX = f"{STATE}/requests/outbox"
    GPU_LOCK = "/run/stt/gpu.lock"
    AUDIO = "${audio}"
    SLEEP = "/run/current-system/sw/bin/sleep"
    FLOCK = "/run/current-system/sw/bin/flock"
    REQUESTER = "owui-marker-5821"
    OTHER = "owui-other-3307"
    HINTS = ["zebrafishquartz", "lanternmossword"]
    KST = timezone(timedelta(hours=9))
    RETENTION = timedelta(hours=23, minutes=58)
    UNITS = ("stt-intake.service", "stt-worker.service", "stt-sweep.service")


    def as_user(user, command):
        return f"runuser -u {user} -- sh -c {shlex.quote(command)}"


    def prop(unit, name, node=server):
        return node.succeed(f"systemctl show -P {name} {unit}").strip()


    def contract_time(text):
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


    def vm_now():
        return datetime.fromtimestamp(int(server.succeed("date +%s")), timezone.utc)


    def set_clock(moment):
        server.succeed(f"date -s @{int(moment.timestamp())}")


    def put(name, data, mode="0640"):
        """Writes an inbox file as the caller: `<name>.tmp`, then a rename (contracts section 3)."""
        encoded = base64.b64encode(data).decode()
        server.succeed(as_user(
            "caller",
            f"umask 0077; echo {encoded} | base64 -d > {INBOX}/{name}.tmp"
            f" && chmod {mode} {INBOX}/{name}.tmp && mv {INBOX}/{name}.tmp {INBOX}/{name}",
        ))


    def attach(request_id, source, extension):
        target = f"{INBOX}/{request_id}.audio.{extension}"
        server.succeed(as_user("caller", f"cp {source} {target} && chmod 0640 {target}"))


    def transcribe(request_id=None, delete_audio="after_job", requester=REQUESTER, extension="wav", **extra):
        request_id = request_id or str(uuid.uuid4())
        doc = {
            "schema_version": 1, "kind": "transcribe", "request_id": request_id,
            "requested_by": requester, "audio": {"file": f"{request_id}.audio.{extension}"},
            "language": "en", "diarize": False, "delete_audio": delete_audio,
        }
        doc.update(extra)
        return doc


    def submit(doc, source=f"{AUDIO}/two-seconds.wav", extension="wav", mode="0640"):
        request_id = doc["request_id"]
        if source is not None:
            attach(request_id, source, extension)
        put(f"{request_id}.json", json.dumps(doc).encode(), mode)
        return request_id


    def delete(target, scope, requester=REQUESTER):
        return submit({
            "schema_version": 1, "kind": "delete", "request_id": str(uuid.uuid4()),
            "requested_by": requester, "target": target, "scope": scope,
        }, source=None)


    def response(request_id, timeout=15):
        """The response, read as the caller once it exists."""
        path = f"{OUTBOX}/{request_id}.json"
        server.wait_until_succeeds(as_user("caller", f"test -r {path}"), timeout=timeout)
        return json.loads(server.succeed(as_user("caller", f"cat {path}")))


    def wait_response(request_id, predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while True:
            doc = response(request_id, timeout)
            if predicate(doc):
                return doc
            assert time.monotonic() < deadline, doc
            time.sleep(1)


    def failed_with(reason):
        return lambda doc: doc["state"] == "failed" and doc["failure"]["reason"] == reason


    def inbox_empty():
        server.wait_until_succeeds(f"test -z \"$(ls -A {INBOX})\"", timeout=15)


    def hold_gpu(unit, seconds):
        """The operator holds the GPU lock from the command line, read-only like `stt transcribe`."""
        server.succeed(f"systemd-run --uid=operator --unit={unit} {FLOCK} {GPU_LOCK} {SLEEP} {seconds}")
        server.wait_until_fails(f"flock -n {GPU_LOCK} true", timeout=15)


    def idle(unit):
        server.wait_until_succeeds(f"systemctl show -P ActiveState {unit} | grep -Ex 'inactive|failed'", timeout=60)


    def sweep():
        server.succeed("systemctl start stt-sweep.service")
        assert prop("stt-sweep.service", "Result") == "success"


    start_all()
    server.wait_for_unit("multi-user.target")
    for unit in ("stt-intake.path", "stt-worker.path", "stt-sweep.timer"):
        server.succeed(f"systemctl is-active {unit}")

    with subtest("folders, lock file and environment follow the contract (contracts section 2)"):
        def owner_mode(path):
            return server.succeed(f"stat -c '%a %U %G' {path}").strip()

        assert owner_mode(STATE) == "751 stt stt", owner_mode(STATE)
        assert owner_mode(f"{STATE}/requests") == "751 stt stt"
        assert owner_mode(INBOX) == "2770 stt stt-requests", owner_mode(INBOX)
        assert owner_mode(OUTBOX) == "2750 stt stt-requests", owner_mode(OUTBOX)
        for name in ("jobs", "audio", "work", "queue"):
            assert owner_mode(f"{STATE}/{name}") == "700 stt stt", (name, owner_mode(f"{STATE}/{name}"))
        assert owner_mode("/run/stt") == "755 stt stt"
        assert owner_mode(GPU_LOCK) == "644 stt stt", owner_mode(GPU_LOCK)
        assert owner_mode("/var/lib/stt-models") == "750 stt stt"
        server.succeed(f"getfacl -p {INBOX} | grep -x 'default:user:stt:rwx'")
        server.succeed("id -nG caller | grep -qw stt-requests")
        server.fail(as_user("operator", f"touch {INBOX}/intruder.json"))
        server.fail(as_user("operator", f"ls {OUTBOX}"))
        assert server.succeed("bash -lc 'echo $STT_GPU_LOCK'").strip() == GPU_LOCK
        server.fail("systemctl cat stt-models.service")

    with subtest("isolation settings (quickstart B-8, FR-015, FR-035)"):
        for unit in UNITS:
            assert prop(unit, "MemorySwapMax") == "0", (unit, prop(unit, "MemorySwapMax"))
            assert prop(unit, "LimitCORE") == "0", (unit, prop(unit, "LimitCORE"))
            assert prop(unit, "PrivateNetwork") == "yes", unit
            assert prop(unit, "RestrictAddressFamilies") == "AF_UNIX", (unit, prop(unit, "RestrictAddressFamilies"))
            assert prop(unit, "User") == "stt" and prop(unit, "Group") == "stt", unit
            assert prop(unit, "UMask") == "0027", unit
            assert prop(unit, "Type") == "oneshot", unit
            assert prop(unit, "ProtectSystem") == "strict", unit
            assert prop(unit, "PrivatePIDs") == "yes", unit
            assert prop(unit, "CapabilityBoundingSet") == "", (unit, prop(unit, "CapabilityBoundingSet"))
            assert "75" in prop(unit, "SuccessExitStatus").split(), (unit, prop(unit, "SuccessExitStatus"))
            assert prop(unit, "StateDirectory") == "stt", unit
            assert prop(unit, "StateDirectoryMode") == "0751", (unit, prop(unit, "StateDirectoryMode"))
            environment = prop(unit, "Environment")
            for name in ("HF_HOME", "HF_HUB_OFFLINE", "LD_LIBRARY_PATH"):
                assert name not in environment, (unit, environment)
        worker = "stt-worker.service"
        devices = prop(worker, "DeviceAllow")
        for device in ("/dev/nvidiactl", "/dev/nvidia0", "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools"):
            assert f"{device} rw" in devices, devices
        assert "/dev/nvidia-modeset" not in devices, devices
        assert prop(worker, "DevicePolicy") == "closed"
        assert prop(worker, "PrivateDevices") == "no"
        assert prop(worker, "RemoveIPC") == "yes"
        assert prop(worker, "KillSignal") == "9", prop(worker, "KillSignal")
        server.succeed(f"grep -x 'X-RestartIfChanged=false' /etc/systemd/system/{worker}")
        assert prop(worker, "TimeoutStartUSec") == "infinity", prop(worker, "TimeoutStartUSec")
        for unit in ("stt-intake.service", worker):
            assert prop(unit, "StartLimitIntervalUSec") == "0", (unit, prop(unit, "StartLimitIntervalUSec"))
        assert prop(worker, "MemoryMax") == str(12 * 1024**3), prop(worker, "MemoryMax")
        assert "stt-models.service" not in prop(worker, "After")
        assert server.succeed(f"grep '^ExecStart=' /etc/systemd/system/{worker}").strip().endswith(
            "/bin/stt server work --gpu-lock /run/stt/gpu.lock"
        )
        for unit, memory, timeout in (
            ("stt-intake.service", 1024**3, "10min"),
            ("stt-sweep.service", 512 * 1024**2, "5min"),
        ):
            assert prop(unit, "PrivateDevices") == "yes", unit
            assert prop(unit, "RemoveIPC") == "no", unit
            assert prop(unit, "MemoryMax") == str(memory), (unit, prop(unit, "MemoryMax"))
            assert prop(unit, "TimeoutStartUSec") == timeout, (unit, prop(unit, "TimeoutStartUSec"))
        timer = "stt-sweep.timer"
        assert prop(timer, "AccuracyUSec") == "1s", prop(timer, "AccuracyUSec")
        assert prop(timer, "Persistent") == "yes"
        assert "OnCalendar=*-*-* *:*:00" in prop(timer, "TimersCalendar"), prop(timer, "TimersCalendar")
        assert "requests/inbox/*.json" in prop("stt-intake.path", "Paths")
        assert "queue/*" in prop("stt-worker.path", "Paths")

    with subtest("a request is queued within seconds and waits behind the operator's GPU lock (B-1, B-7)"):
        hold_gpu("gpu-hold-1", 20)
        first = transcribe(hints=HINTS)
        started = time.monotonic()
        submit(first)
        queued = response(first["request_id"], timeout=10)
        assert time.monotonic() - started < 10
        assert queued["state"] == "queued" and queued["queue_position"] == 1, queued
        inbox_empty()
        time.sleep(6)
        still = response(first["request_id"])
        assert still["state"] == "queued" and still["queue_position"] == 1, still
        server.succeed("systemctl is-active gpu-hold-1.service")

    with subtest("after the lock is released the job fails gpu_unavailable and its after_job audio is gone (B-2)"):
        done = wait_response(first["request_id"], failed_with("gpu_unavailable"), timeout=60)
        assert "deleted_at" in done["audio"], done
        server.succeed(f"test ! -e {STATE}/audio/{first['request_id']}")
        server.fail("systemctl is-active gpu-hold-1.service")

    with subtest("the worker does not wake again once the queue is empty (B-11)"):
        idle("stt-worker.service")
        server.succeed(f"test -z \"$(ls -A {STATE}/queue)\"")
        before = prop("stt-worker.service", "ExecMainStartTimestampMonotonic")
        time.sleep(10)
        assert prop("stt-worker.service", "ExecMainStartTimestampMonotonic") == before
        assert prop("stt-worker.service", "ActiveState") == "inactive"

    with subtest("a burst of requests is answered and intake keeps watching"):
        burst = []
        for _ in range(8):
            burst.append(delete(str(uuid.uuid4()), "result"))
            time.sleep(0.6)
        for request_id in burst:
            assert response(request_id)["outcome"] == "refused"
        inbox_empty()
        server.succeed("systemctl is-active stt-intake.path stt-worker.path")

    with subtest("rejections answer and leave the inbox empty (B-6)"):
        put(f"{first['request_id']}.json", json.dumps(first).encode())
        duplicate = wait_response(first["request_id"], lambda doc: "duplicate_refused_at" in doc)
        assert duplicate["state"] == "failed", duplicate
        inbox_empty()

        put("not-a-request.json", b"{}")
        inbox_empty()
        server.succeed(f"test ! -e {OUTBOX}/not-a-request.json")

        bad_schema = transcribe()
        del bad_schema["language"]
        submit(bad_schema)
        refused = response(bad_schema["request_id"])
        assert refused["kind"] == "invalid" and refused["failure"]["reason"] == "invalid_request", refused
        inbox_empty()

        broken = str(uuid.uuid4())
        put(f"{broken}.json", b'{"schema_version": 1, "kind":')
        refused = response(broken)
        assert refused["kind"] == "invalid" and refused["failure"]["detail"] == "request: file is not UTF-8 JSON", refused
        inbox_empty()

        private = transcribe()
        submit(private, mode="0600")
        refused = response(private["request_id"])
        assert refused["kind"] == "invalid" and refused["failure"]["detail"] == "request: file is not readable", refused
        inbox_empty()

        linked = transcribe()
        server.succeed(as_user("caller", f"ln -s {AUDIO}/two-seconds.wav {INBOX}/{linked['request_id']}.audio.wav"))
        submit(linked, source=None)
        assert failed_with("invalid_request")(response(linked["request_id"]))
        inbox_empty()

        too_long = transcribe(extension="ogg")
        submit(too_long, source=f"{AUDIO}/over-three-hours.ogg", extension="ogg")
        assert failed_with("audio_too_long")(response(too_long["request_id"], timeout=30))
        inbox_empty()

        corrupt = transcribe(extension="mp3")
        submit(corrupt, source=f"{AUDIO}/corrupt.mp3", extension="mp3")
        assert failed_with("unreadable_audio")(response(corrupt["request_id"]))
        inbox_empty()

        missing = transcribe(audio={"from_request": str(uuid.uuid4())})
        submit(missing, source=None)
        assert failed_with("source_unavailable")(response(missing["request_id"]))
        inbox_empty()

    with subtest("the two deletes, someone else's job, already deleted, no result (B-5)"):
        kept = transcribe(delete_audio="at_midnight")
        submit(kept)
        wait_response(kept["request_id"], failed_with("gpu_unavailable"))
        server.succeed(f"test -d {STATE}/audio/{kept['request_id']}")
        other = transcribe(delete_audio="at_midnight")
        submit(other)
        wait_response(other["request_id"], failed_with("gpu_unavailable"))

        answer = response(delete(kept["request_id"], "everything"))
        assert answer["kind"] == "delete" and answer["outcome"] == "deleted", answer
        gone = response(kept["request_id"])
        assert gone["state"] == "deleted" and "deleted_at" in gone["audio"], gone
        server.succeed(f"test ! -e {STATE}/audio/{kept['request_id']}")
        assert response(delete(kept["request_id"], "everything"))["outcome"] == "already_deleted"
        assert response(delete(kept["request_id"], "result"))["outcome"] == "no_result"
        assert response(delete(other["request_id"], "result"))["outcome"] == "no_result"
        assert response(delete(other["request_id"], "everything", requester=OTHER))["outcome"] == "refused"
        assert failed_with("gpu_unavailable")(response(other["request_id"]))
        server.succeed(f"test -d {STATE}/audio/{other['request_id']}")
        inbox_empty()

    with subtest("stopping the worker while the engine runs ends the job interrupted, not engine_error"):
        server.succeed("mkdir -p /run/opengl-driver && ln -s ${hangingCuda}/lib /run/opengl-driver/lib")
        stopped = transcribe()
        submit(stopped)
        wait_response(stopped["request_id"], lambda doc: doc["state"] == "running", timeout=30)
        time.sleep(4)
        server.succeed("rm -r /run/opengl-driver")
        started = time.monotonic()
        server.succeed("systemctl stop stt-worker.service")
        assert time.monotonic() - started < 15, time.monotonic() - started
        assert response(stopped["request_id"])["state"] == "running", response(stopped["request_id"])
        server.succeed(f"test -d {STATE}/work/{stopped['request_id']}")
        sweep()
        assert failed_with("interrupted")(response(stopped["request_id"]))
        server.succeed(f"test ! -e {STATE}/work/{stopped['request_id']}")
        server.succeed(f"test ! -e {STATE}/audio/{stopped['request_id']}")
        server.succeed(f"test -z \"$(ls -A {STATE}/queue)\"")

    with subtest("a busy stage exits 75, which the unit counts as success"):
        server.succeed(f"systemd-run --uid=stt --unit=sweep-hold {FLOCK} {STATE}/sweep.lock {SLEEP} 30")
        server.wait_until_fails(f"flock -n {STATE}/sweep.lock true", timeout=15)
        sweep()
        assert prop("stt-sweep.service", "ExecMainStatus") == "75", prop("stt-sweep.service", "ExecMainStatus")
        server.succeed("systemctl stop sweep-hold.service")

    with subtest("the sweep clears a dead worker's traces (B-10)"):
        idle("stt-worker.service")
        dead = str(uuid.uuid4())
        server.succeed(f"runuser -u stt -- ${package}/bin/python ${deadWorker} {dead}")
        server.succeed(f"test -f {STATE}/work/{dead}/tmp/diarize.wav")
        sweep()
        assert prop("stt-sweep.service", "ExecMainStatus") == "0"
        server.succeed(f"test ! -e {STATE}/work/{dead}")
        server.succeed(f"test ! -e {STATE}/audio/{dead}")
        assert failed_with("interrupted")(response(dead))

    with subtest("an at_midnight job held behind the GPU lock loses its audio at midnight (B-3)"):
        hold_gpu("gpu-hold-2", 180)
        today = vm_now().astimezone(KST).date()
        midnight = datetime(today.year, today.month, today.day, tzinfo=KST) + timedelta(days=2)
        set_clock(midnight - timedelta(seconds=30))
        sweep()
        late = transcribe(delete_audio="at_midnight")
        submit(late)
        queued = response(late["request_id"])
        assert queued["state"] == "queued" and queued["queue_position"] == 1, queued
        assert contract_time(queued["audio"]["delete_at"]) == midnight, (queued, midnight)
        swept_at = prop("stt-sweep.service", "ExecMainStartTimestampMonotonic")
        lost = wait_response(
            late["request_id"],
            lambda doc: doc["state"] == "failed" and doc["failure"]["reason"] == "audio_deleted",
            timeout=120,
        )
        assert lost["failure"]["cause"] == "midnight", lost
        assert contract_time(lost["finished_at"]) <= midnight + timedelta(minutes=1), (lost, midnight)
        assert contract_time(lost["audio"]["deleted_at"]) <= midnight + timedelta(minutes=1), lost
        server.succeed(f"test ! -e {STATE}/audio/{late['request_id']}")

    with subtest("the sweep timer runs the sweep by itself every minute"):
        server.wait_until_succeeds(
            f"test \"$(systemctl show -P ExecMainStartTimestampMonotonic stt-sweep.service)\" != {swept_at}",
            timeout=90,
        )
        server.succeed("systemctl stop gpu-hold-2.service")

    with subtest("23h58m after acceptance the response and the records are gone (B-4)"):
        accepted = contract_time(lost["accepted_at"])
        set_clock(accepted + RETENTION - timedelta(minutes=1))
        sweep()
        server.succeed(f"test -f {OUTBOX}/{late['request_id']}.json")
        set_clock(accepted + RETENTION + timedelta(minutes=1))
        sweep()
        server.succeed(f"test ! -e {OUTBOX}/{late['request_id']}.json")
        server.succeed(f"test ! -e {STATE}/jobs/{late['request_id']}")
        server.succeed(f"test ! -e {STATE}/audio/{late['request_id']}.json")
        for folder in ("jobs", "audio", "work", "queue", "requests/inbox", "requests/outbox"):
            server.succeed(f"test -z \"$(ls -A {STATE}/{folder})\"")

    with subtest("the journal holds no hint word and no requester (B-9, FR-037)"):
        server.succeed(f"journalctl --no-pager -u stt-worker.service | grep -F '{first['request_id']} failed gpu_unavailable'")
        for word in (*HINTS, REQUESTER, OTHER):
            server.fail(f"journalctl --no-pager | grep -F {word}")

    with subtest("the model unit: network, token file, every boot, the worker after it (module section 3)"):
        fetcher.wait_for_unit("multi-user.target")
        models = "stt-models.service"
        fetcher.wait_until_succeeds(f"systemctl show -P ActiveState {models} | grep -x failed", timeout=120)
        # No network in the VM: the download fails (exit 1), by repository name, without the token.
        assert prop(models, "ExecMainStatus", fetcher) == "1", prop(models, "ExecMainStatus", fetcher)
        assert prop(models, "PrivateNetwork", fetcher) == "no"
        families = set(prop(models, "RestrictAddressFamilies", fetcher).split())
        assert families == {"AF_UNIX", "AF_INET", "AF_INET6"}, families
        assert prop(models, "RemainAfterExit", fetcher) == "yes"
        assert "/etc/stt-test/hf-token" in prop(models, "EnvironmentFiles", fetcher)
        assert prop(models, "TimeoutStartUSec", fetcher) == "2h"
        assert prop(models, "StateDirectory", fetcher) == "stt-models"
        assert prop(models, "MemorySwapMax", fetcher) == "0"
        assert prop(models, "LimitCORE", fetcher) == "0"
        assert prop(models, "RemoveIPC", fetcher) == "no"
        assert prop(models, "User", fetcher) == "stt"
        assert "multi-user.target" in prop(models, "WantedBy", fetcher)
        assert prop(models, "ConditionResult", fetcher) == "yes"
        environment = prop(models, "Environment", fetcher)
        assert "HF_HOME" not in environment and "HF_HUB_OFFLINE" not in environment, environment
        assert "HF_XET_CACHE=/var/lib/stt-models/xet" in environment.split(), environment
        assert fetcher.succeed(f"grep '^ExecStart=' /etc/systemd/system/{models}").strip().endswith(
            "/bin/stt server fetch-models --models /var/lib/stt-models"
        )
        worker = "stt-worker.service"
        assert "stt-models.service" in prop(worker, "After", fetcher).split()
        assert "stt-models.service" in prop(worker, "Wants", fetcher).split()
        fetcher.succeed(f"journalctl -u {models} | grep -F 'Failed to download model repository'")
        fetcher.fail("journalctl --no-pager | grep -F hf_vmtestdummytoken")
        # A failed model unit runs again whenever the worker starts.
        fetched_at = prop(models, "ExecMainStartTimestampMonotonic", fetcher)
        fetcher.succeed("systemctl start stt-worker.service")
        assert prop(models, "ExecMainStartTimestampMonotonic", fetcher) != fetched_at
        assert prop("stt-worker.service", "Result", fetcher) == "success"
  '';
}
