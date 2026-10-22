# 교내 서버 전사 — 기술 메모 (idea 단계)

- 작성: 2026-10-22 · 짝 문서: [`idea.md`](idea.md)(무엇·왜) · 이 문서는 `/speckit-plan` 의 입력이다.
- 표기: 🟢 확인한 사실 · 🟡 결정한 설계(운영자 승인) · `[VERIFY]` 계획 단계에서 먼저 확인할 미검증 사실
- `path:line` 은 stt master `afc9603` 기준이다. 다른 저장소는 `geo-scout:`, `bhu-llm:`, `nixos-config:` 를 앞에 붙인다.

## 0. 결정 요약

| 항목 | 결정 | 절 |
|---|---|---|
| 통로 | geo-scout 와 같은 요청함(파일). 상주 HTTP 서비스는 두지 않는다 | §1 |
| 결과 형식 | 상태·전사록·문단 목록·실행 요약·실패 이유. 형식 정의를 패키지에 싣는다 | §2 |
| GPU | 서버와 명령줄이 함께 쓰는 잠금 하나로 차례로. 서버는 `large-v3` 고정, 더 작은 모델로 넘어가지 않는다 | §3 |
| 삭제 | 작업의 삭제 시기 + 처음 받은 때로부터 24시간 상한 + 결과는 회수 뒤 지우기 | §4 |
| 실행 환경 | 외부 접속 없음, 미리 채운 모델 폴더를 읽기만, 실행 중 토큰 불필요 | §5 |
| 배포 | uv.lock 의 휠을 Nix 로 묶는다. 막히면 `uv sync --frozen` + systemd | §6 |

## 1. 통로 — 요청함 🟡

- geo-scout 의 요청함을 그대로 본뜬다: `requests/inbox/`(bhu-llm 이 쓴다)·`outbox/`(bhu-llm 이 읽는다)·`rejected/`, 첨부를 먼저 쓰고
  요청 파일은 `.tmp` 로 쓴 뒤 이름을 바꾼다, 응답은 `accepted → done | failed` 로 원자적으로 바꾼다, 쓸 수 있는 계정은 전용 그룹뿐이다
  (`geo-scout:specs/001-admissions-pilot/contracts/data-directory.md:44-49,89-103`). 운영이 하나로 묶이고 공격면이 파일 권한뿐이다.
- 요청 종류는 둘이다: 전사 작업, 「지금 지우기」.
- 요청자 표식은 bhu-llm 이 확인한 값을 그대로 믿는다(geo-scout 와 같은 신뢰 모델, `data-directory.md:103`). stt 는 그 값을 「앞 작업 녹음
  가리키기」의 같은 요청자 확인에만 쓴다.
- 상태에는 대기 순서를 낸다. 진행률은 내지 않는다(bhu-llm 요구가 「대기 순서 또는 진행률」).
- 호스트 쪽 권한(OWUI 단위의 쓰기 경로·보조 그룹)은 nixos-config 에 둔다. stt 모듈이 bhu-llm 단위를 건드리지 않는다(geo-scout 선례 `data-directory.md:102`).

## 2. 작업·결과 약속 🟡

| | 칸 |
|---|---|
| 작업 | 녹음 파일 **또는** 앞 작업 ID(같은 요청자만), 언어, 힌트 단어장(선택, 한 줄에 한 용어), 화자 구분 여부, 원본 삭제 시기(`끝나면 바로` / `자정`), 요청자 표식 |
| 결과 | 상태, 전사록(txt — `[MM:SS]` 문단, 화자 구분 시 `[MM:SS] 화자N:`), 문단 목록(시작·끝·화자·본문·불확실), 실행 요약(모델·장치·녹음 길이·소요·반영 N/M·빠진 용어), 실패 이유 |

- 문단 목록은 이미 안에 있는 자료다: `Block(start, end, speaker, text, uncertain)`(`src/stt/diarize.py:68-84`). 지금은 txt 로만 쓴다.
  txt 의 `[MM:SS]` 는 시작 시각뿐이라 끝 시각이 필요한 쪽(회의록의 주제 구간·화자별 발언 시간)은 문단 목록을 쓴다.
- 반영 N/M 과 빠진 용어는 `TermsReport`(`src/stt/transcribe.py`, `74cb016`)를 그대로 싣는다.
- 불확실 표시 기준은 지금 값 그대로다: `DEFAULT_MIN_SPEAKER_SECONDS = 1.5`(`src/stt/transcribe.py:46`) 미만 문단은 `(?)`.
- 화자 번호는 pyannote 의 화자 이름을 파일 단위로 `화자N` 으로 바꾸므로(`src/stt/diarize.py` `speaker_label`) 한 전사록 안에서 일관된다.
- 형식 정의(JSON Schema)를 stt 패키지에 실어 bhu-llm 이 결과를 검사한다(핸드오프 S2).
- 서버 결과에는 srt 를 넣지 않는다. 명령줄의 `--srt` 는 그대로 둔다.

