# Research: 교내 서버 전사 (Phase 0)

- 작성: 2026-10-22 · 입력: [spec.md](spec.md), `idea/server-transcription/tech-notes.md`, 이 저장소·geo-scout·bhu-llm·nixos-config 조사,
  이 호스트(miniecoinfo) 실측. 실측은 버리는 코드로 했고 저장소 밖(scratchpad)에 두었다.
- 형식: 결정 · 근거 · 검토한 대안. 🟢 = 이 세션에서 확인한 사실, `[VERIFY]` = 구현·배포 때 확인할 것(호스트 시험 C 에 들어 있다).

## R1. 서버 패키지 — uv.lock 의 휠을 Nix 로 묶는다 (시험 통과, 2026-10-22)

- **결정**: uv2nix(pyproject.nix 계열)로 `pyproject.toml`·`uv.lock` 에서 서버 환경(`stt` + `diarize` + `server` 묶음)을 만든다. 휠만 쓰고
  (`sourcePreference = "wheel"`), CUDA 휠에는 라이브러리 찾는 경로만 고치는 덮어쓰기를 둔다(아래 부록). stt 는 자기 nixpkgs 를 고정한다.
- **근거** 🟢 (버리는 코드로 시험, scratchpad `uv2nix-spike/`):
  - 휠 119개 + `stt` 자체(setuptools)로 빌드됐다. 소스 빌드가 필요한 남의 패키지는 없었다. `av`·`onnxruntime` 의 두 판도 python 3.12 에 맞게 풀렸다.
  - 빈 환경(`env -i`)에서 torch 2.13.0+cu130 이 RTX 2070 을 보고(`cuda.is_available()` True, cuDNN 92000, GPU 합성곱 실행),
    `ctranslate2.get_cuda_device_count()` = 1, pyannote.audio 4.0.7·faster-whisper 1.2.1·`stt.cli` 가 읽힌다.
  - **망을 막고**(`unshare -rn`) `HF_HUB_OFFLINE=1`·가짜 토큰으로 60초 화자 구분 large-v3 전사가 종료 0, 17.4초. 전사록에 화자 표시가 있었다.
  - **`LD_LIBRARY_PATH` 가 전혀 필요 없다** — 개발 셸이 넣던 cuBLAS·cuDNN·드라이버 경로를 휠의 RUNPATH 로 옮겼다. 실행에 필요한 환경은
    `PATH` 의 ffmpeg(화자 구분용 WAV 변환), `HF_HOME`, `HF_HUB_OFFLINE` 뿐이다.
  - 첫 빌드 약 9분(받기 포함), 받은 휠 3.39GiB, 닫힘(closure) 6.6GiB·저장 경로 144개(uv `.venv` 는 6.3GiB). 다시 빌드는 할 일이 없다.
  - 고정한 입력: nixpkgs `148bab9c`(stt flake 와 같음), pyproject.nix `ddc400a6`, uv2nix `7aba73a3`, build-system-pkgs `a17819c8`.
- **주의**:
  - 드라이버의 `libcuda.so.1` 을 `/run/opengl-driver/lib` 에서 찾도록 박는다 → NVIDIA 드라이버가 있는 NixOS 호스트를 전제한다(miniecoinfo 가 그렇다).
    VM 시험에는 드라이버가 없다 — 엔진의 GPU 확인이 `gpu_unavailable` 로 끝나는 것이 바로 그 시험의 기대값이다.
    🟢 확인함(2026-10-22, analyze C12): 시험 환경에서 `/run/opengl-driver` 를 빈 tmpfs 로 가리고(`unshare -rm`) `env -i` 로 돌리면
    `ctranslate2.get_cuda_device_count()` = 0, `torch.cuda.is_available()` = False 이고 예외가 없다. `faster_whisper.tokenizer` 도 읽힌다.
  - torchcodec 은 uv 환경과 똑같이 깨져 있다(FFmpeg 라이브러리를 주지 않음). stt 는 쓰지 않는다(`src/stt/diarize.py:623-656` 이 직접 해독).
  - `workspaceRoot = ./.` 이면 저장소의 어떤 파일을 고쳐도 `stt` 가 다시 빌드된다(약 8초, 휠은 그대로). 구현에서는 `stt` 원본을
    `pyproject.toml`·`uv.lock`·`README.md`·`src/` 로만 좁힌다(문서만 고친 배포가 서버 단위를 다시 띄우지 않게).
- **확인 못 한 것**: ctranslate2 가 Nix 저장소의 `libcublas.so.12` 를 읽은 것을 따로 보지 않았다(빈 `LD_LIBRARY_PATH` 로 cuda 실행이
  종료 0 이었으므로 그렇게 본다). 전사 내용을 uv 환경 결과와 비교하지 않았다. 드라이버 없는 호스트·다른 기계의 재현성은 보지 않았다.
  → 호스트 시험 C 가 실제 전사로 확인한다. 새 `server` 묶음(`jsonschema` 와 그 의존성 — `rpds-py` 는 이진 휠이다)은 아직 uv.lock 에 없어
  시험 환경에 들어가지 않았다 — `uv lock` 뒤 첫 Nix 빌드에서 확인한다(배포 단계의 첫 과제).
- **대안**: nixpkgs 의 패키지로 묶기(`cudaSupport` 면 torch 소스 빌드 포함 229개 직접 빌드, `torch-bin` 은 CUDA 판 충돌 — tech-notes §6.1, 기각),
  `uv sync --frozen` + systemd(선언적이지 않음 — 대비책이었으나 이제 필요 없다).

### R1 부록 — 시험을 통과한 덮어쓰기 (버리는 코드의 사본, `nix/package.nix` 의 출발점)

overlay 순서: `pyproject-build-systems.overlays.wheel` → `workspace.mkPyprojectOverlay { sourcePreference = "wheel"; }` → 아래.

```nix
{ lib, python }:
final: prev:
let
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
}
```

## R2. 서비스 단위와 격리 — geo-scout 의 모양 + GPU·스왑·덤프

- **결정**: `oneshot` 단위 + path 단위(받기·워커), 1분 타이머(청소), 부팅 때 한 번(모델 받기). 격리 묶음은 geo-scout 의 것을 따르고
  셋을 더한다: `MemorySwapMax=0`, `LimitCORE=0`, 워커만 GPU 장치 허용(`DevicePolicy=closed` + `DeviceAllow`).
