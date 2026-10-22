# 명령 계약 — `stt server` 와 명령줄의 GPU 잠금

새 명령은 systemd 단위가 부르는 `stt server` 묶음 하나다. 기존 명령(`fetch`·`transcribe`·`batch`·`enroll`)은 GPU 잠금에 줄을
서는 것 말고는 바뀌지 않는다(FR-040). 메시지·도움말은 영어다(헌법 언어 규칙). 기존 명령의 한국어 메시지는 이 기능이 바꾸지 않는다.

## 1. `stt server`

| 명령 | 하는 일 | 종료 코드 |
|---|---|---|
| `stt server intake [--root DIR]` | inbox 의 요청을 한 번 다 처리하고 끝난다(받아들임·거절·지우기) | 0 처리함(거절 포함) · 2 사용법 오류 · 75 다른 받기가 도는 중 |
| `stt server work [--root DIR] [--models DIR] --gpu-lock FILE` | 줄이 빌 때까지 job 을 하나씩 처리하고 끝난다 | 0 · 2 · 75 다른 워커가 도는 중 |
| `stt server sweep [--root DIR]` | 삭제 시각이 지난 것을 지우고, 멈춘 job 을 정리하고 끝난다 | 0 · 2 · 75 다른 청소가 도는 중(`sweep.lock`) |
| `stt server fetch-models --models DIR` | 고정한 커밋으로 모델을 받아 `refs/main` 을 그 커밋으로 쓰고, 고정 목록을 `DIR/.complete` 에 적는다. `.complete` 의 목록이 코드의 고정 목록과 같으면 망에 닿지 않고 바로 끝난다. 외부 접속과 `HF_TOKEN` 이 필요한 유일한 명령이다(research R9) | 0 · 2 · 1 받기 실패(어느 저장소인지 영어로) |

- 기본값: `--root /var/lib/stt`, `--models /var/lib/stt-models`. 시험은 임시 폴더를 넘긴다.
- 75 는 `EX_TEMPFAIL` — 단위의 `SuccessExitStatus` 에 넣는다(path 단위가 겹쳐 깨워도 실패로 치지 않는다).
- 사용자 입력이 섞인 값으로 셸을 부르지 않는다. 엔진과 ffmpeg 는 인자 목록으로만 띄운다(헌법 「저작권·단어장·개인정보 제약」).
- 로그(stdout/stderr → journal)에는 `request_id`·상태·실패 이유·시각만 쓴다. 전사문·힌트·요청자 표식은 쓰지 않는다(FR-037).
  이름이 UUID 가 아닌 inbox 파일을 버리거나 청소가 오래된 파일을 지울 때도 파일 이름 없이 고정 문구 한 줄을 쓴다(말없이 버리지 않는다, 헌법 V).
- 워커는 job 하나를 끝내고 GPU 잠금을 푼 뒤 `poll_s` 만큼 쉬고 다음 job 의 잠금을 시도한다 — 기다리던 명령줄이 그 사이에 잠금을 받는다(research R3).

### 1.1 엔진(내부)

`python -m stt.server.engine <work_dir>` 는 워커만 부르는 내부 진입점이다. `work_dir/spec.json`(job 의 처리 사양: `request_id`·`audio_path`·
`original_name`·`language`·`diarize`·`hints`·`duration_s`)을 읽고, 결과를
`work_dir/out/` 에, 판정을 `work_dir/outcome.json` 에 쓴다. 판정을 썼으면 실패 판정이어도 종료 코드 0, 판정을 쓰지 못하고 죽으면
워커가 `engine_error` 로 친다. 공개 계약이 아니므로 판을 따로 두지 않는다(같은 패키지 안에서만 바뀐다).

- 임시 파일: 엔진은 시작할 때 `tempfile.tempdir` 를 `<work_dir>/tmp` 로 정한다(화자 구분용 WAV 가 작업 폴더 안에만 생기게 — 환경 변수
  `TMPDIR` 은 프로세스에서 한 번만 읽혀 그것에 기대지 않는다, research R21-5). 워커도 `TMPDIR`·`HF_HOME`·`HF_HUB_OFFLINE=1` 을 엔진 환경에 넣는다.
- 판정: `{"status": "done"}` 또는 `{"status": "failed", "reason": <실패 이유>, "detail": <고정 영어 문장>}`.
- 시작 점검(모델을 올리기 전): CUDA 장치가 있고(화자 구분이면 torch 도 CUDA 를 봄) — 아니면 `gpu_unavailable`; 고정 저장소마다 `refs/main` 이
  고정 커밋과 같음 — 아니면 `models_unavailable`.
- 힌트는 반영되는 앞부분(`hints[:applied]`)만 엔진에 넣는다 — 실행 요약의 반영 수가 정의상 맞다(research R20-10).
- 값은 명령줄 기본값과 같다: `large-v3`·`cuda`, beam 5, 쉼 2초·800자, 화자 구분이면 단어 시각과 불확실 1.5초.

## 2. 명령줄의 GPU 잠금 — `STT_GPU_LOCK`

| 조건 | 동작 |
|---|---|
| `STT_GPU_LOCK` 이 없다(다른 기계, Windows) | 잠금 없이 지금처럼 돈다 |
| 있고, 장치가 `cuda` 로 정해졌다 | GPU 를 쓰기 전에(화자 구분 파이프라인·모델 적재 전) 그 파일을 읽기 전용으로 열어 배타 잠금을 건다. 이미 잡혀 있으면 stderr 에 `GPU is in use by another stt job; waiting for it to finish.` 를 한 번 쓰고 기다린다. 끝나면 놓는다 |
| 있고, 장치가 `cpu` | 잠그지 않는다(GPU 를 쓰지 않는다) |
| 있는데 파일이 없다 | 시작 전에 멈춘다: `STT_GPU_LOCK points to a missing file: <path>` (종료 코드 2) — 배포가 어긋난 것이므로 조용히 잠금 없이 돌지 않는다 |

- 대상: `stt transcribe`(그리고 그것을 부르는 `stt batch`, 묶음마다), `stt enroll`.
- `fcntl` 이 없는 플랫폼(Windows)에서도 명령줄이 그대로 돌아야 한다 — 잠금 모듈은 `fcntl` 을 함수 안에서 불러오고, 명령줄은 `STT_GPU_LOCK` 이
  있을 때만 잠금 모듈을 부른다.
- 없는 파일로 멈출 때(종료 2)는 `stt batch --keep-going` 도 다음 묶음으로 넘어가지 않는다.
- 잠금은 모델을 GPU 에 올려 둔 동안 내내 쥔다 — 메모리를 차지하는 동안이 겹치면 안 되는 구간이기 때문이다.
- 서버의 NixOS 모듈이 `STT_GPU_LOCK=/run/stt/gpu.lock` 을 시스템 환경 변수로 둔다([module.md](module.md)).

## 3. 명령줄의 다른 변경 — 말없던 CPU 화자 구분의 경고 (spec US4-5)

`stt transcribe --diarize`·`stt enroll` 이 GPU 로 정해졌는데 torch 가 CUDA 를 쓰지 못하면, 지금처럼 CPU 로 계속하되 stderr 에 한 번 쓴다:
`Speaker diarization is running on CPU because torch cannot use CUDA; it will be much slower.` 그 밖의 출력은 바뀌지 않는다(FR-040).
