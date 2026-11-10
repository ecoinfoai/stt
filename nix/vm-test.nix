# NixOS VM test of `services.stt` (quickstart B, steps 1-11 and B-P2-1..8; contracts/module.md).
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
    # One sine wave per format of the reader's list (reader.AUDIO_FORMATS); nothing but tones is made.
    tone() { ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=2" -ac 1 "$@"; }
    tone -ar 44100 -c:a aac -f ipod $out/tone.m4a
    tone -ar 44100 -c:a libmp3lame $out/tone.mp3
    tone -ar 44100 -c:a flac $out/tone.flac
    tone -ar 44100 -c:a libvorbis -f ogg $out/tone-vorbis.ogg
    tone -ar 48000 -c:a libopus -f ogg $out/tone-opus.opus
    tone -ar 44100 -c:a aac -f adts $out/tone.aac
    tone -ar 44100 -c:a wmav2 -f asf $out/tone.wma

    # Added by 003 (contracts/module.md section 4): recorder codecs of the reader's list, still nothing but tones.
    tone -ar 16000 -c:a adpcm_ima_wav $out/tone-adpcm-ima.wav
    tone -ar 16000 -c:a adpcm_ms $out/tone-adpcm-ms.wav
    tone -ar 44100 -c:a mp2 -f mp2 $out/tone-mp2.mp3
    tone -ar 48000 -c:a ac3 -f mp4 $out/tone-ac3.m4a
    tone -ar 48000 -c:a eac3 -f mp4 $out/tone-eac3.m4a
    # A speech codec outside the list.
    tone -ar 16000 -c:a libspeex -f ogg $out/tone-speex.ogg

    # A video track or a cover picture next to the sound: solid colours only.
    ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=2" \
      -f lavfi -i "color=c=blue:size=64x64:rate=10:duration=2" \
      -ac 1 -ar 44100 -c:a aac -c:v libx264 -pix_fmt yuv420p -shortest -f mp4 $out/tone-video.m4a
    ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=2" \
      -f lavfi -i "color=c=blue:size=64x64:rate=10:duration=2" \
      -ac 1 -ar 44100 -c:a wmav2 -c:v wmv2 -shortest -f asf $out/tone-video.wma
    ffmpeg -hide_banner -loglevel error -f lavfi -i "color=c=red:size=32x32" -frames:v 1 cover.png
    ffmpeg -hide_banner -loglevel error -i $out/tone.mp3 -i cover.png -map 0 -map 1 -c copy \
      -id3v2_version 3 -disposition:v attached_pic -f mp3 $out/tone-cover.mp3
    ffmpeg -hide_banner -loglevel error -f lavfi -i "color=c=blue:size=64x64:rate=10:duration=2" \
      -c:v libx264 -pix_fmt yuv420p -an -f mp4 $out/video-only.m4a

    # Ten-second tones, whole and cut in half: the cut ones end inside the data.
    tone10() { ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=10" -ac 1 "$@"; }
    tone10 -ar 44100 -c:a libmp3lame -f mp3 $out/tone10.mp3
    tone10 -ar 44100 -c:a libmp3lame -f mp3 whole.mp3
    tone10 -ar 44100 -c:a flac whole.flac
    tone10 -ar 44100 -c:a aac -movflags +faststart -f ipod whole.m4a
    cut_in_half() { head -c $(( $(stat -c %s "$1") / 2 )) "$1" > "$2"; }
    cut_in_half whole.mp3 $out/cut-tone.mp3
    cut_in_half whole.flac $out/cut-tone.flac
    cut_in_half whole.m4a $out/cut-faststart.m4a
    # The index comes first: cut right after the 8-byte header (size and name) of the media data box.
    mdat=$(grep -abo mdat whole.m4a | head -n 1 | cut -d: -f1)
    head -c $(( mdat + 4 )) whole.m4a > $out/nopackets-faststart.m4a
  '';

  # Watches the decoders the reader child opens (contracts/module.md section 3). Only the reader instances of
  # the test node load it (LD_PRELOAD); the production module does not. When the real avcodec_open2 succeeds
  # for a context that is not audio, the process aborts, and the real supervisor writes
  # `reader child <probe|decode> killed by signal 6`. It never looks at a recording.
  decoderWatch = pkgs.runCommandCC "stt-vm-decoder-watch" { } ''
    mkdir -p $out/lib
    cat > watch.c <<'EOF'
    #define _GNU_SOURCE
    #include <dlfcn.h>
    #include <link.h>
    #include <stdlib.h>
    #include <string.h>

    /* The first fields of AVCodecContext (libavcodec/avcodec.h): av_class, log_level_offset, codec_type. */
    struct codec_head { const void *av_class; int log_level_offset; int codec_type; };
    enum { MEDIA_TYPE_AUDIO = 1 };

    typedef int (*open_function)(void *context, const void *codec, void **options);

    /* PyAV loads libavcodec privately, so RTLD_NEXT cannot see it: find the loaded object by name instead. */
    static int find_libavcodec(struct dl_phdr_info *info, size_t size, void *data) {
        const char *base = strrchr(info->dlpi_name, '/');
        base = base ? base + 1 : info->dlpi_name;
        if (strncmp(base, "libavcodec", strlen("libavcodec")) != 0) return 0;
        *(const char **)data = info->dlpi_name;
        return 1;
    }

    int avcodec_open2(void *context, const void *codec, void **options) {
        const char *path = NULL;
        dl_iterate_phdr(find_libavcodec, &path);
        if (path == NULL) abort();
        void *library = dlopen(path, RTLD_LAZY | RTLD_NOLOAD);
        if (library == NULL) abort();
        open_function real = (open_function)dlsym(library, "avcodec_open2");
        if (real == NULL || real == avcodec_open2) abort();
        int result = real(context, codec, options);
        if (result == 0 && ((struct codec_head *)context)->codec_type != MEDIA_TYPE_AUDIO) abort();
        return result;
    }
    EOF
    $CC -shared -fPIC -o $out/lib/stt-decoder-watch.so watch.c -ldl
  '';

  # Sends each recording given on the command line through the real reader socket, as `stt` does,
  # and prints what came back: the outcomes, the length and the loudest sample of the decoded audio.
  readFormats = pkgs.writeText "stt-vm-read-formats.py" ''
    import array
    import json
    import sys

    from stt.server.reader_client import ReaderConfig, decode, probe

    config = ReaderConfig()
    report = {}
    for path in sys.argv[1:]:
        probed = probe(config, path)
        decoded = decode(config, path, job_running=lambda: True, job_limit_s=240)
        samples = array.array("h", bytes(decoded.audio))
        report[path.rsplit("/", 1)[1]] = {
            "probe": probed.outcome,
            "duration_s": probed.duration_s,
            "decode": decoded.outcome,
            "seconds": len(samples) / 16000,
            "peak": max(map(abs, samples), default=0) / 32768,
        }
    print(json.dumps(report))
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

  # The real supervisor with a child that only uses memory: it never opens the recording, so no media is involved.
  # The unit's memory cap kills the child, which is what the OOMPolicy setting is about.
  memoryChild = pkgs.writeText "stt-vm-memory-child.py" ''
    import time

    held = []
    while True:
        held.append(bytearray(b"\x01" * (16 << 20)))
        time.sleep(0.01)
  '';
  supervisedMemory = pkgs.writeText "stt-vm-supervised-memory.py" ''
    import sys

    from stt.server.reader import serve

    sys.exit(serve(child_command=(sys.executable, "${memoryChild}")))
  '';
in
# `audio` and `decoderWatch` are exposed so that they can be built alone (`nix build .#checks.x86_64-linux.server-vm.audio`).
{
  inherit audio decoderWatch;
}
// pkgs.testers.runNixOSTest {
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
    # Test node only: every real read, in the base system and in the specialisations, runs under the decoder watch.
    systemd.services."stt-reader@".environment.LD_PRELOAD = "${decoderWatch}/lib/stt-decoder-watch.so";
    virtualisation = {
      memorySize = 2048;
      cores = 2;
    };
    specialisation =
      lib.mapAttrs (_: behaviours: {
        configuration.systemd.services."stt-reader@".serviceConfig.ExecStart =
          lib.mkForce "${package}/bin/python ${readerDouble} ${behaviours}";
      }) readerDoubles
      // {
        reader-supervised-memory.configuration.systemd.services."stt-reader@".serviceConfig.ExecStart =
          lib.mkForce "${package}/bin/python ${supervisedMemory}";
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
    PYTHON = "${package}/bin/python"
    READ_FORMATS = "${readFormats}"
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
        # Every hidden path carries `-`: under the empty /run and /var the stt paths do not exist, and a missing path must not fail the unit.
        # /mnt, /srv, /opt and /media hold a host's data disks and add-on trees (operator decision 2026-11-10).
        assert set(prop(reader, "InaccessiblePaths").split()) == {
            "-/var/lib/stt", "-/var/lib/stt-models", "-/run/stt", "-/nix/var", "-/mnt", "-/srv", "-/opt", "-/media",
        }, prop(reader, "InaccessiblePaths")
        # A child killed by the memory cap must not take the supervisor with it, and its lines are warnings.
        assert prop(reader, "OOMPolicy") == "continue", prop(reader, "OOMPolicy")
        server.succeed("grep -qx 'SyslogLevel=warning' /etc/systemd/system/stt-reader@.service")
        # The unit's /dev/shm is the host's even with PrivateDevices=yes, so an empty read-only tmpfs covers it;
        # /run and /var hold the host's readable lock files and are covered the same way.
        assert set(prop(reader, "TemporaryFileSystem").split()) == {"/dev/shm:ro", "/run:ro", "/var:ro"}, prop(reader, "TemporaryFileSystem")
        calls = set(prop(reader, "SystemCallFilter").split())
        assert calls and not prop(reader, "SystemCallFilter").startswith("~"), prop(reader, "SystemCallFilter")
        assert "read" in calls, "the filter allows the ordinary calls"
        for call in ("process_vm_readv", "process_vm_writev", "io_uring_setup", "io_uring_enter", "io_uring_register", "userfaultfd", "setuid", "chroot", "add_key", "request_key", "keyctl"):
            assert call not in calls, call
        assert server.succeed("grep '^ExecStart=' /etc/systemd/system/stt-reader@.service").strip().endswith("/bin/stt server read")
        # A deployment must not restart live reads, nor stop the socket on their account.
        server.succeed("grep -qx 'X-RestartIfChanged=false' /etc/systemd/system/stt-reader@.service")

        for unit, command in (
            ("stt-intake.service", "server intake --reader-socket /run/stt-reader.sock"),
            ("stt-worker.service", "server work --gpu-lock /run/stt/gpu.lock --reader-socket /run/stt-reader.sock"),
        ):
            # Wants, not Requires: stopping the socket must not stop a running stage (contracts/module.md section 3).
            assert socket_unit in prop(unit, "Wants").split(), (unit, prop(unit, "Wants"))
            assert socket_unit not in prop(unit, "Requires").split(), (unit, prop(unit, "Requires"))
            assert socket_unit in prop(unit, "After").split(), (unit, prop(unit, "After"))
            assert server.succeed(f"grep '^ExecStart=' /etc/systemd/system/{unit}").strip().endswith(f"/bin/stt {command}"), unit

    with subtest("every format of the reader's list is read through the real reader without a blocked call (B-P2-8, US2-8, SC-007)"):
        formats = (
            "two-seconds.wav", "tone.m4a", "tone.mp3", "tone.flac", "tone-vorbis.ogg", "tone-opus.opus", "tone.aac", "tone.wma",
        )
        listing = " ".join(f"{AUDIO}/{name}" for name in formats)
        report = json.loads(server.succeed(as_user("stt", f"{PYTHON} {READ_FORMATS} {listing}")).strip().splitlines()[-1])
        assert set(report) == set(formats), report
        for name, result in report.items():
            assert result["probe"] == "ok" and result["decode"] == "ok", (name, result)
            assert abs(result["duration_s"] - 2.0) < 0.35, (name, result)
            assert abs(result["seconds"] - 2.0) < 0.35, (name, result)
            # A sine of amplitude 1/8 (lavfi's default); a silent or garbled decode would be far off.
            assert 0.08 < result["peak"] < 0.2, (name, result)
        # The supervisor writes its line after the verdict and journald reads the streams asynchronously: wait for
        # the last instance to end and flush the journal before looking for a line that must not be there.
        server.wait_until_succeeds("test -z \"$(systemctl list-units --all --plain --no-legend 'stt-reader@*')\"", timeout=60)
        server.succeed("journalctl --sync")
        # A call the filter denies kills the decode child with SIGSYS; the kernel logs it and the supervisor says so.
        assert "type=1326" not in server.succeed("journalctl -k --no-pager -o cat")
        assert "reader child" not in server.succeed("journalctl -u 'stt-reader@*' --no-pager -o cat")

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
    FIXED_GROUPS = ("network", "own-fd-write", "scratch-write", "other-processes", "keyring")
    TARGETS_FILE = "/etc/vm-double/targets.json"
    # World-readable lock files of the host, which a reader must not be able to open or lock (adversary H1).
    HOST_LOCK = "/run/nixos/switch-to-configuration.lock"
    NIX_DB = "/nix/var/nix/db"
    NIX_DB_LOCK = f"{NIX_DB}/vm-double-lock"
    # A readable file of the host under /var that belongs to no stt path (US2-10).
    VAR_HOST_FILE = "/var/lib/vm-double-host-file"
    # Readable files on the data disks and in the add-on trees of a host, which a reader must not reach either (reaudit RM2).
    DATA_DIRS = ("/mnt", "/srv", "/opt", "/media")
    DATA_FILES = [f"{directory}/vm-double-data" for directory in DATA_DIRS]

    # The journal check of docs/server-security.md section 3, run after a cursor so that only what one subtest
    # caused is looked at. The cursor comes from the last entry, which exists by then.
    OPERATOR_CHECK = "journalctl -u 'stt-reader@*' -g 'reader child|oom|runtime time limit|main process exited'"

    def journal_cursor():
        return server.succeed("journalctl -n1 --show-cursor --no-pager -o cat").split("-- cursor: ")[1].strip()

    def operator_output(cursor, ready):
        """Runs the documented check on the entries after `cursor`; waits up to 30 s for `ready(output)`."""
        deadline = time.monotonic() + 30
        while True:
            output = server.execute(f"{OPERATOR_CHECK} --after-cursor={shlex.quote(cursor)} --no-pager -o cat")[1]
            if ready(output) or time.monotonic() > deadline:
                return output
            time.sleep(1)

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

    # ---- 003: decoder list, truncation line, error causes, manual-run line (quickstart B-003-1 .. B-003-4) ----------
    # Every check here is collected and asserted once after B-003-4, so that a single run shows all failures of this part.
    # The strings are the exact lines of the contracts: the code that writes them comes with later units.
    # specs/003-reader-hardening/contracts/operator-log.md section 1 (the worker's truncation line).
    TRUNCATION_LINE = "decoded audio is shorter than the probed length"
    # contracts/operator-log.md section 2 (the worker's cause names) and 001's fixed response sentence for the read stage.
    READER_DETAIL = "The server could not start the audio reader."
    # contracts/reader.md section 1 (reader.MANUAL_RUN_LINE).
    MANUAL_RUN_LINE = "stt server read only serves a connection that systemd passes from stt-reader.socket on standard input"
    # docs/server-security.md section 3 (the documented truncation check).
    TRUNCATION_CHECK = "journalctl -u stt-worker -g ' running$|shorter than the probed length'"
    READER_JOURNAL = "journalctl -u 'stt-reader@*' --no-pager -o cat"
    # contracts/module.md section 4: the recorder codecs added to the list, and the recordings with a video track or a cover picture.
    RECORDER_CODECS = ("tone-adpcm-ima.wav", "tone-adpcm-ms.wav", "tone-mp2.mp3", "tone-ac3.m4a", "tone-eac3.m4a")
    WITH_VIDEO = ("tone-video.m4a", "tone-video.wma", "tone-cover.mp3")
    REFUSED_WITHOUT_OPENING = ("video-only.m4a", "tone-speex.ogg")
    B003_FAILURES = []

    def b003(label, check):
        """Runs one check of this part; a failure is kept and printed, and the part goes on."""
        try:
            check()
        except Exception as error:
            message = f"{label}: {type(error).__name__}: {error}"
            print(f"B-003 FAILED {message}")
            B003_FAILURES.append(message)

    def read_report(names):
        """Reads the recordings through the real reader as `stt` does; a hung read ends at 300 s."""
        listing = " ".join(f"{AUDIO}/{name}" for name in names)
        output = server.succeed("timeout 300 " + as_user("stt", f"{PYTHON} {READ_FORMATS} {listing}"))
        return json.loads(output.strip().splitlines()[-1])

    def reader_lines_after(cursor):
        """The reader units' lines after `cursor`, once the last instance has ended and the journal is flushed."""
        server.wait_until_succeeds(f"test -z \"$({READER_INSTANCES})\"", timeout=60)
        server.succeed("journalctl --sync")
        return server.succeed(f"{READER_JOURNAL} --after-cursor={shlex.quote(cursor)}").splitlines()

    def kernel_log_after(cursor):
        return server.succeed(f"journalctl -k --no-pager -o cat --after-cursor={shlex.quote(cursor)}")

    def tone_problems(report, names):
        """What is wrong with the report of recordings that must read as the 2 s tone (as in B-P2-8)."""
        problems = [] if set(report) == set(names) else [("names", sorted(report))]
        for name, result in report.items():
            if not (result["probe"] == "ok" and result["decode"] == "ok"):
                problems.append((name, result))
            elif not (abs(result["duration_s"] - 2.0) < 0.35 and abs(result["seconds"] - 2.0) < 0.35 and 0.08 < result["peak"] < 0.2):
                problems.append((name, result))
        return problems

    # The watch library must really be loaded in a reader process of this run, or B-003-1 would pass without watching.
    def check_watch_loaded():
        holder = "import socket, time; s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET); s.connect('/run/stt-reader.sock'); time.sleep(60)"
        server.execute(as_user("stt", f"nohup {PYTHON} -c {shlex.quote(holder)} >/dev/null 2>&1 &"))
        try:
            instance = wait_reader_instance()
            pid = server.succeed(f"systemctl show -P MainPID {instance}").strip()
            assert pid != "0", instance
            server.succeed(f"grep -q stt-decoder-watch.so /proc/{pid}/maps")
        finally:
            server.execute("pkill -u stt -f '[S]OCK_SEQPACKET'")
            no_reader_instance()

    b003("B-003-1(0) the decoder watch library is loaded in a running reader", check_watch_loaded)

    # B-003-1: the real reader unit, under the decoder watch, opens audio decoders only.
    def check_recorder_codecs():
        cursor = journal_cursor()
        problems = tone_problems(read_report(RECORDER_CODECS), RECORDER_CODECS)
        lines = reader_lines_after(cursor)
        assert not problems, problems
        assert "type=1326" not in kernel_log_after(cursor)
        assert not [line for line in lines if "reader child" in line], lines

    b003("B-003-1(1) recorder codecs read ok under the watch", check_recorder_codecs)

    def check_video_and_cover():
        cursor = journal_cursor()
        problems = tone_problems(read_report(WITH_VIDEO), WITH_VIDEO)
        lines = reader_lines_after(cursor)
        print("B-003-1(2) reader lines after the cursor:", lines)
        assert not problems, problems
        assert "type=1326" not in kernel_log_after(cursor)
        assert not [line for line in lines if "reader child" in line], lines

    b003("B-003-1(2) video track and cover picture: read ok, no decoder opened", check_video_and_cover)

    def check_refused_without_opening():
        cursor = journal_cursor()
        report = read_report(REFUSED_WITHOUT_OPENING)
        lines = reader_lines_after(cursor)
        print("B-003-1(3) reader lines after the cursor:", lines)
        problems = [(name, result) for name, result in report.items() if result["probe"] != "failed" or result["decode"] != "failed"]
        assert set(report) == set(REFUSED_WITHOUT_OPENING) and not problems, (report, problems)
        for mode in ("probe", "decode"):
            exited = [line for line in lines if line == f"reader child {mode} exited with status 1"]
            assert len(exited) == len(REFUSED_WITHOUT_OPENING), (mode, lines)
        assert not [line for line in lines if "killed by signal" in line], lines

    b003("B-003-1(3) a video-only and a Speex recording fail without opening a decoder", check_refused_without_opening)

    # B-003-2: the truncation line of the worker. The jobs end gpu_unavailable (no GPU); the line comes before the GPU stage.
    TRUNCATED = ("cut-tone.mp3", "cut-tone.flac", "cut-faststart.m4a", "nopackets-faststart.m4a")
    WHOLE = "tone10.mp3"
    truncation_jobs = {}

    def check_truncation_batch():
        server.succeed("systemctl stop stt-intake.path")
        hold_gpu("gpu-hold-b003-2", 900)
        for name in (*TRUNCATED, WHOLE):
            extension = name.rsplit(".", 1)[1]
            truncation_jobs[name] = submit(transcribe(extension=extension), source=f"{AUDIO}/{name}", extension=extension)
        # One intake run takes the whole batch.
        server.succeed("systemctl start stt-intake.service")
        assert prop("stt-intake.service", "Result") == "success"
        states = {name: response(request_id, timeout=30)["state"] for name, request_id in truncation_jobs.items()}
        assert set(states.values()) == {"queued"}, states

    def finish_truncation_batch():
        server.execute("systemctl start stt-intake.path")
        cursor = journal_cursor()
        server.execute("systemctl stop gpu-hold-b003-2.service")
        for request_id in truncation_jobs.values():
            wait_response(request_id, failed_with("gpu_unavailable"), timeout=120)
        server.wait_until_succeeds(
            f"journalctl -u stt-worker --no-pager -o cat --after-cursor={shlex.quote(cursor)} | grep -F '{truncation_jobs[WHOLE]} failed'",
            timeout=60,
        )
        server.succeed("journalctl --sync")
        return server.succeed(f"{TRUNCATION_CHECK} --no-pager -o cat --after-cursor={shlex.quote(cursor)}").splitlines()

    b003("B-003-2 five recordings in one batch are queued", check_truncation_batch)
    truncation_lines = []
    b003("B-003-2 the jobs run to the GPU check", lambda: truncation_lines.extend(finish_truncation_batch()))
    print("B-003-2 truncation check output:", truncation_lines)

    def check_truncation_line(name):
        position = truncation_lines.index(f"{truncation_jobs[name]} running")
        assert truncation_lines[position + 1 : position + 2] == [TRUNCATION_LINE], truncation_lines

    for name in TRUNCATED:
        b003(f"B-003-2 {name}: the truncation line follows its running line", lambda name=name: check_truncation_line(name))

    def check_whole_has_no_line():
        position = truncation_lines.index(f"{truncation_jobs[WHOLE]} running")
        assert truncation_lines[position + 1 : position + 2] != [TRUNCATION_LINE], truncation_lines

    b003(f"B-003-2 {WHOLE}: no truncation line", check_whole_has_no_line)
    def check_four_lines():
        assert truncation_lines.count(TRUNCATION_LINE) == 4, truncation_lines

    b003("B-003-2 exactly four truncation lines", check_four_lines)

    # B-003-3: the cause that ends a `engine_error` line.
    def check_reader_unavailable():
        lock = "gpu-hold-b003-3a"
        try:
            hold_gpu(lock, 900)
            victim = transcribe()
            submit(victim)
            wait_queued(victim["request_id"])
            switch_to("reader-not-started")
            cursor = journal_cursor()
            server.succeed(f"systemctl stop {lock}.service")
            done = wait_response(victim["request_id"], failed_with("engine_error"), timeout=90)
            assert done["failure"]["detail"] == READER_DETAIL, done
            server.succeed("journalctl --sync")
            lines = server.succeed(
                f"journalctl -u stt-worker -g 'failed engine_error' --no-pager -o cat --after-cursor={shlex.quote(cursor)}"
            ).splitlines()
            assert f"{victim['request_id']} failed engine_error reader_unavailable" in lines, lines
        finally:
            server.execute(f"systemctl stop {lock}.service")
            switch_to()
            server.succeed("systemctl reset-failed 'stt-*'")
            no_reader_instance()

    b003("B-003-3(1) a reader that never starts: the line ends reader_unavailable", check_reader_unavailable)

    def check_recording_unusable():
        lock = "gpu-hold-b003-3b"
        hold_gpu(lock, 900)
        broken = transcribe()
        submit(broken)
        wait_queued(broken["request_id"])
        following = transcribe()
        submit(following)
        wait_queued(following["request_id"])
        # The recording is replaced by a FIFO of the same name while the job waits: opening it for reading would block.
        recording = f"{STATE}/audio/{broken['request_id']}/audio.wav"
        server.succeed(f"rm {recording} && mkfifo -m 0600 {recording} && chown stt:stt {recording}")
        cursor = journal_cursor()
        server.succeed(f"systemctl stop {lock}.service")

        def response_check():
            done = wait_response(broken["request_id"], failed_with("engine_error"), timeout=60)
            assert done["failure"]["detail"] == READER_DETAIL, done

        def line_check():
            server.succeed("journalctl --sync")
            lines = server.succeed(
                f"journalctl -u stt-worker -g 'failed engine_error' --no-pager -o cat --after-cursor={shlex.quote(cursor)}"
            ).splitlines()
            assert f"{broken['request_id']} failed engine_error recording_unusable" in lines, lines

        b003("B-003-3(2) the unusable recording ends engine_error with the reader sentence", response_check)
        b003("B-003-3(2) the line ends recording_unusable", line_check)
        b003(
            "B-003-3(2) the job right behind it reaches the GPU check",
            lambda: wait_response(following["request_id"], failed_with("gpu_unavailable"), timeout=90),
        )

        def failed_units_check():
            assert not failed_stt_units(), failed_stt_units()

        b003("B-003-3(2) no stt unit is failed", failed_units_check)
        # Without the fix the worker is stuck opening the FIFO: end that job the way a stopped worker does, so that the rest can run.
        if response(broken["request_id"])["state"] == "running":
            server.succeed("systemctl stop stt-worker.path")
            stop_worker()
            server.succeed(f"rm -f {recording}")
            sweep()
            server.succeed("systemctl start stt-worker.path")
            b003(
                "B-003-3(2) cleanup: the job behind the stuck one runs",
                lambda: wait_response(following["request_id"], failed_with("gpu_unavailable"), timeout=120),
            )

    b003("B-003-3(2) setup and cleanup of the FIFO case", check_recording_unusable)

    # B-003-4: the manual-run line.
    def check_manual_run():
        status = server.execute("timeout 30 ${package}/bin/stt server read </dev/null >/tmp/b003-4.out 2>/tmp/b003-4.err")[0]
        stderr = server.succeed("cat /tmp/b003-4.err")
        stdout = server.succeed("cat /tmp/b003-4.out")
        print("B-003-4 status, stderr, stdout:", status, repr(stderr), repr(stdout))
        assert status == 2, status
        assert stderr == MANUAL_RUN_LINE + "\n", stderr
        assert stdout == "", stdout

    b003("B-003-4 a hand-started reader exits 2 and writes the manual-run line", check_manual_run)

    assert not B003_FAILURES, "B-003 failures:\n" + "\n".join(B003_FAILURES)

    with subtest("a 2 s job passes the read stage: audio.wav is there and spec.json names no recording (B-P2-1, B-P2-2)"):
        hang_cuda()
        passed = transcribe()
        submit(passed)
        wait_response(passed["request_id"], lambda doc: doc["state"] == "running", timeout=30)
        work = f"{STATE}/work/{passed['request_id']}"
        fmt, frames = wait_wav(f"{work}/audio.wav", lambda frames: abs(len(frames) // 2 - 2 * 16000) <= 1600)
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
        # The supervisor's fixed line reaches the journal through the real unit, with /run covered and no path in it.
        server.wait_until_succeeds(
            "journalctl -u 'stt-reader@*' --no-pager -o cat | grep -Fx 'reader child probe exited with status 1'", timeout=30
        )
        assert response(good["request_id"])["state"] == "queued"
        assert response(good["request_id"])["queue_position"] == 1
        server.succeed("systemctl start stt-intake.path")
        inbox_empty()
        assert not failed_stt_units(), failed_stt_units()
        server.succeed("systemctl stop gpu-hold-batch.service")
        wait_response(good["request_id"], failed_with("gpu_unavailable"), timeout=60)
        no_reader_instance()
        assert not failed_stt_units(), failed_stt_units()

    with subtest("a reader instance reaches nothing but its own recording, and overlapping reads cannot see each other (B-P2-4)"):
        for node_name, major, minor in (("nvidia0", 195, 0), ("nvidiactl", 195, 255), ("nvidia-uvm", 510, 0)):
            server.succeed(f"mknod -m 0666 /dev/{node_name} c {major} {minor}")
        switch_to("reader-check")
        # The test runs the queue by hand (a switch starts the path unit and the timer again, so
        # they are stopped after it): the worker starts only when asked, so that the stand-in work
        # folders planted below survive the dead-worker cleanup at its start.
        server.succeed("systemctl stop stt-worker.path stt-sweep.timer")
        hang_cuda()
        stt_uid = int(server.succeed("id -u stt"))
        # A world-writable file in the host's /dev/shm: the reader must neither see nor change it.
        SHM_MARKER = "/dev/shm/host-marker"
        server.succeed(f"echo host-data > {SHM_MARKER} && chmod 0666 {SHM_MARKER}")
        # The host's own 0644 root lock files, as they are on a real host; made here when the VM lacks them.
        server.succeed(
            f"mkdir -p -m 0755 /run/nixos /nix/var/nix/db {' '.join(DATA_DIRS)}"
            f" && touch {HOST_LOCK} {NIX_DB_LOCK} {VAR_HOST_FILE} {' '.join(DATA_FILES)}"
            f" && chmod 0644 {HOST_LOCK} {NIX_DB_LOCK} {VAR_HOST_FILE} {' '.join(DATA_FILES)}"
        )
        planted = [HOST_LOCK, NIX_DB_LOCK, VAR_HOST_FILE, *DATA_FILES]
        assert server.succeed(f"stat -c '%a %U' {' '.join(planted)}").split("\n")[: len(planted)] == ["644 root"] * len(planted)
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
            ("host-files", [SHM_MARKER, HOST_LOCK, NIX_DB, NIX_DB_LOCK, VAR_HOST_FILE, *DATA_FILES]),
        ]
        GROUP_NAMES = [name for name, _ in TARGET_GROUPS] + list(FIXED_GROUPS)
        document = {"groups": [{"name": name, "paths": paths} for name, paths in TARGET_GROUPS]}
        server.succeed(
            "mkdir -p -m 0755 /etc/vm-double"
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
            # to take the GPU lock (clock_nanosleep, 230 on x86-64) that is behind it. This relies on
            # the worker polling the held lock with time.sleep, which uses clock_nanosleep on Python 3.11+.
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
                "uid": packed & 0xFFFF,
                "own_fd_readable": bool(packed >> 16 & 1),
                "successes": packed >> 17 & 0x7,
                "control": packed >> 20 & 1,
                "visible_pids": packed >> 21 & 0x7,
                "reached": [name for index, name in enumerate(GROUP_NAMES) if packed >> 24 >> index & 1],
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
            # PID 1 of the instance's own PID namespace is the double; nothing else may show, in particular
            # not the instance running next to it (the dynamic UID is shared by all instances of the template).
            assert report["visible_pids"] == 1, (who, report)

        common = [
            f"{STATE}/audio/{a}/audio.wav", f"{STATE}/audio/{b}/audio.wav", f"{STATE}/jobs/{a}/job.json",
            f"{STATE}/jobs/{b}/job.json", f"{OUTBOX}/{a}.json", f"{OUTBOX}/{b}.json", INBOX, f"{INBOX}/{p}.json.tmp", GPU_LOCK,
            f"{STATE}/state.lock", "/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm", SHM_MARKER,
            HOST_LOCK, NIX_DB_LOCK, VAR_HOST_FILE, *DATA_FILES,
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
        # Assumes systemd-logind runs with PrivateTmp on this systemd, so that its folder is there to find.
        server.succeed("test -n \"$(find /tmp -maxdepth 1 -name 'systemd-private-*systemd-logind*')\"")
        server.succeed(f"test -z \"$({PRIVATE_FOLDERS})\"")
        report_a = finish_read(a)
        server.succeed(f"rm -r {STATE}/work/{b}")
        assert_confined(report_a, "decode of A")
        assert report_a["uid"] == decode_uid, (report_a, decode_uid)
        # The probe of C ran next to A's held decode instance, so it is the side that observes the overlap.
        probes = {name: probe_report(name_id) for name, name_id in (("A", a), ("B", b), ("C", c))}
        for name, report in probes.items():
            assert_confined(report, f"probe of {name}")
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
        server.succeed(f"rm {INBOX}/{p}.json.tmp {SHM_MARKER} {NIX_DB_LOCK} {VAR_HOST_FILE} {' '.join(DATA_FILES)}")
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
            cursor = journal_cursor()
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
                # The supervisor is the double itself here, so it is the one the cap kills: the supervisor writes no
                # line, and the operator sees systemd's own lines for the instance.
                output = operator_output(cursor, lambda text: "OOM killer" in text and "Main process exited" in text)
                assert "The kernel OOM killer killed some processes in this unit." in output, output
                assert "Main process exited, code=killed, status=9/KILL" in output, output
                assert "reader child" not in output, output
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

    with subtest("a probe child killed by the memory cap leaves the real supervisor alive to answer and to write its line (OOMPolicy)"):
        # The test VM panics on any out-of-memory kill; the unit's memory cap killing the child is what this expects.
        panic_on_oom = server.succeed("sysctl -n vm.panic_on_oom").strip()
        server.succeed("sysctl -w vm.panic_on_oom=0")
        cursor = journal_cursor()
        switch_to("reader-supervised-memory")
        victim = transcribe()
        submit(victim)
        assert failed_with("unreadable_audio")(response(victim["request_id"], timeout=90)), response(victim["request_id"])
        inbox_empty()
        line = "reader child probe killed by signal 9"
        server.wait_until_succeeds(f"journalctl -u 'stt-reader@*' --no-pager -o cat | grep -Fx '{line}'", timeout=30)
        # The line is a warning, so that `journalctl -p warning` shows it (reaudit RL5).
        server.succeed(f"journalctl -u 'stt-reader@*' -p warning --no-pager -o cat | grep -Fx '{line}'")
        # The documented check shows the supervisor's line and systemd's own line for the instance, and the supervisor survived.
        output = operator_output(cursor, lambda text: "OOM killer" in text and line in text)
        assert line in output.splitlines(), output
        assert "The kernel OOM killer killed some processes in this unit." in output, output
        assert "Main process exited" not in output, output
        no_reader_instance()
        assert not failed_stt_units(), failed_stt_units()
        server.succeed(f"sysctl -w vm.panic_on_oom={panic_on_oom}")
        switch_to()

    with subtest("a deployment that changes the reader template leaves a held read, the socket and the stages alone (module section 3)"):
        switch_to("reader-hold-decode")
        held = transcribe()
        submit(held)
        wait_response(held["request_id"], lambda doc: doc["state"] == "running", timeout=60)
        instance = wait_reader_instance()
        worker_pid = prop("stt-worker.service", "MainPID")
        assert worker_pid != "0", worker_pid
        socket_since = prop("stt-reader.socket", "ActiveEnterTimestampMonotonic")
        # The base configuration differs from the specialisation in the template's ExecStart.
        switch_to()
        server.succeed(f"systemctl is-active {instance}")
        server.succeed("systemctl is-active stt-reader.socket")
        assert prop("stt-reader.socket", "ActiveEnterTimestampMonotonic") == socket_since
        assert prop("stt-worker.service", "MainPID") == worker_pid, (prop("stt-worker.service", "MainPID"), worker_pid)
        assert prop("stt-worker.service", "ActiveState") in ("activating", "active"), prop("stt-worker.service", "ActiveState")
        # A held probe (intake active during the switch) is not covered here.
        time.sleep(5)
        assert response(held["request_id"])["state"] == "running", response(held["request_id"])
        server.succeed(f"systemctl is-active {instance}")
        assert response(delete(held["request_id"]))["outcome"] == "deleted"
        no_reader_instance()
        idle("stt-worker.service")
        # The next read runs under the new template: the real reader, so the job reaches the GPU check.
        after = transcribe()
        submit(after)
        wait_response(after["request_id"], failed_with("gpu_unavailable"), timeout=60)
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

        def no_files_of(request_id, response_state):
            """Nothing of the job is left but what the end state keeps.

            No instance, recording, work folder, queue mark or result folder. The job record and the
            response are expected to be there with state `response_state` and no hint when it is a state
            ("deleted"); when it is None the record has expired with the purge and both must be gone.
            """
            no_reader_instance()
            server.wait_until_succeeds(
                f"test ! -e {STATE}/audio/{request_id} && test ! -e {OUTBOX}/{request_id}"
                f" && test -z \"$(ls -A {STATE}/work; ls -A {STATE}/queue)\"",
                timeout=60,
            )
            kept = [f"{STATE}/jobs/{request_id}/job.json", f"{OUTBOX}/{request_id}.json"]
            for path in kept:
                present = server.execute(f"test -e {path}")[0] == 0
                assert present == (response_state is not None), (path, present, response_state)
                if present:
                    for word in HINTS:
                        server.fail(f"grep -qF {word} {path}")
            if response_state is not None:
                assert json.loads(server.succeed(f"cat {kept[0]}"))["hints"] is None
                assert response(request_id)["state"] == response_state

        deleted = held_read()
        started = time.monotonic()
        assert response(delete(deleted))["outcome"] == "deleted"
        no_files_of(deleted, "deleted")
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
        no_files_of(purged, None)
        idle("stt-worker.service")
        assert not failed_stt_units(), failed_stt_units()
        switch_to()

    with subtest("the reader units' journal holds no job ID (B-P2-7, FR-019)"):
        reader_log = server.succeed("journalctl --no-pager -o cat -u 'stt-reader@*'")
        assert reader_log.strip(), "the memory double left lines, so an empty log would mean the query is wrong"
        server.fail(f"journalctl --no-pager -o cat -u 'stt-reader@*' | grep -E '{UUID_PATTERN}'")
        # Every line of the supervisor is a warning (reaudit RL5): the usual one of an unreadable upload is among them.
        server.succeed("journalctl --no-pager -o cat -p warning -u 'stt-reader@*' | grep -Fx 'reader child probe exited with status 1'")
  '';
}