- **근거**:
  - geo-scout 가 같은 요청함을 `PathExistsGlob` + `oneshot` 으로 돌린다(`geo-scout:nix/module.nix` `systemd.paths`). `oneshot` 은
    시작 시한이 없어 긴 처리에 맞고, geo-scout 처럼 단위마다 `TimeoutStartSec` 상한을 둔다. 🟢
  - 🟢 스왑 8GB 가 평문이다(`/dev/nvme0n1p2`, bhu-llm F198 OPEN). bhu-llm fileforge 가 같은 이유로 `MemorySwapMax = 0` 을 쓴다.
    녹음을 푼 PCM(1시간 16kHz float32 ≈ 230MB)이 스왑에 내려가면 「모든 저장 위치에서 삭제」가 거짓이 된다 → FR-035 에 명시(R14).
  - 오류 덤프(core)에도 녹음이 담긴다 → `LimitCORE=0`.
  - 🟢 이 호스트의 GPU 장치: `/dev/nvidiactl`·`/dev/nvidia0`·`/dev/nvidia-uvm`·`/dev/nvidia-uvm-tools`·`/dev/nvidia-modeset`(모두 0666).
    CUDA 계산에 modeset 은 필요 없다 → 앞의 넷만 허용. `[VERIFY]` 호스트 시험 C 에서 이 넷만으로 엔진이 GPU 를 보는지.
  - 🟢 `nvidia_uvm` 은 부팅 때 올라오지 않는다(analyze C11): `/etc/modules-load.d` 에는 `nvidia`·`nvidia_drm`·`nvidia_modeset` 뿐이고,
    udev 규칙(`/etc/udev/rules.d/99-local.rules:14-15`)은 모듈이 올라온 뒤 `/dev/nvidia-uvm*` 를 만들 뿐이다. 지금 올라와 있는 것은 부팅
    (2026-09-07) 뒤 권한 있는 프로세스가 CUDA 를 처음 쓸 때 올린 것으로 본다. 워커는 모듈을 올릴 수 없다(`NoNewPrivileges`, 능력 없음) →
    호스트가 `boot.kernelModules = [ "nvidia_uvm" ]` 로 부팅 때 올린다(module.md §4). 호스트 시험 C 가 재부팅 뒤 첫 job 으로 확인한다.
  - 🟢 망을 막아도 CUDA 전사·화자 구분이 된다(2026-10-22 네트워크 네임스페이스 시험, tech-notes §5). → `PrivateNetwork=true`.
  - 🟢 systemd 261 — `PrivatePIDs` 를 쓸 수 있다(geo-scout 가 쓴다).
- **대안**: 상주 데몬 하나(→ R10에서 기각), 단위 없이 cron(→ 잠금·격리·재시작을 손으로 짜야 한다).

## R3. GPU 잠금 — `/run/stt/gpu.lock` 의 `flock`, 명령줄은 `STT_GPU_LOCK`

- **결정**: tmpfiles 가 만든 `/run/stt/gpu.lock`(0644, stt 소유)을 읽기 전용으로 열어 `flock(LOCK_EX)`. 워커는 엔진이 도는 동안,
  명령줄은 GPU 를 쓰는 동안 쥔다. 워커는 잠금을 `LOCK_NB` 로 2초마다 시도하며 그동안 job 이 지워지거나 만료됐는지 본다(기다리는 동안에도
  지우기가 먹는다). 명령줄은 `STT_GPU_LOCK` 이 있을 때만 잠근다(다른 기계·Windows 는 그대로).
  `flock` 은 기다린 순서를 보장하지 않으므로, 워커는 job 하나를 끝내고 잠금을 푼 뒤 `poll_s` 만큼 쉬고 다음 job 의 잠금을 시도한다 — 그동안
  `LOCK_EX` 로 기다리던 명령줄이 잠금을 받는다(US4-1 「서버 작업이 끝난 뒤」, R21-8).
- **근거**: 🟢 읽기 전용으로 연 파일에 건 `flock` 이 다른 프로세스를 막는다(2026-10-22 시험: 자식이 `LOCK_NB` 에서 막힘). 🟢 Python 이 연
  파일은 기본으로 자식에게 상속되지 않는다(PEP 446, 시험에서 `get_inheritable` = False) — 엔진·ffmpeg 가 잠금을 물고 남지 않는다.
  `flock` 은 inode 에 걸리므로 단위의 마운트 격리(읽기 전용 바인드) 안팎에서 같은 잠금이다. 프로세스가 죽으면 커널이 푼다.
- **대안**: systemd `Conflicts=`(명령줄은 단위가 아니다), GPU 메모리 확인 후 시작(경쟁 조건), 명령줄도 요청함으로만(운영자가 기각 — (c)).

## R4. 녹음 길이 재기 — 받기 단계에서 PyAV

- **결정**: 받기 단계가 `av.open()` 으로 컨테이너 길이를 읽는다. 길이가 비어 있으면 패킷을 해독 없이 훑어 길이를 더한다. 열 수 없거나 음성
  흐름이 없으면 `unreadable_audio`, 10,800초를 넘으면 `audio_too_long` — 둘 다 GPU 에 가기 전에 거절한다(FR-004, US5-1·2).
- **근거**: 🟢 이 저장소의 m4a 에서 컨테이너 길이 2,922.8초를 바로 읽었다. PyAV 는 faster-whisper 의 의존성으로 이미 있다.
- **대안**: `ffprobe` 하위 프로세스(경로·출력 해석이 는다), 엔진에서만 재기(3시간 넘는 녹음이 줄을 선다).

## R5. 언어 코드 — faster-whisper 의 목록

- **결정**(R21-6 로 고침): `stt.server.contract` 가 언어 코드 목록을 상수로 갖고 받기 단계는 그것만 본다. 계약 시험이 그 상수가
  `faster_whisper.tokenizer._LANGUAGE_CODES` 와 같은지 확인한다(시험 안에서만 faster-whisper 를 불러온다). 스키마는 모양(`^[a-z]{2,3}$`)만 본다.
