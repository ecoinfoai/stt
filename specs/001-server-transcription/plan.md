# Implementation Plan: 교내 서버 전사

**Branch**: `001-server-transcription` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-server-transcription/spec.md` · 짝 문서 `idea/server-transcription/tech-notes.md`

## Summary

bhu-llm 이 이용자의 녹음을 파일 요청함(inbox)에 맡기면 stt 가 받아 검사하고, GPU 한 장으로 하나씩 전사(선택: 화자 구분, 힌트 단어장)해
정해진 형식(전사록·문단 목록·실행 요약)으로 outbox 에 돌려주고, 녹음·임시 음성·힌트 사본·결과를 정한 때(매일 23:58 정리·「다 지우기」 요청 즉시)에
지운다. 같은 녹음을 고친 단어장으로 한 번 더 전사(2패스)할 수 있다. 구조는 geo-scout 의 요청함을 본뜬 세 systemd 단위 — 받기(GPU 없음, 수 초),
워커(엔진을 하위 프로세스로 띄워 멈출 수 있게), 청소(삭제 시각 집행) — 와 모델 받기 단위 하나다. 전사는 기존 명령줄 코드를 다시 쓰고,
운영자의 명령줄 전사와는 GPU 잠금 하나로 줄을 선다. 배포는 uv.lock 의 휠을 Nix 로 묶은 패키지와 NixOS 모듈이다(R1).

## Technical Context

**Language/Version**: Python 3.10+ (CI 3.10·3.12), 서버 실행은 Nix 의 python312

**Primary Dependencies**: faster-whisper 1.2.1(ctranslate2 4.8.1, CUDA 12 휠), pyannote.audio 4.0.7 + torch 2.13.0(CUDA 13 휠) — 기존 `diarize`
묶음; PyAV(기존, 길이 재기); huggingface_hub(기존, 모델 받기); **jsonschema(새 `server` 묶음, R6)**; ffmpeg(기존, 화자 구분용 WAV); systemd;
Nix — uv2nix·pyproject.nix·build-system-pkgs(R1: 휠만으로 빌드·망 없이 GPU 화자 구분 전사 통과, `LD_LIBRARY_PATH` 불필요, 닫힘 6.6GiB)

**Storage**: 파일만. `/var/lib/stt`(LUKS 위 btrfs 루트, 백업·스냅샷 제외), `/var/lib/stt-models`, `/run/stt/gpu.lock`(contracts/README §2)

**Testing**: pytest(가짜 모델·가짜 엔진·시각 주입, GPU·망 없이) · NixOS VM 시험(GPU 없이 단위·권한·삭제·잠금) · 호스트 시험(실제 GPU) —
[quickstart.md](quickstart.md)

**Target Platform**: NixOS x86_64 서버 miniecoinfo(RTX 2070 8GB, 램 32GB, systemd 261). 명령줄은 지금처럼 다른 Linux·Windows 에서도 돈다

**Project Type**: 명령줄 도구 + 시스템 서비스(파일 요청함 처리 단위)

**Performance Goals**: 1시간 녹음, 대기 없음 — 1차 10분·2차(화자 구분) 25분 이내(SC-001, 제안값·호스트 실측으로 확정). 접수 확인 수 초(SC-011),
「다 지우기」 1분 안(SC-005, 설계상 2초)

**Constraints**: 처리 중 외부 접속 없음 · 녹음 ≤ 3시간 · 처리 시간 ≤ max(녹음 길이, 5분) · 녹음·힌트 사본은 처음 받은 때로부터, 응답·결과는 그 job 을 받은 날(Asia/Seoul)의 23:58 에 삭제(매일 정리, R22) — 어느 것도 받은 때로부터 24시간을 넘지 않는다 ·
스왑·덤프 금지 · 로그에 개인정보 없음 · GPU 에서만(작은 모델·CPU 로 대신하지 않음) · 코드에 한글 없음

**Scale/Scope**: 이용자 약 14명(`report-authors`), 동시 처리 1, 줄 길이 한 자릿수

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* — Phase 1 설계 뒤 다시 확인한 결과다.

| 원칙 | 이 설계에서 | 판정 |
|---|---|---|
| I. 최소 역할 | 서버가 하는 일은 받기·전사·돌려주기·지우기뿐. 힌트 선별·(1) 검증·화자별 통계·회의록은 호출하는 쪽(contracts/README §1·§8). 기존 코드 변경 넷(R8)은 서버가 같은 전사 코드를 다시 쓰기 위한 것이고 새 명령줄 기능이 아니다. 실행 요약에는 출력으로 셀 수 있는 값(문단 수)을 넣지 않는다(R21-3) — `hints.total` 은 요청에서 오는 수로, 헌법 V 의 「버린 힌트」 알림을 스스로 읽히게 한다 | 통과 |
| II. 교내 처리 | 받기·워커·청소 단위 `PrivateNetwork=true`(R2). 외부 접속은 모델 받기 단위뿐 — 녹음을 다루지 않고, 부팅마다 돌되 받아 둔 고정 목록이 같으면 망에 닿지 않는다(R9). LLM 호출 없음. 입력은 inbox 의 파일뿐 | 통과 |
| III. 삭제 보장 | 삭제 시각 계산(data-model §5·§6), 끝 상태로 가는 길 하나(`lifecycle.finish`, R20-9), 워커와 따로 도는 청소 단위(`AccuracySec=1s`), 엔진 하위 프로세스를 죽여 즉시 지우기, `MemorySwapMax=0`·`LimitCORE=0`·워커 `RemoveIPC`, 암호화 루트·백업 제외(R14), 이용자별 보관 없음, `화자N` 만. 경로마다 시험(quickstart A·B) | 통과 |
| IV. 원문 무결성 | 전사록은 엔진 출력 그대로. 교정 규칙 없음. 불확실 표시·문단 시각 유지 | 통과 |
| V. 말없는 실패 금지 | 정해진 실패 이유 11개(data-model §9), 작은 모델·CPU 로 대신하지 않음, 판정 재확인 뒤에만 게시, 경계 검사(스키마 + 의미, 링크·일반 파일 확인), 반영분만 엔진에 넣어 힌트 반영 수가 정의상 맞음(R20-10), 고정 모델 판 확인(R9). 명령줄도 GPU 전용 — CPU 로 넘어가지 않고 이유와 함께 멈춘다(R22, US4-5·US4-6, 헌법 1.1.0) | 통과 |
| VI. 계약으로만 | JSON Schema 세 개를 패키지에 싣고 바이트 일치 시험, `schema_version`, 호환 깨짐은 판·핸드오프. bhu-llm 코드 공유 없음. 호스트 연결은 nixos-config | 통과 |
| VII. 테스트 우선 | 과제마다 RED → GREEN 과 닫는 시나리오(tasks 템플릿). 기본 시험은 GPU·망·모델 없이. 시나리오 시험 이름 `test_us<N>_<n>_...` | 통과 |
| 저작권·단어장·개인정보 | (1)은 stt 에 오지 않음. 힌트 사본은 job 이 끝나면 지움. 비밀은 agenix 경로로만. 셸 보간 없음(인자 목록) | 통과 |
| 개발 환경·언어 | Nix + uv, GPU 한 줄, Conventional Commits, 최종 `project-audit`. 새 코드는 영어 — 손대지 않는 기존 한국어는 아래 Complexity Tracking | 조건부 통과 |

## Project Structure

### Documentation (this feature)

```text
specs/001-server-transcription/
├── spec.md              # 기능 정의(plan 단계 보정 포함 — research R11~R14)
├── plan.md              # 이 문서
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1 — 확인 순서 A·B·C
├── contracts/           # Phase 1 — 결합 지점 전부
│   ├── README.md        #   요청함 규약·검사·지우기·예시
│   ├── request.schema.json
│   ├── response.schema.json
│   ├── result.schema.json
│   ├── cli.md           #   stt server 명령, 명령줄 GPU 잠금
│   └── module.md        #   NixOS 모듈과 호스트의 몫
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks 가 만든다
```

### Source Code (repository root)

```text
src/stt/
├── cli.py               # 바뀜: `server` 명령 묶음 등록
├── gpu_lock.py          # 새: GPU 잠금(명령줄·워커 공용, STT_GPU_LOCK)
├── transcribe.py        # 바뀜: 끝 시각 문단(build_timed_paragraphs), 빈 결과 허용 선택, 화자 문단/글 분리, main 의 GPU 잠금 (R8)
├── diarize.py           # 바뀜: 오프라인이면 HF_TOKEN 불요 (R8-4), CUDA 없으면 멈춤 (R22, US4-6)
├── enroll.py            # 바뀜: GPU 잠금
└── server/              # 새
    ├── __init__.py
    ├── cli.py           #   stt server intake|work|sweep|fetch-models
    ├── schemas/         #   request·response·result .schema.json (패키지 자료, contracts 와 바이트 일치)
    ├── contract.py      #   스키마 적재·검사
    ├── layout.py        #   폴더 배치·식별자 규칙·원자적 쓰기·flock 도우미
    ├── records.py       #   Job·Audio 내부 기록(읽기·쓰기)
    ├── lifecycle.py     #   상태 전이와 삭제 기본 동작 — 끝 상태로 가는 길 하나(finish), 녹음·결과·전부 지우기 (R20-9)
    ├── deadlines.py     #   매일 정리 시각(Asia/Seoul 23:58)·처리 시간 상한 — 순수 함수
    ├── responses.py     #   기록 → 응답 문서(상태·지우기 결과·거절) — 순수 함수
    ├── intake.py        #   받기 단계
    ├── worker.py        #   워커(엔진 하위 프로세스 관리, 게시)
    ├── engine.py        #   엔진 진입점 `python -m stt.server.engine <work_dir>`
    ├── sweep.py         #   청소 단계(주기 점검만 — 삭제 동작은 lifecycle)
    └── models.py        #   고정 모델 목록·받기