## 3. GPU 잠금과 모델 🟡

- 잠금 파일 하나를 서버 처리 단위와 명령줄 `stt transcribe`·`stt batch` 가 함께 잡는다. 먼저 잡은 쪽이 끝날 때까지 다른 쪽은 기다린다.
  명령줄은 기다리기 시작할 때 「서버 작업 진행 중 — 대기」를 알린다.
- 서버 작업의 대기 시간도 처음 받은 때로부터 24시간 상한 안에서 센다. 별도 대기 상한은 두지 않는다(spec FR-016, 운영자 결정 2026-10-22).
- 서버는 `large-v3` 고정. 지금 명령줄은 GPU 메모리가 모자라면 더 작은 모델로 넘어간다(`run_with_fallback`, `src/stt/transcribe.py:898`).
  서버 경로에서는 넘어가지 않고 실패로 끝낸다.
- `--device auto` 의 CPU 전환은 이제 경고가 나온다(`74cb016`). 서버는 `cuda` 를 명시한다 — GPU 가 없으면 `load_model` 이
  `RuntimeError: CUDA failed with error no CUDA-capable device is detected` 로 멈춘다(2026-10-21 작업자 확인). 서버 쪽 실패 이유는 이 원문을 그대로 내지 않고 정해진 이유로 바꾼다.

## 4. 삭제 🟡

| 대상 | 지우는 때 |
|---|---|
| 녹음·단어장 사본 | 작업의 삭제 시기(`끝나면 바로` = 그 작업 완료·실패 직후, `자정` = 00:00 KST), 「지금 지우기」, 처음 받은 때로부터 24시간 — 먼저 오는 것 |
| 처리 중 만든 음성 | 화자 구분용 임시 WAV(`tempfile.TemporaryDirectory`, `src/stt/transcribe.py:795`, 1시간 ≈ 115MB). 정상·예외 종료에는 지워지지만 강제 종료에는 남는다 → 처리 단위가 시작할 때와 주기적으로 남은 것을 청소한다 |
| 결과 | 지우기 요청, 또는 그 작업을 받은 때로부터 24시간 |

- 2패스: 1차는 `자정`, 2차는 `끝나면 바로` 로 맡기는 것이 기본 흐름이다. 2차가 오지 않으면 녹음은 1차의 자정에 지워진다.
- 저장 위치: 처리 중 원본과 임시 음성은 암호화된 루트 아래에만 둔다. 큰 디스크 둘은 평문이다(`bhu-llm:docs/ops/open-items.md` F193).
- 24시간 상한은 처리 단위와 별개인 청소 타이머가 지킨다 — 처리 단위가 멈춰도 상한이 지켜져야 한다.

## 5. 실행 환경 — 외부 접속 없음 🟢 시험 2026-10-22

- 시험(버리는 코드, 저장소 밖): 네트워크 네임스페이스로 외부 접속을 막고(curl 실패 확인) `HF_HUB_OFFLINE=1`, 가짜 `HF_TOKEN` 으로 60초 녹음을
  `--diarize --device cuda --model large-v3` 로 전사 → 종료 0, 처리 6초, 모델 적재 포함 19초. 실행 중에는 토큰이 쓰이지 않는다.
- 고칠 것 하나: `load_pipeline` 이 `HF_TOKEN` 이 없으면 멈춘다(`src/stt/diarize.py:544`). 오프라인 실행에서는 이 확인을 건너뛴다.
- 모델 폴더: 운영자가 배포할 때 한 번, agenix 의 `HF_TOKEN` 으로 Whisper `large-v3` 와 pyannote 게이트 모델 3종
  (`segmentation-3.0`, `speaker-diarization-3.1`, `speaker-diarization-community-1`)과 `wespeaker-voxceleb-resnet34-LM` 을 받아 둔다.
  서비스는 그 폴더를 읽기만 하고 외부 접속을 막고 돈다. 게이트 모델은 재배포 제약이 있을 수 있어 공유 바이너리 캐시에 올리지 않는다.

## 6. 배포 — 시험 결과와 결정

### 6.1 nixpkgs 로 묶기 — 시험 2026-10-22 🟢

stt flake 가 고정한 nixpkgs(26.11.20260801)에는 같은 판이 있다: faster-whisper 1.2.1, ctranslate2 4.8.1, pyannote-audio 4.0.7, torch 2.12.0
(uv.lock 은 2.13.0), av 17.0.1. 판 문제는 작다. 그러나 `nix build --dry-run` 결과:

| 시도 | 결과 |
|---|---|
| `cudaSupport = true` 전체 | **229개 직접 빌드**(torch 2.12 CUDA·triton·magma·onnxruntime·opencv·nccl·ctranslate2 포함), 1,172개 받기(8.6GiB). 공개 캐시(cache.nixos.org, nix-community)에 없다 — nixpkgs 를 올릴 때마다 몇 시간 빌드 |
| ctranslate2 만 CUDA + `torch-bin` | 평가 거부: `torch-bin` 은 CUDA 13 의 `cuda-bindings` 를 요구하는데 기본 `cudaPackages` 는 12.9 |
| `cudaPackages_13` 으로 통일 | 평가 오류(`attribute 'rocmSupport' missing`). ctranslate2 를 CUDA 13 으로 빌드해 동작하는지도 미확인 |

### 6.2 결정 — uv.lock 의 휠을 Nix 로 묶는다 🟡

- 지금 개발 환경에서 돌아가는 휠 조합(torch 는 자체 CUDA 13 스택, ctranslate2 는 CUDA 12 빌드 + `nvidia-cublas-cu12`, cuDNN 공유 —
  `pyproject.toml` extra 주석)을 uv2nix(pyproject.nix 계열)로 묶는다. 소스 빌드가 없고 uv.lock 해시로 재현된다.
- ~~`[VERIFY]` 이 경로는 아직 빌드해 보지 않았다.~~ → 2026-10-22 계획 단계에서 시험 통과: 휠만으로 빌드되고 망 없이 GPU 화자 구분 전사가
  끝났다(`specs/001-server-transcription/research.md` R1).
  확인할 것 — 휠의 공유 라이브러리 경로 고치기, 드라이버의 `libcuda`(`/run/opengl-driver/lib`), CUDA 12·13 두 스택이 한 환경에서 충돌하지 않는지.
- 막히면: 서버에서 `uv sync --frozen` 으로 만든 환경을 systemd 가 실행한다. 선언적이지 않아 핸드오프 S3 와 geo-scout 선례에서 벗어나므로 대비책으로만 둔다.
- NixOS 모듈은 stt 저장소에 둔다(geo-scout 의 `nix/module.nix`·`nix/package.nix`·`nix/vm-test.nix` 모양). stt 는 자기 nixpkgs 를 고정한다 —
  bhu-llm·geo-scout 도 호스트 nixpkgs 를 따르지 않는다(`nixos-config:flake.nix:78-92`).

## 7. 처리 시간 근거 🟢

- 화자 구분 없음: 이 서버 기록으로 `large-v3`(cuda, int8_float16) 48분 녹음 4:00, 47분 4:03, 22분 1:53 — 실시간의 약 11~12배
  (`bhu-llm:idea/stt-integration/tech-notes.md` §1.2, `transcribe.log`).
- 화자 구분 포함: 2026-08-03 화자 분리 도입 시험(RTX 2070 8GB)에서 22분 녹음 — 단어 시각 포함 전사 3:47 + 화자 분리 2:43.
- 1시간 추정(비례 외삽, 미실측): 1차 약 5분 + 2차 약 10분 + 화자 분리 약 7분. idea SC-1 의 제안값(1차 10분, 2차 25분)은 이것에 여유를 둔 값이다.

## 8. 핸드오프(`docs/handoff/bhu-llm-2026-10-15.md`) 대응

| ID | 이 문서에서 |
|---|---|
| S1 처리 단위 | §1, §3 |
| S2 약속 | §2 — 문단 목록(이번 논의의 S13)을 더한다 |
| S3 배포 | §5, §6 |
| S4 원본 삭제 | §4 |
| S5 한도 | idea Constraints(3시간·처리 시간 = 녹음 길이), §3 |
| S6 작업별 단어장 | §2 — 힌트 단어장 (2)만 받는다 |
| S7 화자 구분 배포 | §5 — `화자N` 만, 목소리 등록 없음 |
| S10 화자 번호 일관성 | §2 |
| S11 지금 지우기 | §1, §4 |
| S9·S12 | 처리됨(`74cb016`·`39d5c9d`·`afc9603`) |
| S8 영상 | 범위 밖(나중) |
| (S14 화자별 통계) | 넣지 않는다 — 호출하는 쪽이 문단 목록으로 계산 |

## 9. 명령줄에 바뀌는 것

- GPU 잠금에 줄을 선다(§3).
- 오프라인 실행에서 `HF_TOKEN` 확인을 건너뛴다(§5).
- 그 밖의 명령줄 동작은 바꾸지 않는다(idea SC-13).