- **근거**: 🟢 1.2.1 에 있고 100개, `ko` 포함. 이름이 밑줄로 시작하는 내부 값이지만 판을 `==1.2.1` 로 고정했고, 시험이 같음을 확인한다(판을
  올리면 시험이 먼저 알린다). 🟢 `import faster_whisper.tokenizer` 는 ctranslate2 를 거쳐 torch 를 불러온다(2026-10-22 측정: 최대 RSS 704MB,
  1.4초 — `ctranslate2/specs/model_spec.py:18`). 서버 패키지에는 torch 가 있으므로 받기에서 그것을 부르면 받기 단위(MemoryMax 1G)가 torch 를 올린다.
- **대안**: 스키마에 100개를 적기(엔진과 따로 놀 수 있다), 검사하지 않기(엔진에서 늦게 실패).

## R6. 형식 검사 — `jsonschema`, 스키마를 패키지에 싣는다

- **결정**: 새 선택 의존성 묶음 `server = ["jsonschema"]`. 스키마 세 개를 `stt/server/schemas/` 에 패키지 자료로 싣고, 받기 단계는 요청을,
  시험은 응답·결과를 같은 스키마로 검사한다. 계약 시험이 패키지 사본과 `specs/.../contracts/` 의 바이트 일치를 본다.
- **근거**: FR-023(받는 쪽이 같은 정의로 검사). 정의가 하나여야 받기 검사와 계약이 따로 놀지 않는다. 🟢 Draft 2020-12 로 세 스키마를
  검사하고 README 예시 9개 통과·틀린 예시 13개 거절을 확인했다(`cause` 를 더한 뒤 다시 돌려 10개 통과·16개 거절, analyze 보정). 🟢 `jsonschema` 는 지금 uv.lock 에 없다 → `uv lock` 으로 더한다.
- **대안**: 손으로 쓴 검사(정의가 둘), pydantic(무겁고 계약 파일이 코드에서 나온다).

## R7. 시각 — UTC 기록, 자정은 Asia/Seoul

- **결정**: 계약의 모든 시각은 UTC RFC 3339 초 단위. 「자정」은 `zoneinfo("Asia/Seoul")` 로 다음 00:00 을 계산한다. 시각은 함수 인자로
  주입한다(시험은 시계를 기다리지 않는다).
- **근거**: 🟢 nixpkgs python312 의 `zoneinfo.TZPATH` 가 Nix tzdata 를 가리키고 `Asia/Seoul` 을 읽는다. uv 환경에는 `tzdata` 패키지도 있다.
  호스트 시간대 설정과 상관없이 같은 답이 나온다.
- **대안**: 호스트 지역 시각(호스트 설정에 묶인다), 상수 +9시간(서머타임은 없지만 의도가 코드에 안 드러난다).

## R8. 명령줄 전사 코드의 재사용 — 바꾸는 곳 넷, 출력은 그대로

- **결정**: 엔진은 `load_model`·`transcribe_file`·`build_header`·`build_paragraphs`·`render_txt`·`render_diarized`·`assign_blocks`·
  `build_terms_report`·`is_oom_error` 를 그대로 쓰고, 명령줄의 `run_with_fallback`(작은 모델로 넘어가기)·`report_terms_budget`(빠진 용어를
  출력 — 로그에 힌트가 남는다)은 쓰지 않는다. 바꾸는 곳:
  1. 새 함수 `build_timed_paragraphs` 가 (시작, 끝, 본문) 문단을 내고, `build_paragraphs` 는 그 결과에서 (시작, 본문)만 뽑는다
     (`src/stt/transcribe.py:355-415`). `build_paragraphs` 의 반환 모양과 `render_txt` 는 그대로다 — 기존 시험 6개(`build_paragraphs` 4,
     `render_txt` 2, `tests/test_transcribe.py:130-196`)가 `(시작, 본문)` 모양을 쓰므로 바꾸면 SC-013 을 어긴다(analyze F3).
  2. `transcribe_file` 에 「말소리가 없어도 실패하지 않기」 선택을 더한다(지금은 빈 결과에 `RuntimeError`, `:678-682`). 기본값은 지금 동작.
  3. `build_diarized_body` 에서 화자 문단 만들기와 글로 옮기기를 가른다(엔진은 문단 목록이 필요하다). 명령줄 출력은 그대로.
  4. `load_pipeline` 은 `HF_HUB_OFFLINE` 이 켜져 있으면 `HF_TOKEN` 을 요구하지 않는다(`src/stt/diarize.py:544-552`, R9).
- **근거**: FR-040(명령줄 유지), 헌법 I(같은 일을 두 번 짜지 않는다). 바꾸는 곳마다 기존 시험이 출력 동일을 지킨다.
- **대안**: 엔진이 명령줄을 하위 프로세스로 부르고 txt 를 해석(끝 시각·실패 이유를 잃는다), 서버용 전사 코드를 따로 짜기(중복).

## R9. 모델 — 미리 받아 두고 망 없이 읽는다

- **결정**(analyze C14·C18 로 고침): `stt server fetch-models` 가 Hugging Face 캐시 배치로 `/var/lib/stt-models/hub/` 에 받는다.
  - 저장소마다 **고정 커밋으로** 받고(`revision=<커밋>`), 오프라인 해석이 읽는 `refs/main` 을 그 커밋으로 직접 쓴다 — huggingface_hub 가
    가지 이름으로 받을 때 쓰는 것과 같은 파일이다. 그래서 상류 `main` 이 움직여도 새 호스트가 고정 판을 받을 수 있다.
  - 다 받으면 `.complete` 에 고정 목록(저장소·커밋)을 쓴다. `.complete` 의 목록이 코드의 고정 목록과 같으면 망에 닿지 않고 바로 끝난다.
  - 모델 받기 단위는 부팅마다 돈다(조건 없음, `RemainAfterExit=true`). 판이 바뀐 배포에서 NixOS 가 바뀐 활성 단위를 다시 돌리므로 새 고정
    목록을 바로 받는다 `[VERIFY]`(호스트 시험 C — 처음 고정 목록을 바꾸는 배포 때). 워커 단위는 모델 받기 단위 뒤에 선다(`after`·`wants`).
    다시 돌지 않으면 운영자가 `systemctl start stt-models.service` 를 부른다(module.md §4).
  - 엔진은 시작할 때 저장소마다 `refs/main` 이 고정 커밋과 같은지 본다. 없거나 다르면 `models_unavailable` — 다른 판으로 말없이 돌지 않는다(헌법 V).
  - 엔진은 `HF_HOME=/var/lib/stt-models`, `HF_HUB_OFFLINE=1` 로 돈다.