tests/
├── server/              # 새 — conftest(임시 루트·고정 시각·요청 만들기·워커 실행 도우미·GPU 있음 흉내), fake_engine.py,
│                        #   test_contract·test_deadlines·test_layout·test_records·test_responses·test_engine·test_models·
│                        #   test_server_cli·test_logging, 수용 시나리오 test_us1.py … test_us5.py, test_edge_cases.py
├── test_gpu_lock.py     # 새
├── test_cli_gpu_lock.py # 새 — 명령줄 잠금(transcribe·batch·enroll), fcntl 없는 환경
└── test_transcribe.py · test_diarize.py   # 새 시험만 더함(기존 시험은 고치지 않는다 — 출력 동일은 기존 시험이 지킨다)

nix/
├── package.nix          # 새: uv2nix 서버 환경 (R1)
├── module.nix           # 새: services.stt (contracts/module.md)
└── vm-test.nix          # 새: nixosTest — quickstart B
flake.nix · flake.lock   # 바뀜: 입력(pyproject-nix·uv2nix·build-system-pkgs), packages·nixosModules·checks(server-smoke·server-vm). devShell 은 그대로
pyproject.toml · uv.lock # 바뀜: `server` 묶음(jsonschema), 스키마 패키지 자료, 판 0.3.0
.github/workflows/ci.yml # 바뀜: `uv sync --locked --extra server`; flake 는 평가만(`--no-build`) — R15
README.md                # 바뀜: 서버 절(영어)
```

**Structure Decision**: 단일 프로젝트를 유지한다. 서버는 `stt.server` 하위 묶음 하나로 두고, 명령줄 쪽 변경은 R8 의 네 곳과 GPU 잠금뿐이다.
무거운 라이브러리(faster-whisper·torch·pyannote)는 엔진 안에서만 불러와 받기·워커·청소와 시험이 가볍게 돈다 — `faster_whisper.tokenizer` 만 불러도
torch 가 올라오므로(R5 측정) 받기의 언어 확인은 `contract.py` 의 상수 목록을 쓴다.

## Phase 계획 (tasks 의 뼈대)

| 단계 | 내용 | 닫는 이야기 |
|---|---|---|
| 0. 기반 | `server` 묶음·스키마 패키지 자료·계약 시험, 배치·식별자·원자적 쓰기, 시각 계산, GPU 잠금 | — (모든 이야기의 선행) |
| 1. US1 (P1) | 받기(경계 검사·파일 안전·받아들임·대기 순서), 워커(가짜 엔진), 엔진(시작 점검 GPU·모델 판, 가짜 모델: 문단 끝 시각·화자·힌트 반영 수), 결과 게시 | US1 + US5-5·US5-6 |
| 2. US2 (P1) | 매일 정리(23:58)·결과 만료, 지우기(「다 지우기」 요청·매일 정리 둘뿐), 처리 중 취소, 청소 단계, 죽은 워커 정리 | US2 |
| 3. US3 (P2) | `from_request`, 같은 요청자 확인, 대기 중 1차 가리키기 | US3 |
| 4. US4 (P2) | 명령줄 GPU 잠금(transcribe·batch·enroll, Windows 무해), 워커의 잠금 줄 서기, GPU 전용 명령줄(R22) | US4 |
| 5. US5 (P3) | 길이·형식·시간 초과·GPU 메모리·엔진 오류 분류·무음·짧은 녹음 | US5 |
| 6. 배포 | 모델 받기(고정 커밋), 패키지 smoke check → uv2nix 패키지, VM 시험 → NixOS 모듈, CI, README | 경계 사례·FR-015·FR-035·FR-037 |
| 7. 확인 | 호스트 시험 C(운영자와 함께), 핸드오프 §7, `T-Verify-Final`(project-audit) | SC-001~013 |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 손대지 않는 기존 코드에 한국어 docstring·메시지가 남고, 이 기능이 고치는 함수 안의 기존 사용자 출력 문자열도 그대로 남는다(헌법 「언어」: 코드에 한글 없음 — R19) | 헌법이 이전을 별도 변경으로 정했다(운영자가 비준한 헌법 1.0.0 의 언어 절과 Sync Impact Report — 승인된 예외). 이 기능은 새 코드와 고치는 함수만 영어로 쓴다(R19) | 지금 모든 모듈을 옮기면 이 기능과 상관없는 변경이 모든 파일·시험에 퍼진다(외과적 변경 원칙 위반). 전사록 머리글·`화자N` 같은 한국어 산출물의 자리(TODO(KOREAN_PRODUCT_TEXT))도 아직 정하지 않았다 |
