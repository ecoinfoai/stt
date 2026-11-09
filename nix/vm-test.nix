# NixOS VM test of `services.stt` (quickstart B, steps 1-11 and B-P2-1..7; contracts/module.md).
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
  inherit (pkgs) lib;

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
            received_at=now, delete_at=deadlines.purge_time(now), users=(request_id,),
        ))
        save_job(layout, Job(
            request_id=request_id, requested_by="dead-worker-caller", seq=next_seq(load_jobs(layout)),
            accepted_at=now, audio_id=request_id, language="en", diarize=False, hints=None,
            duration_s=2.0, state="running",
            expires_at=deadlines.purge_time(now), started_at=now,
        ))
        scratch = layout.work / request_id / "tmp"
        scratch.mkdir(mode=0o700, parents=True)
        (scratch / "diarize.wav").write_bytes(bytes(4096))
  '';

  # Each specialisation runs the reader instance as a test double (nix/vm-reader-double.py):
  # `<behaviour for the length probe> <behaviour for the decode>`. A double never interprets
  # the recording (contracts/reader.md section 9).
  readerDouble = ./vm-reader-double.py;
  readerDoubles = {
    reader-check = "check check";
    reader-no-verdict = "no-verdict no-verdict";
    reader-hang = "hold hold";
    reader-memory = "memory memory";
    reader-not-started = "not-started not-started";
    reader-hold-decode = "ok hold";
  };