- **고정값**(🟢 이 호스트 캐시의 `refs/main`, 2026-10-22):

  | 저장소 | 커밋 |
  |---|---|
  | `Systran/faster-whisper-large-v3` | `edaa852ec7e145841d8ffdb056a99866b5f0a478` |
  | `pyannote/speaker-diarization-3.1` | `84fd25912480287da0247647c3d2b4853cb3ee5d` |
  | `pyannote/segmentation-3.0` | `e66f3d3b9eb0873085418a7b813d3b369bf160bb` |
  | `pyannote/speaker-diarization-community-1` | `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` |
  | `pyannote/wespeaker-voxceleb-resnet34-LM` | `837717ddb9ff5507820346191109dc79c958d614` |

- **근거**: 🟢 `HF_HUB_OFFLINE=1`·가짜 토큰·망 없음으로 60초 화자 구분 전사가 끝났다(tech-notes §5). 실행 중 토큰이 쓰이지 않으므로 지금
  코드의 `HF_TOKEN` 필수 확인만 풀면 된다(R8-4). 게이트 모델은 재배포 제약이 있을 수 있어 Nix 저장소·공유 캐시에 올리지 않는다 — 모델은
  패키지 밖 상태 폴더에 둔다. 🟢 확인함(2026-10-22): huggingface_hub 는 가지·태그 이름으로 받을 때만 `refs/<이름>` 을 쓴다
  (`huggingface_hub/_snapshot_download.py:411-421` — 커밋 해시로 받으면 쓰지 않는다). 망을 막고 `HF_TOKEN` 없이
  `Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=None)` 이 캐시만으로 읽혔다. 그때 나오는 torchcodec 적재 오류 출력은
  R1 에 적은, stt 가 쓰지 않는 라이브러리의 경고다(로그에 개인정보는 없다).
- **대안**: `main` 으로 받고 커밋이 다르면 실패(처음 안 — 상류 `main` 이 움직이면 새 호스트가 고정 판을 영영 받을 수 없다, analyze C14),
  고정 커밋으로 받고 `refs/main` 을 쓰지 않기(오프라인 해석이 깨진다), `.complete` 가 있으면 단위를 건너뛰기(처음 안 — 고정 목록이 바뀌어도
  다시 받지 않는다, analyze C18), 모델을 Nix 고정 출력으로(토큰이 빌드에 들어가고 재배포 문제).

## R10. 처리 구조 — 받기 · 워커(+ 엔진 하위 프로세스) · 청소

- **결정**: 세 단위와 하나의 하위 프로세스.
  - 받기: GPU 없이 수 초 — 검사, 받아들임·거절, 지우기 요청 처리, 대기 순서 갱신.
  - 워커: job 을 하나씩 꺼내 GPU 잠금 → 엔진 하위 프로세스(`start_new_session`, 인자 목록) → 2초마다 끝남·취소 표시·시간 상한을 본다 →
    취소·시간 초과면 프로세스 묶음을 죽이고 작업 폴더를 지운다 → 판정을 상태 잠금 아래에서 다시 확인한 뒤에만 결과를 게시한다.
  - 청소: 1분마다 삭제 시각(자정·24시간·결과 만료·남은 inbox 파일)을 집행하고, 워커 생존 잠금이 비어 있으면 남은 작업 폴더와 `running` 기록을
    정리한다(`interrupted`).
- **근거**: FR-001(GPU 가 바쁜 동안에도 접수 즉시), SC-005(처리 중 지금 지우기 1분 안 — 같은 프로세스 안의 GPU 계산은 멈출 수 없어
  하위 프로세스를 죽인다), FR-032(삭제가 워커 생존에 기대지 않는다), FR-022(판정을 다시 확인한 뒤에만 게시 — 일부 결과 없음), 권한 분리(GPU·망을
  쓸 단위만 갖는다).
- **대안**: 상주 데몬 하나(GPU 계산을 멈출 수 없고, 데몬이 멈추면 삭제도 멈춘다), HTTP 서비스(열린 포트 — bhu-llm 이 이미 기각).

## R11. 지우기 요청 두 가지 — spec 보정 (R22 가 대체)

- **결정**: `scope: result`(결과 지우기)와 `scope: everything`(지금 지우기). spec FR-005·FR-033·US2-6·US2-12 를 고쳤다.
- **근거**: 운영자 결정 「가져가면 지운다 — 「지금 지우기」로」를 그대로 따르면, bhu-llm 이 1차 결과를 가져가며 녹음까지 지워 2차 전사를 할 수
  없다. 지우기 범위를 하나 더 두는 것이 가장 작은 고침이다(새 요청 종류가 아니라 칸 하나).
- **대안**: 1차의 결과를 가져가지 않고 24시간 두기(「가져가면 지운다」와 어긋남), 녹음 삭제를 「남은 job 이 없을 때」로 미루기(2차가 올지 모른다).
- **운영자 확인 대상.**

## R12. 처리 시간 상한의 하한 5분 — spec 보정

- **결정**: 작업당 처리 시간 상한 = max(녹음 길이, 5분). FR-014·US5-3·US5-9.
- **근거**: 🟢 60초 녹음이 모델 적재 포함 19초 걸렸다. 「녹음 길이만큼」만 두면 10초 녹음은 적재 중에 상한에 닿는다. 운영자의 뜻(멈춘 job 을
  끊기)은 그대로다.
- **운영자 확인 대상.**

## R13. 아직 끝나지 않은 1차를 가리키는 2차 — 받아들인다 (spec 보정)

- **결정**: 거절하지 않고 받아 FIFO 로 1차 뒤에 처리한다(spec 경계 사례 수정). 녹음의 삭제 정책은 가장 나중에 받은 요청이 정한다(data-model §5).
- **근거**: 녹음은 받아들일 때 이미 복사돼 있다. 거절은 상태 하나와 실패 이유 하나를 늘릴 뿐 막는 위험이 없다(헌법 I).

