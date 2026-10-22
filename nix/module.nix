# NixOS module `services.stt` (contracts/module.md sections 1-3, contracts/README.md section 2).
#
# A fixed system account `stt` runs four oneshot units with separated privileges:
#
#   stt-intake   path unit on inbox/*.json   no network, no GPU
#   stt-worker   path unit on queue/*        no network, the GPU devices only
#   stt-sweep    timer, every minute         no network, no GPU
#   stt-models   every boot                  network, HF_TOKEN; only with huggingFaceTokenFile
#
# Callers write requests as members of `stt-requests`; which accounts join it is the
# host's business (module.md section 4). The engine's HF_HOME, HF_HUB_OFFLINE and
# TMPDIR are set by the worker for the engine subprocess only, never in a unit.
{
  config,
  lib,
  ...
}:
let
  inherit (lib) mkOption types;

  cfg = config.services.stt;
  stt = lib.getExe cfg.package;

  stateDir = "/var/lib/stt";
  modelsDir = "/var/lib/stt-models";
  gpuLock = "/run/stt/gpu.lock";
  fetchesModels = cfg.huggingFaceTokenFile != null;

  secretPath =
    types.addCheck types.str (path: lib.hasPrefix "/" path && !lib.hasPrefix builtins.storeDir path)
    // {
      description = "absolute path outside the Nix store";
    };

  hardening = {
    Type = "oneshot";
    User = "stt";
    Group = "stt";
    UMask = "0027";
    NoNewPrivileges = true;
    ProtectSystem = "strict";
    ProtectHome = true;
    PrivateTmp = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    RestrictNamespaces = true;
    RestrictSUIDSGID = true;
    LockPersonality = true;
    CapabilityBoundingSet = "";
    PrivatePIDs = true;
    # EX_TEMPFAIL: another run of the same stage holds its lock (cli.md section 1).
    SuccessExitStatus = "75";
    # The host's swap is not encrypted, and a core dump would hold decoded audio (FR-035).
    MemorySwapMax = 0;
    LimitCORE = 0;
  };
  offline = {
    PrivateNetwork = true;
    RestrictAddressFamilies = "AF_UNIX";
  };
  state = {
    StateDirectory = "stt";
    StateDirectoryMode = "0751";
  };
  stage =
    description: serviceConfig:
    {
      inherit description;
      serviceConfig = hardening // offline // state // serviceConfig;
    };
  # A path unit starts its service once per burst of files; with the default start
  # limit (5 in 10 s) a burst of requests fails the service and then the path unit
  # itself (`unit-start-limit-hit`), which never watches again.
  pathTriggered = unit: unit // { startLimitIntervalSec = 0; };

  folder = mode: user: group: {
    d = { inherit mode user group; };
  };
