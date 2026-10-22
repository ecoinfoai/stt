# Server environment built from uv.lock wheels with uv2nix (research R1).
{ lib
, pkgs
, python
, pyproject-nix
, uv2nix
, pyproject-build-systems
}:
let
  # Only what the build reads, so doc-only edits do not rebuild `stt`.
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../uv.lock
      ../README.md
      ../src
    ];
  };

  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = src; };

  sp = python.sitePackages;
  driverLib = "/run/opengl-driver/lib"; # libcuda.so.1 comes from the host driver

  # Lets autoPatchelf resolve sonames shipped inside other wheels' site-packages/nvidia/** dirs.
  linkAgainst = deps: ignore: old: {
    buildInputs = (old.buildInputs or [ ]) ++ deps;
    preFixup = (old.preFixup or "")
      + lib.concatMapStrings (d: "addAutoPatchelfSearchPath ${d}/${sp}\n") deps;
    autoPatchelfIgnoreMissingDeps = (old.autoPatchelfIgnoreMissingDeps or [ ]) ++ ignore;
    # $ORIGIN: libcudnn.so.9 and libnvrtc dlopen siblings by bare soname; autoPatchelf drops the wheel's own $ORIGIN.
    appendRunpaths = (old.appendRunpaths or [ ]) ++ [ "$ORIGIN" driverLib ];
  };

  overrides = final: prev:
    let
      cu13 = with final; [
        nvidia-cublas nvidia-cuda-cupti nvidia-cuda-nvrtc nvidia-cuda-runtime
        nvidia-cudnn-cu13 nvidia-cufft nvidia-cufile nvidia-curand nvidia-cusolver
        nvidia-cusparse nvidia-cusparselt-cu13 nvidia-nccl-cu13 nvidia-nvjitlink
        nvidia-nvshmem-cu13 nvidia-nvtx
      ];
    in
    lib.genAttrs (lib.filter (lib.hasPrefix "nvidia-") (lib.attrNames prev))
      (n: prev.${n}.overrideAttrs (linkAgainst [ ] [ ]))
    // {
      nvidia-cusparse = prev.nvidia-cusparse.overrideAttrs (linkAgainst [ final.nvidia-nvjitlink ] [ ]);
      nvidia-cusolver = prev.nvidia-cusolver.overrideAttrs (linkAgainst
        (with final; [ nvidia-nvjitlink nvidia-cusparse nvidia-cublas ]) [ ]);
      # GPUDirect-Storage RDMA plugin; optional, unused.
      nvidia-cufile = prev.nvidia-cufile.overrideAttrs (linkAgainst [ ]
        [ "librdmacm.so.1" "libmlx5.so.1" "libibverbs.so.1" ]);
      # Multi-node transport plugins (UCX, MPI, PMIx, libfabric, IB); unused.
      nvidia-nvshmem-cu13 = prev.nvidia-nvshmem-cu13.overrideAttrs (linkAgainst [ ]
        [ "libucs.so.0" "libucp.so.0" "libpmix.so.2" "liboshmem.so.40" "libmpi.so.40"
          "libmlx5.so.1" "libfabric.so.1" ]);
      torch = prev.torch.overrideAttrs (linkAgainst cu13 [ "libcuda.so.1" ]);
      torchaudio = prev.torchaudio.overrideAttrs (linkAgainst [ final.torch final.nvidia-cuda-runtime ] [ ]);
      # torchcodec dlopens one FFmpeg major (4..8); stt decodes audio itself, so none are provided.
      torchcodec = prev.torchcodec.overrideAttrs (linkAgainst [ final.torch final.nvidia-cuda-runtime ]
        [ "libavutil.so.*" "libavcodec.so.*" "libavformat.so.*" "libavdevice.so.*"
          "libavfilter.so.*" "libswscale.so.*" "libswresample.so.*" ]);
      # CUDA 12 build: dlopens libcublas.so.12 (nvidia-cublas-cu12) and cuDNN 9 (shared with torch). No DT_NEEDED -> RUNPATH.
      ctranslate2 = prev.ctranslate2.overrideAttrs (old: {
        appendRunpaths = (old.appendRunpaths or [ ]) ++ [
          "${final.nvidia-cublas-cu12}/${sp}/nvidia/cublas/lib"
          "${final.nvidia-cudnn-cu13}/${sp}/nvidia/cudnn/lib"
          driverLib
        ];
      });
    };

  pythonSet = (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope
    (lib.composeManyExtensions [
      pyproject-build-systems.overlays.wheel
      (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
      overrides
    ]);

  # `stt` needs ffmpeg on PATH to convert audio to WAV for diarization.
  withFfmpeg = env: pkgs.symlinkJoin {
    name = "${env.name}-wrapped";
    paths = [ env ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = ''
      wrapProgram $out/bin/stt --prefix PATH : ${lib.makeBinPath [ pkgs.ffmpeg ]}
    '';
    meta.mainProgram = "stt";
  };
in
{
  server = withFfmpeg (pythonSet.mkVirtualEnv "stt-server-env" {
    stt = [ "diarize" "server" ];
  });
  # No diarization stack: for the VM test, where the engine stops at the GPU check.
  server-light = withFfmpeg (pythonSet.mkVirtualEnv "stt-server-light-env" {
    stt = [ "server" ];
  });
}