## R14. 호스트 저장 위치 — 스왑·덤프·로그·백업 (spec FR-035·FR-037)

- 🟢 루트는 LUKS 위 btrfs(`@` 하위 볼륨), 스냅샷 도구(snapper·btrbk)는 없다. 기존 백업 둘(bhu-llm·geo-scout)은 각자 자기 자료만 담는다 →
  `/var/lib/stt` 는 백업되지 않는다. 앞으로도 넣지 않는다(module.md §4).
- 🟢 스왑은 평문이다 → 모든 stt 단위 `MemorySwapMax=0`. 덤프 → `LimitCORE=0`.
- 로그: journal 은 24시간 삭제 밖이다 → 로그에는 `request_id`·상태·이유·시각만(FR-037). 명령줄 함수 가운데 힌트를 출력하는
  `report_terms_budget` 은 엔진이 쓰지 않는다(R8). VM 시험이 시험 힌트 낱말이 journal 에 없음을 확인한다.

## R15. 시험 전략 — pytest 그대로, VM 은 GPU 없이, GPU 는 호스트에서

- **결정**: 수용 시나리오는 pytest 함수(`test_us<N>_<n>_...`)로 옮긴다 — `.feature` 도구를 더하지 않는다(헌법 VII 이 둘 다 허용, 헌법 I).
  VM 시험(nixosTest)은 GPU 가 없어 모든 job 이 `gpu_unavailable` 로 끝나는 것을 이용해 단위·권한·삭제·잠금을 실제 systemd 위에서 본다.
  완료 경로(실제 전사)는 호스트 시험 C. 운영 코드에 시험용 분기는 두지 않는다 — 워커는 엔진 명령과 시계를 인자로 받는다(시험이 바꿔 낀다).
- **근거**: CI 에 GPU 가 없다. 지금 시험도 가짜 모델로 전체 경로를 돈다(`tests/test_cli_integration.py`).
- **CI**: pytest 는 `uv sync --locked --extra server` 로 돌린다. `nix flake check` 는 평가만 한다(`--no-build`) — 서버 패키지는 CUDA 휠
  수 GB 를 받고 VM 시험은 KVM 이 필요하다. VM 시험은 개발 기기(이 호스트)에서 `nix build .#checks.x86_64-linux.server-vm` 으로 돌린다.
  VM 시험용 패키지는 화자 구분 묶음(torch)을 뺀 가벼운 환경이다 — 엔진이 GPU 확인에서 끝나므로 torch 가 필요 없다(`services.stt.package` 로 바꿔 낀다).

## R16. 화자 표시 — 지금 규칙 그대로, 서버에는 목소리 등록 없음

- 화자 라벨은 pyannote 의 파일 단위 라벨을 `speaker_label` 로 `화자N` 으로 바꾼 것이라 한 전사록 안에서 같다(FR-013). 불확실 기준은 명령줄
  기본 1.5초. 엔진은 목소리 사전을 받지 않는다(받을 칸이 계약에 없다).
- 전사록 머리글과 `화자N` 은 기존 코드가 만드는 한국어 산출물이다. 이 기능은 그 글자를 새로 쓰지 않는다 — 시험은 상수 대신
  `speaker_label("SPEAKER_00")` 을 불러 비교한다(헌법 TODO(KOREAN_PRODUCT_TEXT)).

## R17. 명령줄에 이미 있는 말없는 대체 — 엔진은 막고, 명령줄은 경고한다 (명령줄 부분은 R22 가 대체)

- 🟢 `load_pipeline` 은 torch 가 CUDA 를 못 보면 말없이 CPU 로 화자 구분을 한다(`src/stt/diarize.py:562-563`). 헌법 V 위반이다.
  엔진은 화자 구분을 할 때 torch 의 CUDA 를 먼저 확인하고 없으면 `gpu_unavailable` 로 끝낸다.
- 명령줄: 이 기능이 같은 함수(R8-4)를 고치므로 그대로 두지 않는다(analyze D1). 헌법 V 가 허용하는 대로 CPU 로 계속하되 stderr 에 영어 경고
  한 줄을 낸다 — spec US4-5, FR-040. 기록만 하고 남기는 안(Complexity Tracking + 운영자 승인)은 고칠 곳이 이미 열려 있어 기각했다.
- 범위 밖에 기록만 하는 것(헌법 위반이 아닌 사용성 문제): 명령줄 `--device cuda` 의 영어 원문 오류, 작은 모델로 넘어갈 때 반영 수 안내가 두 번 나오는 것,
  Windows 콘솔의 stderr 인코딩.

## R18. 말소리 없는 녹음 — 완료, 문단 0개

- 지금 명령줄은 빈 결과를 오류로 낸다(`transcribe_file`·`render_txt`·`render_diarized`). 엔진은 R8-2 의 선택으로 빈 세그먼트를 받고, 화자 구분을
  건너뛰며, 머리글만 있는 전사록과 `paragraphs: []` 를 낸다(US5-7).

## R19. 언어 규칙과의 관계

- 새 코드(서버 묶음·잠금·시험)는 영어이고 한글이 없다. 이 기능이 새로 쓰는 메시지(경고·오류)도 영어다.
- 이 기능이 고치는 기존 함수는 docstring·주석을 영어로 바꾼다. 다만 그 함수가 지금 내는 **사용자에게 보이는 출력**(진행 안내·오류 문구 등
  한국어 문자열)은 그대로 둔다 — FR-040·SC-013(명령줄 출력과 기존 시험 그대로)이 그것을 묶고, 출력 문구를 옮기는 일은 헌법이 정한 별도 이전
  작업(TODO(KOREAN_PRODUCT_TEXT))이다(analyze 보정 중 발견, 2026-10-22). 손대지 않는 기존 한국어도 그 작업으로 남긴다(plan Complexity Tracking).

## R20. `/speckit-analyze` 보정 (2026-10-22)

분석 보고의 지적을 설계에 반영한 결정이다. 괄호는 지적 번호다. 고친 곳은 data-model·contracts·quickstart·tasks 에 있다.