in
pkgs.testers.runNixOSTest {
  name = "stt-server";

  nodes.server = {
    imports = [ module ];
    services.stt = {
      enable = true;
      inherit package;
    };
    # The test moves the clock forward to the next midday in Seoul, so that it cannot
    # run across the daily purge at 23:58. Never backwards: the sweep timer, which is
    # persistent, would then wait for its last trigger time.
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
    specialisation = lib.mapAttrs (_: behaviours: {
      configuration.systemd.services."stt-reader@".serviceConfig.ExecStart =
        lib.mkForce "${package}/bin/python ${readerDouble} ${behaviours}";
    }) readerDoubles;
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

  testScript =
    { nodes, ... }:
    ''
    import base64
    import io
    import json
    import shlex
    import time
    import uuid
    import wave
    from datetime import datetime, timedelta, timezone

    STATE = "/var/lib/stt"
    INBOX = f"{STATE}/requests/inbox"
    OUTBOX = f"{STATE}/requests/outbox"
    GPU_LOCK = "/run/stt/gpu.lock"
    AUDIO = "${audio}"
    HANGING_CUDA = "${hangingCuda}/lib"
    SLEEP = "/run/current-system/sw/bin/sleep"
    FLOCK = "/run/current-system/sw/bin/flock"
    REQUESTER = "owui-marker-5821"
    OTHER = "owui-other-3307"
    HINTS = ["zebrafishquartz", "lanternmossword"]
    KST = timezone(timedelta(hours=9))
    UNITS = ("stt-intake.service", "stt-worker.service", "stt-sweep.service")


    def as_user(user, command):
        return f"runuser -u {user} -- sh -c {shlex.quote(command)}"


    def prop(unit, name, node=server):
        return node.succeed(f"systemctl show -P {name} {unit}").strip()


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


    def transcribe(request_id=None, requester=REQUESTER, extension="wav", **extra):
        request_id = request_id or str(uuid.uuid4())
        doc = {
            "schema_version": 1, "kind": "transcribe", "request_id": request_id,
            "requested_by": requester, "audio": {"file": f"{request_id}.audio.{extension}"},
            "language": "en", "diarize": False,
        }
        doc.update(extra)
        return doc


    def submit(doc, source=f"{AUDIO}/two-seconds.wav", extension="wav", mode="0640"):
        request_id = doc["request_id"]
        if source is not None:
            attach(request_id, source, extension)
        put(f"{request_id}.json", json.dumps(doc).encode(), mode)
        return request_id


    def delete(target, requester=REQUESTER):
        return submit({
            "schema_version": 1, "kind": "delete", "request_id": str(uuid.uuid4()),
            "requested_by": requester, "target": target,
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


    def hang_cuda():
        server.succeed(f"mkdir -p /run/opengl-driver && ln -sfn {HANGING_CUDA} /run/opengl-driver/lib")


    def release_cuda():
        server.succeed("rm -rf /run/opengl-driver")


    start_all()
    server.wait_for_unit("multi-user.target")
    local = vm_now().astimezone(KST)
    noon = datetime(local.year, local.month, local.day, 12, tzinfo=KST)
    set_clock(noon if local < noon else noon + timedelta(days=1))
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
        # The reader socket argument that follows is checked in the reader subtest below.
        assert "/bin/stt server work --gpu-lock /run/stt/gpu.lock" in server.succeed(f"grep '^ExecStart=' /etc/systemd/system/{worker}")
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
        fetcher.wait_for_unit("multi-user.target")
        for unit, node in [(unit, server) for unit in UNITS] + [("stt-models.service", fetcher)]:
            assert prop(unit, "SystemCallArchitectures", node) == "native", (unit, prop(unit, "SystemCallArchitectures", node))
            syscall_filter = prop(unit, "SystemCallFilter", node)
            assert syscall_filter and not syscall_filter.startswith("~"), (unit, syscall_filter)
            assert prop(unit, "MemoryDenyWriteExecute", node) == "yes", (unit, prop(unit, "MemoryDenyWriteExecute", node))

    # The reader's own settings need no request, so they are checked here, before the first
    # subtest that sends one. Intake and the worker cannot work without the reader unit.
    with subtest("the reader socket and unit carry the settings of contracts/module.md section 2 (B-P2-1)"):
        reader = "stt-reader@check.service"
        socket_unit = "stt-reader.socket"
        assert prop(reader, "LoadState") == "loaded", prop(reader, "LoadState")
        assert prop(socket_unit, "LoadState") == "loaded", prop(socket_unit, "LoadState")
        server.succeed(f"systemctl is-active {socket_unit}")
        assert prop(socket_unit, "Listen") == "/run/stt-reader.sock (SequentialPacket)", prop(socket_unit, "Listen")
        assert prop(socket_unit, "Accept") == "yes"
        assert prop(socket_unit, "SocketUser") == "stt" and prop(socket_unit, "SocketGroup") == "stt"
        assert prop(socket_unit, "SocketMode") == "0600", prop(socket_unit, "SocketMode")
        assert prop(socket_unit, "MaxConnections") == "4"
        assert "sockets.target" in prop(socket_unit, "WantedBy")
        assert server.succeed("stat -c '%a %U %G' /run/stt-reader.sock").strip() == "600 stt stt"

        # `RestrictAddressFamilies=none` shows as an empty allow list; an unset one shows as `~`.
        for name, value in (
            ("DynamicUser", "yes"), ("PrivateDevices", "yes"), ("PrivateNetwork", "yes"),
            ("RestrictAddressFamilies", ""), ("ProtectHome", "yes"), ("ProtectSystem", "strict"),
            ("PrivateIPC", "yes"), ("PrivatePIDs", "yes"), ("ProtectProc", "invisible"), ("ProcSubset", "pid"),
            ("ProtectKernelTunables", "yes"), ("ProtectKernelModules", "yes"), ("ProtectKernelLogs", "yes"),
            ("ProtectControlGroups", "yes"), ("ProtectClock", "yes"), ("ProtectHostname", "yes"),
            ("RestrictNamespaces", "yes"), ("RestrictRealtime", "yes"), ("LockPersonality", "yes"),
            ("RestrictSUIDSGID", "yes"), ("NoNewPrivileges", "yes"), ("CapabilityBoundingSet", ""),
            ("SystemCallArchitectures", "native"), ("MemoryDenyWriteExecute", "yes"),
            ("MemoryMax", str(768 * 1024**2)), ("MemorySwapMax", "0"), ("LimitCORE", "0"), ("LimitFSIZE", "0"),
            ("TasksMax", "32"), ("RuntimeMaxUSec", "5min"), ("KillSignal", "9"),
            ("CollectMode", "inactive-or-failed"), ("StandardInput", "socket"), ("StandardOutput", "null"),
            ("StandardError", "journal"),
        ):
            assert prop(reader, name) == value, (name, prop(reader, name))
        # `DynamicUser=yes` prints `yes` on this systemd; that private /tmp leaves no folder on the host.
        assert prop(reader, "PrivateTmp") == "yes", prop(reader, "PrivateTmp")
        assert set(prop(reader, "InaccessiblePaths").split()) == {"/var/lib/stt", "/var/lib/stt-models", "/run/stt"}, prop(reader, "InaccessiblePaths")
        calls = set(prop(reader, "SystemCallFilter").split())
        assert calls and not prop(reader, "SystemCallFilter").startswith("~"), prop(reader, "SystemCallFilter")
        assert "read" in calls, "the filter allows the ordinary calls"
        for call in ("process_vm_readv", "process_vm_writev", "io_uring_setup", "io_uring_enter", "io_uring_register", "userfaultfd", "setuid", "chroot"):
            assert call not in calls, call
        assert server.succeed("grep '^ExecStart=' /etc/systemd/system/stt-reader@.service").strip().endswith("/bin/stt server read")

        for unit, command in (
            ("stt-intake.service", "server intake --reader-socket /run/stt-reader.sock"),
            ("stt-worker.service", "server work --gpu-lock /run/stt/gpu.lock --reader-socket /run/stt-reader.sock"),
        ):
            assert socket_unit in prop(unit, "Requires").split(), (unit, prop(unit, "Requires"))
            assert socket_unit in prop(unit, "After").split(), (unit, prop(unit, "After"))
            assert server.succeed(f"grep '^ExecStart=' /etc/systemd/system/{unit}").strip().endswith(f"/bin/stt {command}"), unit

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

    with subtest("after the lock is released the job fails gpu_unavailable and its audio stays until deleted (B-2)"):
        done = wait_response(first["request_id"], failed_with("gpu_unavailable"), timeout=60)
        assert "deleted_at" not in done["audio"], done
        assert done["expires_at"] == done["audio"]["delete_at"], done
        server.succeed(f"test -d {STATE}/audio/{first['request_id']}")
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
            burst.append(delete(str(uuid.uuid4())))
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

    with subtest("delete everything: a shared recording, someone else's job, already deleted (B-5)"):
        kept = transcribe()
        submit(kept)
        wait_response(kept["request_id"], failed_with("gpu_unavailable"))
        server.succeed(f"test -d {STATE}/audio/{kept['request_id']}")
        other = transcribe()
        submit(other)
        wait_response(other["request_id"], failed_with("gpu_unavailable"))

        answer = response(delete(kept["request_id"]))
        assert answer["kind"] == "delete" and answer["outcome"] == "deleted", answer
        gone = response(kept["request_id"])
        assert gone["state"] == "deleted" and "deleted_at" in gone["audio"], gone
        server.succeed(f"test ! -e {STATE}/audio/{kept['request_id']}")
        assert response(delete(kept["request_id"]))["outcome"] == "already_deleted"
        assert response(delete(other["request_id"], requester=OTHER))["outcome"] == "refused"
        assert failed_with("gpu_unavailable")(response(other["request_id"]))
        server.succeed(f"test -d {STATE}/audio/{other['request_id']}")
        inbox_empty()

    with subtest("stopping the worker while the engine runs ends the job interrupted, not engine_error"):
        hang_cuda()
        stopped = transcribe()
        submit(stopped)
        wait_response(stopped["request_id"], lambda doc: doc["state"] == "running", timeout=30)
        time.sleep(4)
        release_cuda()
        started = time.monotonic()
        server.succeed("systemctl stop stt-worker.service")
        assert time.monotonic() - started < 15, time.monotonic() - started
        assert response(stopped["request_id"])["state"] == "running", response(stopped["request_id"])
        server.succeed(f"test -d {STATE}/work/{stopped['request_id']}")
        sweep()
        assert failed_with("interrupted")(response(stopped["request_id"]))
        server.succeed(f"test ! -e {STATE}/work/{stopped['request_id']}")
        server.succeed(f"test -d {STATE}/audio/{stopped['request_id']}")
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
        server.succeed(f"test -d {STATE}/audio/{dead}")
        assert failed_with("interrupted")(response(dead))

    with subtest("the sweep timer runs the sweep by itself every minute"):
        swept_at = prop("stt-sweep.service", "ExecMainStartTimestampMonotonic")
        server.wait_until_succeeds(
            f"test \"$(systemctl show -P ExecMainStartTimestampMonotonic stt-sweep.service)\" != {swept_at}",
            timeout=90,
        )

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

    with subtest("no system call was blocked on either node, and a blocked call would show (B-P1-3)"):
        for node in (server, fetcher):
            kernel_log = node.succeed("journalctl -k --no-pager -o cat")
            assert "type=1326" not in kernel_log
            # Positive control: without it an empty result could mean the VM never logs seccomp kills.
            node.fail("systemd-run --wait --collect -p 'SystemCallFilter=~uname' uname")
            node.wait_until_succeeds("journalctl -k --no-pager -o cat | grep -F type=1326", timeout=30)

    # ---- P2: the reader unit (quickstart B-P2-1 .. B-P2-7) ------------------------------------
    # They come after the P1 and 001 subtests; the reader's settings are checked earlier (see above).

    TOPLEVEL = "${nodes.server.system.build.toplevel}"
    READER_INSTANCES = "systemctl list-units --all --plain --no-legend 'stt-reader@*'"
    READER_ACTIVE = "systemctl list-units --plain --no-legend --state=active 'stt-reader@*'"
    PRIVATE_FOLDERS = "find /tmp /var/tmp -maxdepth 1 -name 'systemd-private-*stt-reader*'"
    UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    # Groups the double always adds after the ones in its target list (nix/vm-reader-double.py).
    FIXED_GROUPS = ("network", "own-fd-write", "scratch-write", "other-processes")
    TARGETS_FILE = "/run/vm-double/targets.json"

    def switch_to(specialisation=None):
        """Activates the base system or one of its specialisations without touching the boot entry."""
        path = TOPLEVEL if specialisation is None else f"{TOPLEVEL}/specialisation/{specialisation}"
        server.succeed(f"{path}/bin/switch-to-configuration test")

    def no_reader_instance():
        """No `stt-reader@*` unit remains in any state, and none left a private folder on the host (US2-7)."""
        server.wait_until_succeeds(f"test -z \"$({READER_INSTANCES})\"", timeout=60)
        server.succeed(f"test -z \"$({PRIVATE_FOLDERS})\"")

    def wait_reader_instance():
        """Waits for a running reader instance and returns its unit name."""
        server.wait_until_succeeds(f"test -n \"$({READER_ACTIVE})\"", timeout=60)
        return server.succeed(f"{READER_ACTIVE} | awk '{{print $1}}' | head -n 1").strip()

    def failed_stt_units():
        listing = server.succeed("systemctl --failed --plain --no-legend")
        return {line.split()[0] for line in listing.splitlines() if line.startswith("stt")}

    def stop_worker():
        """Kills the worker with the unit's own stop; that leaves it `failed(signal)`, which is not a reader failure."""
        server.succeed("systemctl stop stt-worker.service")
        server.succeed("systemctl reset-failed stt-worker.service")

    def end_held_job(request_id):
        """Stops the worker under a running job; the sweep then ends the job `interrupted` and clears work/."""
        stop_worker()
        sweep()
        assert failed_with("interrupted")(response(request_id)), response(request_id)
        server.succeed(f"test -z \"$(ls -A {STATE}/work)\"")

    def read_wav(path):
        raw = base64.b64decode(server.succeed(f"base64 -w0 {path}"))
        with wave.open(io.BytesIO(raw)) as handle:
            return (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()), handle.readframes(handle.getnframes())

    def wait_wav(path, ready=lambda frames: len(frames) > 0):
        """Reads a WAV the worker is still writing: waits until it has content, parses and `ready` accepts it."""
        server.wait_until_succeeds(f"test -s {path}", timeout=120)
        for _ in range(30):
            try:
                fmt, frames = read_wav(path)
            except (wave.Error, EOFError):
                frames = b""
            else:
                if ready(frames):
                    return fmt, frames
            time.sleep(1)
        raise AssertionError(f"{path} never became a complete WAV")

    def wait_queued(request_id):
        return wait_response(request_id, lambda doc: doc["state"] == "queued")

    with subtest("a 2 s job passes the read stage: audio.wav is there and spec.json names no recording (B-P2-1, B-P2-2)"):
        hang_cuda()
        passed = transcribe()
        submit(passed)
        wait_response(passed["request_id"], lambda doc: doc["state"] == "running", timeout=30)
        work = f"{STATE}/work/{passed['request_id']}"
        fmt, frames = wait_wav(f"{work}/audio.wav")
        assert fmt == (1, 2, 16000), fmt
        assert abs(len(frames) // 2 - 2 * 16000) <= 1600, len(frames)
        spec = json.loads(server.succeed(f"cat {work}/spec.json"))
        assert "audio_path" not in spec, spec
        assert abs(spec["duration_s"] - 2.0) < 0.2, spec
        release_cuda()
        end_held_job(passed["request_id"])
        server.succeed(f"test -d {STATE}/audio/{passed['request_id']}")
        after = transcribe()
        submit(after)
        wait_response(after["request_id"], failed_with("gpu_unavailable"), timeout=60)
        no_reader_instance()
        assert not failed_stt_units(), failed_stt_units()

    with subtest("a text file with a recording extension is refused while a good recording in the same batch is queued (B-P2-3)"):
        hold_gpu("gpu-hold-batch", 900)
        good = transcribe()
        bad = transcribe(extension="mp3")
        # Both requests are in the inbox before intake runs, so one intake run takes the whole batch.
        server.succeed("systemctl stop stt-intake.path")
        submit(bad, source=f"{AUDIO}/corrupt.mp3", extension="mp3")
        submit(good)
        server.succeed("systemctl start stt-intake.service")
        assert prop("stt-intake.service", "Result") == "success"
        assert failed_with("unreadable_audio")(response(bad["request_id"])), response(bad["request_id"])
        assert response(good["request_id"])["state"] == "queued"
        assert response(good["request_id"])["queue_position"] == 1
        server.succeed("systemctl start stt-intake.path")
        inbox_empty()
        assert not failed_stt_units(), failed_stt_units()
        server.succeed("systemctl stop gpu-hold-batch.service")
        wait_response(good["request_id"], failed_with("gpu_unavailable"), timeout=60)
        no_reader_instance()
        assert not failed_stt_units(), failed_stt_units()

    with subtest("a reader instance reaches nothing but its own recording, and probe and decode run as different one-off users (B-P2-4)"):
        for node_name, major, minor in (("nvidia0", 195, 0), ("nvidiactl", 195, 255), ("nvidia-uvm", 510, 0)):
            server.succeed(f"mknod -m 0666 /dev/{node_name} c {major} {minor}")
        switch_to("reader-check")
        # The test runs the queue by hand (a switch starts the path unit and the timer again, so
        # they are stopped after it): the worker starts only when asked, so that the stand-in work
        # folders planted below survive the dead-worker cleanup at its start.
        server.succeed("systemctl stop stt-worker.path stt-sweep.timer")
        hang_cuda()
        stt_uid = int(server.succeed("id -u stt"))
        a, b, c, a2, p = (str(uuid.uuid4()) for _ in range(5))

        def audio_paths(i):
            return [f"{STATE}/audio/{i}", f"{STATE}/audio/{i}/audio.wav", f"{STATE}/audio/{i}.json"]

        def job_paths(i):
            return [f"{STATE}/jobs/{i}", f"{STATE}/jobs/{i}/job.json"]

        def work_paths(i):
            return [f"{STATE}/work/{i}", f"{STATE}/work/{i}/audio.wav", f"{STATE}/work/{i}/spec.json"]

        # Not-yet-existing paths are fine in the list; the paths the checks rely on are asserted below.
        TARGET_GROUPS = [
            ("audio-A", audio_paths(a)), ("audio-B", audio_paths(b)),
            ("jobs-A", job_paths(a)), ("jobs-B", job_paths(b)), ("jobs-A2", job_paths(a2)),
            ("work-A", work_paths(a)), ("work-B", work_paths(b)), ("work-A2", work_paths(a2)),
            ("outbox", [OUTBOX, f"{OUTBOX}/{a}.json", f"{OUTBOX}/{b}.json"]),
            ("inbox", [INBOX, f"{INBOX}/{p}.json.tmp"]),
            ("state-root", [STATE, f"{STATE}/state.lock", f"{STATE}/worker.lock", f"{STATE}/queue"]),
            ("gpu-lock", ["/run/stt", GPU_LOCK, "/var/lib/stt-models"]),
            ("devices", ["/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm"]),
        ]
        GROUP_NAMES = [name for name, _ in TARGET_GROUPS] + list(FIXED_GROUPS)
        document = {"groups": [{"name": name, "paths": paths} for name, paths in TARGET_GROUPS]}
        server.succeed(
            "mkdir -p -m 0755 /run/vm-double"
            f" && echo {base64.b64encode(json.dumps(document).encode()).decode()} | base64 -d > {TARGETS_FILE}"
            f" && chmod 0644 {TARGETS_FILE}"
        )

        # A request file as a caller leaves it while writing (`<name>.tmp`, then a rename): intake ignores it.
        server.succeed(as_user("caller", f"umask 0027; echo pending > {INBOX}/{p}.json.tmp"))

        def plant_work(i):
            """A stand-in for another job's work folder with its decoded audio and its spec."""
            server.succeed(as_user("stt", f"umask 077 && mkdir {STATE}/work/{i} && echo decoy > {STATE}/work/{i}/audio.wav && echo decoy > {STATE}/work/{i}/spec.json"))

        def start_read(decoys, lock_unit, existing):
            """Starts the worker behind the operator's lock, plants stand-in work folders and lets the read begin."""
            server.succeed("systemctl start --no-block stt-worker.service")
            server.wait_until_succeeds("systemctl show -P ActiveState stt-worker.service | grep -Ex 'activating|active'", timeout=30)
            # The worker clears the work folders of a dead worker first; once it sleeps between attempts
            # to take the GPU lock (clock_nanosleep, 230 on x86-64) that is behind it.
            server.wait_until_succeeds(
                "test \"$(cut -d' ' -f1 /proc/$(systemctl show -P MainPID stt-worker.service)/syscall)\" = 230", timeout=30
            )
            for decoy in decoys:
                plant_work(decoy)
            for path in existing + [f"{STATE}/work/{decoy}" for decoy in decoys]:
                server.succeed(f"test -e {path}")
            server.succeed(f"systemctl stop {lock_unit}.service")

        def finish_read(request_id):
            """Waits for the decoded audio of the job (the double's report) and parses it."""
            def whole_report(frames):
                try:
                    json.loads(frames.decode("utf-8"))
                except ValueError:
                    return False
                return True

            fmt, frames = wait_wav(f"{STATE}/work/{request_id}/audio.wav", whole_report)
            server.succeed(f"test -f {STATE}/work/{request_id}/spec.json")
            assert fmt == (1, 2, 16000), fmt
            return json.loads(frames.decode("utf-8"))

        def probe_report(request_id):
            record = json.loads(server.succeed(f"cat {STATE}/jobs/{request_id}/job.json"))
            packed = round(record["duration_s"] * 2**30)
            return {
                "uid": packed & 0x1FFFF,
                "own_fd_readable": bool(packed >> 17 & 1),
                "successes": packed >> 18 & 0x7F,
                "control": packed >> 25 & 1,
                "reached": [name for index, name in enumerate(GROUP_NAMES) if packed >> 26 >> index & 1],
            }

        def assert_confined(report, who):
            assert report["own_fd_readable"] is True, (who, report)
            if "groups" in report:
                assert report["control"] == 2, (who, report)
                reached = {name: count for name, count in report["groups"].items() if count}
                assert not reached, (who, reached)
            else:
                assert report["control"] == 1, (who, report)
                assert report["reached"] == [] and report["successes"] == 0, (who, report)
            assert report["uid"] != stt_uid and 61184 <= report["uid"] <= 65519, (who, report, stt_uid)

        common = [
            f"{STATE}/audio/{a}/audio.wav", f"{STATE}/audio/{b}/audio.wav", f"{STATE}/jobs/{a}/job.json",
            f"{STATE}/jobs/{b}/job.json", f"{OUTBOX}/{a}.json", f"{OUTBOX}/{b}.json", INBOX, f"{INBOX}/{p}.json.tmp", GPU_LOCK,
            f"{STATE}/state.lock", "/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm",
        ]

        # A: job A (requester one, with hints) and job B (requester two) wait behind the operator's lock.
        hold_gpu("gpu-hold-a", 900)
        submit(transcribe(a, hints=HINTS))
        wait_queued(a)
        submit(transcribe(b, requester=OTHER))
        wait_queued(b)
        start_read([b], "gpu-hold-a", common)
        # While A's decode instance is held open, a third request's length probe runs next to it.
        decode_instance = wait_reader_instance()
        decode_uid = int(prop(decode_instance, "UID"))
        submit(transcribe(c))
        wait_queued(c)
        server.succeed(f"systemctl is-active {decode_instance}")
        assert int(prop(decode_instance, "UID")) == decode_uid
        # The pattern finds the private folders of other running units, and none for the reader instance.
        server.succeed("test -n \"$(find /tmp -maxdepth 1 -name 'systemd-private-*systemd-logind*')\"")
        server.succeed(f"test -z \"$({PRIVATE_FOLDERS})\"")
        report_a = finish_read(a)
        server.succeed(f"rm -r {STATE}/work/{b}")
        assert_confined(report_a, "decode of A")
        assert report_a["uid"] == decode_uid, (report_a, decode_uid)
        probes = {name: probe_report(name_id) for name, name_id in (("A", a), ("B", b), ("C", c))}
        for name, report in probes.items():
            assert_confined(report, f"probe of {name}")
        assert probes["C"]["uid"] != report_a["uid"], (
            "probe and decode ran at the same time under one UID", probes["C"], report_a, prop(decode_instance, "User")
        )
        assert response(delete(c))["outcome"] == "deleted"
        end_held_job(a)
        no_reader_instance()

        # B: another requester's job, read while a stand-in for A's decoded audio exists.
        hold_gpu("gpu-hold-b", 900)
        start_read([a], "gpu-hold-b", common + [f"{STATE}/audio/{a}.json"])
        report_b = finish_read(b)
        server.succeed(f"rm -r {STATE}/work/{a}")
        assert_confined(report_b, "decode of B")
        end_held_job(b)
        no_reader_instance()

        # A': a second pass over A's recording reaches that recording through the descriptor only.
        again = transcribe(a2, audio={"from_request": a})
        submit(again, source=None)
        wait_queued(a2)
        hold_gpu("gpu-hold-a2", 900)
        start_read([a, b], "gpu-hold-a2", common + [f"{STATE}/jobs/{a2}/job.json"])
        report_a2 = finish_read(a2)
        for decoy in (a, b):
            server.succeed(f"rm -r {STATE}/work/{decoy}")
        assert_confined(report_a2, "decode of A2")
        end_held_job(a2)
        no_reader_instance()
        assert not failed_stt_units(), failed_stt_units()

        release_cuda()
        server.succeed(f"rm {INBOX}/{p}.json.tmp")
        for node_name in ("nvidia0", "nvidiactl", "nvidia-uvm"):
            server.succeed(f"rm /dev/{node_name}")
        server.succeed("systemctl start stt-worker.path stt-sweep.timer")
        switch_to()

    WORKER_DETAIL = {
        "unreadable_audio": "The recording could not be read as audio.",
        "engine_error": "The server could not start the audio reader.",
    }

    for double, intake_reason, worker_reason, wait in (
        ("reader-no-verdict", "unreadable_audio", "unreadable_audio", 90),
        ("reader-hang", "unreadable_audio", "unreadable_audio", 330),
        ("reader-memory", "unreadable_audio", "unreadable_audio", 90),
        ("reader-not-started", None, "engine_error", 90),
    ):
        with subtest(f"{double}: only the affected job fails, the next job completes and no instance is left (B-P2-5)"):
            lock = f"gpu-hold-{double}"
            if double == "reader-memory":
                # The test VM panics on any out-of-memory kill; the unit's memory cap killing the
                # reader is exactly what this subtest expects.
                panic_on_oom = server.succeed("sysctl -n vm.panic_on_oom").strip()
                server.succeed("sysctl -w vm.panic_on_oom=0")
            # The job to be decoded by the failing reader is accepted by the healthy one first.
            hold_gpu(lock, 900)
            victim = transcribe()
            submit(victim)
            wait_queued(victim["request_id"])
            switch_to(double)
            server.succeed(f"systemctl stop {lock}.service")

            def check_late_request():
                """The intake's probe meets the failing reader."""
                late = transcribe()
                submit(late)
                if intake_reason is None:
                    inbox_empty()
                    server.fail(f"test -e {OUTBOX}/{late['request_id']}.json")
                    server.succeed(f"journalctl --no-pager -u stt-intake.service | grep -F '{late['request_id']} intake error'")
                else:
                    assert failed_with(intake_reason)(response(late["request_id"], timeout=90)), response(late["request_id"])
                    inbox_empty()

            def check_victim():
                """The worker's decode met the failing reader."""
                done = wait_response(victim["request_id"], failed_with(worker_reason), timeout=wait)
                assert done["failure"]["detail"] == WORKER_DETAIL[worker_reason], done

            if double == "reader-memory":
                # One memory hog at a time, so that the system-wide OOM killer cannot pick another process.
                check_victim()
                check_late_request()
            else:
                check_late_request()
                check_victim()
            no_reader_instance()
            if double == "reader-memory":
                server.wait_until_succeeds("journalctl --no-pager -o cat -u 'stt-reader@*' | grep -i 'oom'", timeout=30)
                server.succeed(f"sysctl -w vm.panic_on_oom={panic_on_oom}")
            # A request the reader could not even be asked about ends the intake run with exit 1
            # (001 FR-032a, kept by the operator decision of 2026-11-08): the one failed stt unit any double may leave.
            expected_failed = {"stt-intake.service"} if intake_reason is None else set()
            assert failed_stt_units() == expected_failed, failed_stt_units()
            server.succeed("systemctl reset-failed 'stt-*'")
            switch_to()
            following = transcribe()
            submit(following)
            wait_response(following["request_id"], failed_with("gpu_unavailable"), timeout=60)
            no_reader_instance()
            assert not failed_stt_units(), failed_stt_units()

    with subtest("a deletion, the 23:58 purge and a stopped worker end a held read at once and leave no file (B-P2-6)"):
        switch_to("reader-hold-decode")

        def held_read():
            job = transcribe(hints=HINTS)
            submit(job)
            wait_response(job["request_id"], lambda doc: doc["state"] == "running", timeout=60)
            wait_reader_instance()
            return job["request_id"]

        def no_files_of(request_id):
            """Nothing of the job is left but its record: no instance, recording, work folder, queue mark, result or hint."""
            no_reader_instance()
            server.wait_until_succeeds(
                f"test ! -e {STATE}/audio/{request_id} && test ! -e {OUTBOX}/{request_id}"
                f" && test -z \"$(ls -A {STATE}/work; ls -A {STATE}/queue)\"",
                timeout=60,
            )
            record_path = f"{STATE}/jobs/{request_id}/job.json"
            if server.execute(f"test -e {record_path}")[0] == 0:
                record = json.loads(server.succeed(f"cat {record_path}"))
                assert record["hints"] is None, record
            for word in HINTS:
                server.fail(f"grep -rF {word} {STATE}/jobs/{request_id} {OUTBOX}/{request_id}.json")

        deleted = held_read()
        started = time.monotonic()
        assert response(delete(deleted))["outcome"] == "deleted"
        no_files_of(deleted)
        assert time.monotonic() - started < 60, time.monotonic() - started
        idle("stt-worker.service")

        stopped_worker = held_read()
        stop_worker()
        no_reader_instance()
        sweep()
        assert failed_with("interrupted")(response(stopped_worker)), response(stopped_worker)
        server.succeed(f"test -z \"$(ls -A {STATE}/work)\"")

        purged = held_read()
        local = vm_now().astimezone(KST)
        set_clock(datetime(local.year, local.month, local.day, 23, 58, 10, tzinfo=KST))
        sweep()
        no_files_of(purged)
        idle("stt-worker.service")
        assert not failed_stt_units(), failed_stt_units()
        switch_to()

    with subtest("the reader units' journal holds no job ID (B-P2-7, FR-019)"):
        reader_log = server.succeed("journalctl --no-pager -o cat -u 'stt-reader@*'")
        assert reader_log.strip(), "the memory double left lines, so an empty log would mean the query is wrong"
        server.fail(f"journalctl --no-pager -o cat -u 'stt-reader@*' | grep -E '{UUID_PATTERN}'")
  '';
}