in
{
  options.services.stt = {
    enable = lib.mkEnableOption "stt server transcription: its units, account and folders";

    package = mkOption {
      type = types.package;
      defaultText = lib.literalMD "the `server` package of the stt flake";
      description = "The stt server environment; `bin/stt` is started by every unit. Changed only in tests.";
    };

    huggingFaceTokenFile = mkOption {
      type = types.nullOr secretPath;
      default = null;
      example = "/run/agenix/stt-hf-token";
      description = ''
        A dedicated agenix secret holding the single line `HF_TOKEN=...`; never a shared
        file with other keys. Only the model unit reads it, as its `EnvironmentFile`.
        When null, the model unit is not created.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.stt = {
      isSystemUser = true;
      group = "stt";
    };
    users.groups.stt = { };
    users.groups.stt-requests = { };

    # The command line queues on the same lock as the worker (cli.md section 2).
    environment.variables.STT_GPU_LOCK = gpuLock;

    systemd.tmpfiles.settings."10-stt" = {
      ${stateDir} = folder "0751" "stt" "stt";
      "${stateDir}/requests" = folder "0751" "stt" "stt";
      # Group-readable files a caller creates stay readable by stt under the default ACL.
      "${stateDir}/requests/inbox" = folder "2770" "stt" "stt-requests" // {
        "a+".argument = "default:user:stt:rwx";
      };
      "${stateDir}/requests/outbox" = folder "2750" "stt" "stt-requests";
      "${stateDir}/jobs" = folder "0700" "stt" "stt";
      "${stateDir}/audio" = folder "0700" "stt" "stt";
      "${stateDir}/work" = folder "0700" "stt" "stt";
      "${stateDir}/queue" = folder "0700" "stt" "stt";
      ${modelsDir} = folder "0750" "stt" "stt";
      "/run/stt" = folder "0755" "stt" "stt";
      ${gpuLock}.f = {
        mode = "0644";
        user = "stt";
        group = "stt";
      };
    };

    systemd.services = {
      stt-intake = pathTriggered (stage "stt intake: answer and queue inbox requests" {
        ExecStart = "${stt} server intake";
        PrivateDevices = true;
        MemoryMax = "1G";
        TimeoutStartSec = "10min";
      });

      stt-worker =
        pathTriggered (stage "stt worker: run queued jobs on the GPU" {
          ExecStart = "${stt} server work --gpu-lock ${gpuLock}";
          PrivateDevices = false;
          DevicePolicy = "closed";
          DeviceAllow = [
            "/dev/nvidiactl rw"
            "/dev/nvidia0 rw"
            "/dev/nvidia-uvm rw"
            "/dev/nvidia-uvm-tools rw"
          ];
          # Removes every IPC object of the account when the unit stops, so only here:
          # elsewhere it would remove a running worker's (research R20-17).
          RemoveIPC = true;
          MemoryMax = "12G";
          # Each job has its own processing limit; waiting for the GPU lock has none (FR-016).
          TimeoutStartSec = "infinity";
          # PID 1 of its PID namespace ignores SIGTERM: the default stop killed only the engine,
          # the worker then published engine_error and started the next job. Killed at once,
          # the running job becomes `interrupted` at the next worker start or sweep.
          KillSignal = "SIGKILL";
        })
        // {
          # A deployment does not kill a running transcription; the next start uses the new unit.
          restartIfChanged = false;
        }
        // lib.optionalAttrs fetchesModels {
          after = [ "stt-models.service" ];
          wants = [ "stt-models.service" ];
        };

      stt-sweep = stage "stt sweep: scheduled deletion and expiry" {
        ExecStart = "${stt} server sweep";
        PrivateDevices = true;
        MemoryMax = "512M";
        TimeoutStartSec = "5min";
      };
    }
    // lib.optionalAttrs fetchesModels {
      # Unconditional on every boot: with the pinned list already in `.complete` it ends
      # without touching the network (research R9).
      stt-models = {
        description = "stt models: download the pinned models";
        wantedBy = [ "multi-user.target" ];
        wants = [ "network-online.target" ];
        after = [ "network-online.target" ];
        serviceConfig = hardening // {
          ExecStart = "${stt} server fetch-models --models ${modelsDir}";
          RemainAfterExit = true;
          EnvironmentFile = cfg.huggingFaceTokenFile;
          # hf_xet caches under $HF_HOME/xet by default; the unit's home (/var/empty) is read-only.
          Environment = [ "HF_XET_CACHE=${modelsDir}/xet" ];
          StateDirectory = "stt-models";
          StateDirectoryMode = "0750";
          PrivateDevices = true;
          RestrictAddressFamilies = [
            "AF_UNIX"
            "AF_INET"
            "AF_INET6"
          ];
          TimeoutStartSec = "2h";
        };
      };
    };

    systemd.paths = {
      stt-intake = {
        wantedBy = [ "paths.target" ];
        pathConfig.PathExistsGlob = "${stateDir}/requests/inbox/*.json";
      };
      stt-worker = {
        wantedBy = [ "paths.target" ];
        pathConfig.PathExistsGlob = "${stateDir}/queue/*";
      };
    };

    systemd.timers.stt-sweep = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "minutely";
        # The default 1min accuracy could delay the midnight deletion past one minute.
        AccuracySec = "1s";
        Persistent = true;
      };
    };
  };
}