1. **대기 표시의 수명**(F1): 워커 path 단위는 `queue/*` 가 있으면 깨운다. 표시를 24시간 두면 워커가 끝날 때마다 다시 깨어나 하루 내내
   재시작한다 → 표시는 job 이 `queued` 를 떠나는 즉시(시작·실패·지움) 지운다.
2. **응답의 꼴**(F2): `refusal` = 요청 파일을 쓸 수 없을 때(읽을 수 없음·일반 파일 아님·UTF-8 JSON 아님·스키마 위반)와 delete 요청이 뒤
   검사에서 실패할 때. 스키마는 맞고 뒤 검사(첨부·언어·이름 일치·길이·해독·원본)에서 실패한 transcribe 는 `failed` job 상태(`accepted_at` 없음).
   응답의 `request_id` 는 언제나 파일 이름이다(본문 값이 달라도).
3. **inbox 파일 안전**(C1): inbox 는 sticky 폴더가 아니라 커널의 `protected_symlinks` 가 걸리지 않고, 쓰는 쪽은 인터넷에 열린 OWUI 서비스
   계정이다. 받기는 요청·첨부를 `O_NOFOLLOW` 로 열고 `fstat` 으로 일반 파일·링크 수 1 을 확인한다. 요청 JSON 은 1 MiB 이하. 어기면 내용을
   읽지 않고 거절한다.
4. **detail 만들기**(C2): 🟢 jsonschema 메시지는 입력 값을 그대로 담는다 — 100자 힌트 200개의 중복 오류가 20,824자, 맨 위 `oneOf` 오류는
   21,107자이고 원인을 가린다(2026-10-22 측정). 받기는 `kind` 로 갈래(`$defs/transcribe`·`$defs/delete`)를 골라 검사하고, detail 은
   `<JSON 경로>: <규칙마다 정한 고정 문구>` 꼴로 500자 이하, 값 없음. `invalid_request` 밖의 detail 은 이유마다 고정 문장이다(PyAV 오류
   원문·내부 경로를 싣지 않는다, US5-8).
5. **본 것은 다 처리**(C4): 받기는 본 `*.json` 을 하나도 남기지 않는다(남으면 path 단위가 되풀이해 깨운다). 읽을 수 없는 요청 파일도 이름이
   UUID 면 `refusal` 로 답한다 — 말없이 지우지 않는다(헌법 V).
6. **호출하는 쪽의 파일 권한**(C5): inbox 의 기본 ACL 아래에서는 파일을 만들 때의 모드가 ACL 마스크가 된다 → `tempfile.mkstemp` 의 0600 이면
   stt 가 읽지 못한다. 계약에 「그룹이 읽을 수 있게(0640·0660)」를 적는다.
7. **결과 지우기의 답**(C6): 결과가 있었고 이미 지워졌으면 `already_deleted`, 결과가 생긴 적이 없으면 `no_result`.
8. **원본 삭제의 원인**(F4): `failure.cause`(`midnight`·`retention_limit`·`deleted_on_request`)를 더한다 — `audio_deleted` 일 때만, 반드시.
   호출하는 쪽은 이유와 원인으로 이용자 문구를 고른다(spec US2-5·US3-5·FR-021). `schema_version` 1 은 아직 내보내지 않았으므로 판을 올리지 않는다.
9. **끝 상태로 가는 길은 하나**(C8): 끝 상태로 가는 모든 길(워커 판정·시간 초과·지우기 요청·원본 삭제·죽은 워커)은 `lifecycle.finish` 하나를
   거친다 — 힌트 삭제, 대기 표시 삭제, `after_job` 녹음 삭제, 응답 갱신. 삭제 기본 동작도 `lifecycle.py` 에 두고, `sweep.py` 는 주기 점검만 한다.
10. **힌트는 반영분만 넣는다**(C9): 엔진은 `hints[:applied]` 만 엔진에 넣는다. 명령줄처럼 전부 넣고 앞부분을 인코딩해 수를 세면, 문장 부호로
    끝나는 용어 경계에서 하나가 어긋날 수 있다 — 「반영」이라 적힌 용어가 실제로는 잘리는 말없는 누락이다(헌법 V). 반영분만 넣으면 보고가
    정의상 맞고, 순서는 그대로다(FR-006).
11. **엔진 값**(C10): beam 5, 쉼 2초·800자, 화자 구분이면 단어 시각, 불확실 1.5초 — 명령줄 기본값과 같다(`vad_filter`·
    `condition_on_previous_text=False` 는 `transcribe_file` 이 정한다). 말소리가 없으면 `render_txt`·`render_diarized` 를 부르지 않고(빈 입력에
    오류) 머리글만 쓴다.
12. **자정 집행의 지연**(B1): 청소 타이머의 기본 `AccuracySec=1min` 은 최대 1분 늦게 깨울 수 있다 → `AccuracySec=1s`. spec 「직후」 = 1분 안.
13. **외부 접속 시도의 수**(E1): `PrivateNetwork` 는 시도를 감춘다 → 호스트 시험에서 `strace -f -e trace=connect` 로 `AF_INET`·`AF_INET6`
    connect 를 세어 0 을 확인한다.
14. **토큰 파일**(C13): 운영자의 `HF_TOKEN` 은 지금 여러 API 키가 든 agenix `shared-api.env` 에 있다. 그것을 넘기면 모델 받기 단위가 다른 키까지
    받는다 → `HF_TOKEN` 한 줄만 든 전용 비밀을 만든다(module.md §4).
15. **Windows**(C3): `fcntl` 은 Windows 에 없다. `gpu_lock` 은 `fcntl` 을 함수 안에서 불러오고, `transcribe`·`enroll` 은 `STT_GPU_LOCK` 이
    있을 때만 `gpu_lock` 을 부른다. 시험이 `fcntl` 없는 환경을 흉내 낸다.
16. **Python 3.10**(C15): CI 가 3.10 도 돈다 — `datetime.UTC`·`enum.StrEnum`·`typing.Self`·`tomllib` 를 쓰지 않는다(이 호스트는 3.12 라 로컬에서는
    드러나지 않는다).
17. **공유 메모리**(C19): 워커 단위에만 `RemoveIPC=true` — 엔진이 강제 종료돼도 stt 계정의 POSIX·SysV IPC 객체가 남지 않게. 이 설정은 그 계정의
    객체를 모두 지우므로, 다른 단위에 두면 그 단위가 멈출 때 돌고 있는 워커의 것까지 지운다 → 워커에만 둔다.
18. **패키지의 실패 확인 먼저**(D2): 서버 패키지는 배포되는 산출물이라 헌법 VII 의 예외(오타·포맷·문서)가 아니다 →
    `checks.x86_64-linux.server-smoke` 를 먼저 쓰고 실패를 본 뒤 패키지를 만든다.
19. **받기가 멈췄을 때**(C17): 받기가 멈추면 1시간 지난 inbox 파일을 청소가 지운다 → 계약 §7 에 「5분 안에 응답이 없으면 실패로 본다」를 적는다.
20. **고친 문서의 시험**(C16): 기존 시험 파일에 더하는 새 시험(`tests/test_transcribe.py`·`tests/test_diarize.py`)도 한글 없음 확인에 넣는다.

## R21. 두 번째 `/speckit-analyze` 보정 (2026-10-22)

독립 검토자의 지적 39건을 문서·코드와 대조해 38건을 반영했다(1건은 사실과 달라 뺐다 — R8-1 의 「6개」는 `(시작, 본문)` 모양을 쓰는 시험 수가
맞다). 괄호는 분석 보고의 번호다.

1. **24시간을 넘기지 않는 여유**(D1): 24시간 상한은 1분마다 도는 청소가 집행하므로, 기한을 정확히 24시간에 두면 최대 약 1분 늦는다(헌법 III
   「늦어도 24시간」). 기한을 **2분 당긴다** — 녹음 `hard_deadline` = 처음 받은 때 + 23시간 58분, job `expires_at` = 받은 때 + 23시간 58분. 청소
   주기 1분(`AccuracySec=1s`)과 청소가 도는 시간을 더해도 24시간 안에 끝난다. 1분이 아니라 2분인 까닭은 「주기 1분 + 실행 시간」이 1분을 넘을 수
   있어서다. 자정은 「정한 시각」이므로 당기지 않는다(spec 「직후」 = 1분 안).
2. **강제 종료 흔적**(D2): 게시 중간 폴더 `outbox/.<id>.tmp/`, 원자적 쓰기의 임시 파일(`.*.tmp`), 기록 없는 녹음 폴더(받기가 복사한 뒤 기록 전에
   죽은 경우)는 ctime 으로 1시간이 지나면 청소가 지운다. 죽은 워커를 정리할 때는 게시 중간 폴더를 바로 지우고, 게시 rename 뒤 마무리 전에 죽은
   job(`interrupted`)의 `outbox/<id>/` 도 지운다. 1시간을 두는 까닭은 돌고 있는 받기·워커의 임시 파일을 지우지 않기 위해서다.
3. **문단 수를 내지 않는다**(D3): `summary.paragraph_count` 는 `len(paragraphs)` 라 헌법 I(출력으로 셀 수 있는 통계 금지)에 걸린다 → 뺀다. US5-7 은
   「문단 목록이 비어 있다」로 고쳤다. `hints.total` 은 stt 출력이 아니라 요청에서 오는 수이고, 헌법 V 가 요구하는 「버린 힌트」 알림(반영 N /
   전체 M, 빠진 용어)을 스스로 읽히게 하므로 남긴다(FR-020·SC-008).
4. **녹음 기록과 녹음 파일을 나눈다**(F1): 기록 `audio/<audio_id>.json`, 파일 `audio/<audio_id>/audio.<ext>`. 녹음을 지우면 파일 폴더만 지우고,
   기록(`deleted_at` — 다른 job 의 응답이 쓴다)은 그 녹음을 쓰는 마지막 job 의 `expires_at` 에 지운다.
5. **엔진의 임시 폴더는 엔진이 정한다**(E1): 엔진은 시작할 때 `tempfile.tempdir` 를 `<work_dir>/tmp` 로 정한다 — 환경 변수 `TMPDIR` 은 프로세스에서
   처음 한 번만 읽히므로(같은 프로세스에서 도는 시험에서는 바뀌지 않는다) 그것에 기대지 않는다. 워커도 `TMPDIR` 을 작업 폴더로 넣는다(그 밖의
   하위 프로세스용). `HF_HOME`·`HF_HUB_OFFLINE` 은 워커가 엔진 하위 프로세스에만 넣고, 단위 환경에는 두지 않는다(모델 받기 단위가 망을 써야 하므로).
6. **언어 목록 상수**(C2) → R5.
7. **삭제 시각이 지난 녹음**(C1): 받기·워커는 지금 시각이 그 녹음의 삭제 시각(정책 시각·`hard_deadline`)을 지났으면 청소를 기다리지 않고 먼저
   `delete_audio` 를 부른다(원인 포함) — 그 뒤 온 2차는 `source_unavailable`, 기다리던 job 은 시작하지 않는다(spec 경계 사례 8).
8. **잠금 차례**(C8) → R3.
9. **거절 때 지우는 첨부**(C3): 언제나 파일 이름 앞부분의 `<파일 id>.audio.*` 뿐이다. 본문의 `audio.file` 이 다른 앞부분을 가리켜도 건드리지 않는다.
10. **inbox 의 나이**(B1): 1시간 청소는 `st_ctime` 으로 잰다(rename·`copy2` 로 들어온 파일은 mtime 이 옛 값이다).
11. **기록 없는 응답의 중복**(C4): 거절·delete 답·받기 거절 응답은 파일을 읽어 `duplicate_refused_at` 을 더해 원자적으로 다시 쓴다.
12. **지운 job 의 응답**(C5): 「지금 지우기」는 결과가 있었으면 `result_deleted_at` 도 적는다. `deleted` 응답은 `result`(`deleted_at` 포함)·`started_at`·
    `finished_at` 을 남기고 `failure` 는 뺀다(스키마). `deleted` job 에 「결과 지우기」가 오면 `result_deleted_at` 이 있으면 `already_deleted`, 없으면 `no_result`.
13. **엔진 사양의 `request_id`**(C6): `spec.json` 에 `request_id` 를 넣는다(`result.json` 의 필수 칸).
14. **모델 판을 바꾼 배포**(C7) → R9.
15. **워커의 시작 시한**(C14): 워커는 줄이 빌 때까지 돈다 — job 마다 처리 시간 상한이 있고 GPU 잠금 대기에는 상한이 없다(FR-016) → 단위의
    `TimeoutStartSec=infinity`. 24시간이면 줄이 길 때 처리 중인 job 이 `interrupted` 로 끊긴다.
16. **말없이 버리는 일에 로그 한 줄**(C13): 이름이 UUID 가 아닌 inbox 파일을 버릴 때와 1시간 청소로 지울 때 파일 이름 없는 고정 문구를 남긴다(헌법 V, FR-037).
17. **첫 job 의 보관 상한 실패**(C12): 녹음을 가져온 job 은 `expires_at` 과 녹음의 `hard_deadline` 이 같아, `retention_limit` 실패가 같은 청소에서 응답과
    함께 사라진다 → 계약 §7 에 「응답이 사라지면 만료로 본다」.
18. **`summary.diarized`**(C11): 요청의 `diarize` 그대로다(말소리가 없어 화자 구분을 건너뛰어도 true).
19. **`delete_after`**(C9): `delete_policy` 가 `after_job` 이면 반드시 있고(그 정책을 정한 요청), 아니면 없다 — 스키마 조건으로 묶었다.
20. **`/var/lib/stt` 권한**(F17): systemd `StateDirectoryMode` 기본값은 0755 → 0751 로 둔다.
21. **패키지 자료**(C15): 스키마가 휠에 실리는지 계약 시험이 `uv build --wheel` 로 확인한다(Phase 8 까지 기다리지 않는다).

## R22. 삭제 단순화와 GPU 전용 — 운영자 결정 (2026-10-22, 001 병합 뒤)

운영자 결정: 「이용자가 마쳤다고 지우기를 요청하면 녹음과 회의록을 지우고, 아니면 밤 12시 직전에 지운다」, 「늦게 맡겨 끝나지 못하는 작업은
그대로 실패 — bhu-llm 이 『자정에 유지관리로 작업결과물과 자료가 유실될 수 있으니 자정 이후에 하라』고 알린다」, 「CPU 는 안 된다. 오직 GPU 만
하고, GPU 가 안 되면 안 되는 것」(명령줄 포함). 처리 시간 상한(R12)과 1차 전에 온 2차 받기(R13), 저장 위치(R14)는 그대로 확정.

- **결정 — 삭제**: 지우기는 「다 지우기」 요청과 매일 정리 둘뿐이다. 요청의 `delete_audio`, delete 의 `scope`, 응답의 `audio.delete_policy`·
  `audio.delete_after`·`failure.cause`, delete 답의 `no_result` 를 없앤다. 모든 기한은 `next_purge(t)` = t 보다 뒤인 첫 23:58 `Asia/Seoul` 이다
  (job·응답 `expires_at`, 녹음 `delete_at`). `RETENTION_DUE`(R21-1)는 없어진다.
- **왜 23:58 인가**: 운영자의 「밤 12시 직전」. 정확히 00:00 이면 00:00 직후에 받은 자료가 다음 날 00:00 + 청소 지연(1분 안)까지 남아 24시간을
  넘을 수 있다(헌법 III). 23:58 이면 청소가 늦어도 23:59 무렵 끝나 24시간 안이다. 23:58 뒤에 받은 작업은 다음 날 23:58 까지 남는다.
- **「다 지우기」의 범위**: 대상 job 의 녹음을 쓰는 모든 job(`audio.users`, 같은 요청자)을 `deleted` 로 만든다 — 「음성과 회의록을 지운다」는
  뜻은 1차·2차를 가리지 않는다. 다른 job 을 `failed(audio_deleted)` 로 만들던 동작(R20-8 `deleted_on_request`)은 없어진다.
- **job 이 끝나도 녹음을 지우지 않는다**: 「끝나면 바로」가 없으므로 `lifecycle.finish` 는 녹음을 지우지 않는다. 최종 리뷰 I2(after_job 녹음
  복구)와 남겨 둔 지적(공유 녹음 오삭제 틈)은 대상이 없어진다.
- **매일 정리 때의 대기·처리 중 job**: 지금 구조(녹음 기한 도래 → `failed(audio_deleted)` → 같은 청소의 만료 단계가 job 째 삭제)를 그대로 쓴다.
  `audio_deleted` 는 남지만 원인 칸은 필요 없다(원인이 하나뿐이다). 계약 §7 「응답이 사라지면 만료로 본다」가 호출하는 쪽의 기준이다.
- **결정 — GPU 전용**: 명령줄 `transcribe`·`batch`·`enroll` 과 화자 구분은 GPU 로만 돈다. `--device` 는 `auto`·`cuda` 만 받는다(둘 다 GPU —
  `auto` 는 기존 명령을 깨지 않으려고 남긴다, `cpu` 는 argparse 가 거절한다). GPU 를 쓸 수 없으면 모델을 올리기 전에 영어 고정 문구로 멈추고
  0 아닌 코드로 끝난다(전사: ctranslate2 CUDA 장치 0개, 화자 구분·목소리 등록: torch 가 CUDA 를 못 씀). CPU 기본 모델(`small`)·CPU 경고·
  `--device cpu` 안내 문구를 없앤다. GPU 잠금은 언제나 쥔다. GPU 메모리 부족 때 더 작은 모델로 넘어가는 명령줄 폴백(장치가 아니라 모델)은
  그대로다(FR-040).
- **대안**: 00:00 정리(24시간을 1분 넘길 수 있어 기각), 정리 때 대기 job 을 다음 날로 넘기기(운영자가 「그대로 실패」로 정함), 「다 지우기」를
  대상 job 하나로 한정(이용자 뜻과 어긋남), 명령줄 CPU 경고 유지(운영자가 금지).
- **시험**: 기존 시험 가운데 CPU 실행을 전제한 것만 바꾼다(SC-013). `RETENTION_DUE`·삭제 정책을 다루던 001 시험은 새 규칙으로 바꾼다.

