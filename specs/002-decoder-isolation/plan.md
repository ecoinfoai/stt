# Implementation Plan: 녹음 해독 격리

**Branch**: `002-decoder-isolation` | **Date**: 2026-11-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-decoder-isolation/spec.md` · 짝 문서 `idea/decoder-isolation/tech-notes.md` · 인계
`docs/handoff-stt-decoder-security-20261108.md`

## Summary

맡긴 녹음의 바이트를 해석하는 일을 GPU 가 있는 처리 단위와 모든 작업의 자료가 있는 서비스 계정에서 떼어 낸다. 세 단계로 배포한다.

- **P1**: 서버 단위 넷(받기·워커·청소·모델 받기)에 시스템 호출 묶음 `@system-service`, 기본 아키텍처 제한, 쓰기·실행 메모리 금지를 건다. CUDA 쪽
  예외는 호스트에서 관측한 것만 더한다(R1~R3).
- **P2**: 녹음 읽기(받기의 길이 재기, 전사·화자 구분용 해독)를 새 소켓 활성화 단위 `stt-reader@.service` 로 옮긴다(R4~R11).
  - 연결마다 systemd 가 인스턴스를 새로 띄운다. 인스턴스는 일회용 사용자로, GPU 장치·망·상태 폴더 없이 돌고, 받기·워커가 넘긴 녹음 기술자 하나와
    출력 파이프만 쥔다.
  - 안에서는 녹음을 해석하지 않는 감독이 PyAV 해독 자식을 띄우고, 끊김이나 상한이 오면 바로 죽인다.
  - 해독된 음성은 워커가 그 작업의 작업 폴더에 16 kHz 모노 WAV 로 쓴다. GPU 단계는 이 WAV 만 받는다. 서버 경로의 해독기는 PyAV 하나가 된다 — 이
    호스트에서 잰 결과 바이트가 지금 전사 입력과 같았다.
  - 읽기를 시작한 뒤의 모든 실패는 `unreadable_audio`, 띄우지 못하면 `engine_error`(받기는 001 FR-032a)다.
- **P3**: 녹음을 해석하는 구성 요소의 판·날짜·정책과 매달 확인 기록을 문서 하나에 두고, 저장소의 잠금 파일과 시험으로 맞춘다(R16).

계약(스키마·경로·지우기·재전사·23:58)과 실패 이유 목록은 바뀌지 않는다. 단계마다 bhu-llm 으로 회신 인계를 낸다(R17).

## Technical Context

**Language/Version**: Python 3.10+ (CI 3.10·3.12), 서버 실행은 Nix 의 python312 — 001 그대로

**Primary Dependencies**: 새 의존성 없음.
- PyAV 18.0.0(휠 안 FFmpeg, libavcodec 62.28.102)이 서버 경로의 유일한 해독기가 된다(R7).
- systemd 262: 소켓 활성화 `Accept=yes`, `DynamicUser`, seccomp 필터(R4·R12).
- 표준 라이브러리: `socket`(SOCK_SEQPACKET, `send_fds`/`recv_fds`), `subprocess`, `wave`.
- faster-whisper 1.2.1 · CTranslate2 4.8.1 · torch 2.13.0 · pyannote.audio 4.0.7 은 그대로다. nixpkgs `ffmpeg` 8.1.2 는 명령줄만 쓴다.

**Storage**: 파일만. 새 내부 자료는 `work/<request_id>/audio.wav`(작업마다, 0600, 3시간 녹음에 363 MB 이하, R8)와 소켓
`/run/stt-reader.sock`(stt:stt 0600) 둘이다. 나머지는 001 그대로(`/var/lib/stt` 는 LUKS 위 루트, 여유 175 GB 🟢).

**Testing**: [quickstart.md](quickstart.md) 의 세 층.
- pytest: 흉내 읽기 소켓(연결마다 진짜 감독 또는 대역), 대역 자식, 가짜 엔진. GPU·망·systemd 없이 돈다.
- NixOS VM 시험: P1 설정, 읽기 단위의 격리·권한·실패·삭제. 대역은 specialisation 으로 넣는다.
- 호스트 시험: CUDA 쪽 호출 관측, 쓰기·실행 메모리, 기준 녹음 비교, 처리 시간.

**Target Platform**: NixOS x86_64 서버 miniecoinfo — RTX 2070 8GB, 램 32GB, 커널 6.18.60, systemd 262, LSM `landlock,yama,bpf`, Yama
`ptrace_scope=1`, `io_uring_disabled=0` 🟢. 명령줄은 지금처럼 다른 Linux·Windows 에서도 돈다.

**Project Type**: 명령줄 도구 + 시스템 서비스. 이번에 단위 하나(소켓 + 템플릿)를 더한다.

**Performance Goals**:
- 접수 확인은 맡긴 직후(1분 안, 001 SC-011)다. 읽기 인스턴스 시작과 길이 재기는 1초 안으로 본다 — 잰 것은 Python + PyAV 0.07초(감독·자식
  두 번)와 길이 재기 3 ms 이고, systemd 의 인스턴스 시작은 `[VERIFY]`(C-P2-6)다.
- 1시간 녹음의 1차 10분·2차 25분(001 SC-001) — 읽기 단계의 해독(1시간 AAC 약 4초)이 지금 엔진 안의 해독을 대신하고, 2차의 `ffmpeg` 변환은 없어진다.
- 3시간 녹음의 해독은 최악 18초(Opus)다(R9).

**Constraints**:
- 계약과 실패 이유 목록은 그대로다. 해독된 음성은 001 처리 중 음성의 삭제 규칙을 따른다(FR-012).
- 읽기 단위는 망에 닿지 않는다. 엔진은 GPU 로만 돈다.
- 읽기 단위를 못 쓸 때 다른 곳에서 해독하는 대체 길은 없다(헌법 V).
- 코드에 한글을 쓰지 않는다. 해독기를 노리는 파일을 만들거나 보관하지 않는다(FR-028).
- P1 은 P2 없이 배포할 수 있어야 한다.

**Scale/Scope**: 이용자 약 14명. 동시에 도는 읽기는 많아야 둘(받기 1 + 워커 1). 한 업로드는 최대 5번 해독된다.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* — 아래는 Phase 1 설계 뒤 다시 확인한 결과다. Phase 0 전의 판정도 같았다.

| 원칙 | 이 설계에서 | 판정 |
|---|---|---|
| I. 최소 역할 | 새 기능이 없다. 「받는다·받아 적는다」 안의 녹음 읽기를 떼어 놓을 뿐이고, 읽기 단위는 길이 재기와 해독만 한다. 호출하는 쪽이 stt 의 결과로 할 수 없는 일(서버 안의 격리)이다. 명령줄 명령은 그대로다 | 통과 |
| II. 교내 처리 | 읽기 단위는 망 이름공간 없이 돌고 소켓도 만들 수 없다. 해독된 음성은 같은 기기의 작업 폴더에만 있다. 망을 쓰는 단위는 지금처럼 모델 받기뿐이다 | 통과 |
| III. 삭제 보장 | 해독된 음성은 작업 폴더 안에 있어서, 지금의 모든 끝 경로(`finish`·워커 마무리·죽은 워커 정리)가 지운다(R13). 읽기 인스턴스는 스왑·덤프가 없고, `/tmp` 는 사설 tmpfs 이며, 파일에 자료를 쓰지 못한다. 끊김이 오면 감독이 바로 끝낸다 — 해독기가 장악돼도 그렇다(R5). 다섯 경우(정상·실패·강제 종료·「다 지우기」·23:58) 모두 남은 파일 0 을 시험한다(SC-008) | 통과 |
| IV. 원문 무결성 | 전사 입력 샘플은 지금과 바이트까지 같다(측정 + A 층 시험). 화자 구분 입력도 사인파 측정에서 같았고, 기준 녹음으로 다시 확인한다. 다르면 운영자가 받아들인 뒤에만 완료다(SC-007) | 통과 |
| V. 말없는 실패 금지 | 읽기 단위를 띄우지 못하면 대신 해독하지 않고 `engine_error`(받기는 요청 오류)로 끝낸다. 시작 뒤의 실패는 `unreadable_audio` 다. 호출 필터를 어기면 SIGSYS 로 끝난다(조용한 EPERM 이 아니다). 메시지·기술자·출력 크기·WAV 머리는 경계에서 검사한다. 엔진의 일어날 수 없는 해독 분류는 지운다. 해독 중 잘못된 프레임을 건너뛰는 것은 faster-whisper 가 지금 하는 그대로이고, 같음 시험이 지킨다 | 통과 |
| VI. 계약으로만 | 스키마 바이트·경로·지우기 의미·`from_request`·23:58 이 그대로다. 고치는 것은 내부 문서뿐이다(contracts/module.md §5). 회신 인계는 두 건이다. bhu-llm 과 코드를 나누지 않는다 | 통과 |
| VII. 테스트 우선 | 과제마다 RED → GREEN 과 닫는 시나리오를 적는다. P1 은 VM 시험의 단언을 먼저 쓰고(RED) 모듈을 바꾼다(GREEN). 읽기 실패는 대역으로 만든다. 기본 시험은 GPU·망·모델·systemd 없이 돈다. 시험 이름은 `test_decoder_us<N>_<n>_...` 다 | 통과 |
| 저작권·단어장·개인정보 | 단어장 흐름은 그대로다. 로그에는 작업 ID·상태·이유만 남고, 읽기 단위는 작업 ID 도 모른다. 셸 보간이 없다(인자 목록). 비밀을 다루지 않는다 | 통과 |
| 개발 환경·언어 | Nix + uv, 새 의존성 없음, 무거운 실행은 `captest`, 최종 `project-audit`. 새 코드는 영어다. 손대는 `transcribe.py` 함수 안의 기존 한국어 문장은 아래 Complexity Tracking | 조건부 통과 |

## Project Structure

### Documentation (this feature)

```text
specs/002-decoder-isolation/
├── spec.md              # 기능 정의
├── plan.md              # 이 문서
├── research.md          # Phase 0 — R1~R18
├── data-model.md        # Phase 1 — 읽기 인스턴스·프로토콜·해독된 음성·실패 지도·기록 문서
├── quickstart.md        # Phase 1 — 확인 순서 A·B·C (B-P1·B-P2·C-0·C-P1·C-P2·C-P3)
├── contracts/
│   ├── reader.md        #   읽기 프로토콜(내부 계약), 상수, 명령줄, 대역의 자리
│   └── module.md        #   단위 설정의 바뀜과 확인 명령
├── checklists/requirements.md
├── handoff/             # 구현 뒤: p1-reply.md, p2-reply.md
└── tasks.md             # /speckit-tasks 가 만든다
```

### Source Code (repository root)

```text
src/stt/
├── transcribe.py        # 바뀜: transcribe_file·diarized_blocks 의 안쪽을 떼어, 엔진이 해독된 음성으로 같은 설정을 쓰게 한다(명령줄 동작 그대로, R11)
└── server/
    ├── cli.py           # 바뀜: `read` 명령, intake·work 의 `--reader-socket`
    ├── reader.py        # 새: 읽기 단위 — 감독(serve)과 해독 자식(child probe|decode), 프로토콜 상수(contracts/reader.md §4~6)
    ├── reader_client.py # 새: 받기·워커 쪽 — probe()·decode() 와 다섯 결과(contracts/reader.md §7)
    ├── intake.py        # 바뀜: 길이 재기는 읽기 단위로(probe_duration 과 형식 목록은 reader.py 로), 띄우지 못함은 예외로 올림(001 FR-032a)
    ├── worker.py        # 바뀜: 읽기 단계(→ work/<id>/audio.wav), 두 단계에 걸친 작업 상한, spec.json 에서 audio_path 뺌, 엔진 이유에서 unreadable_audio 뺌
    └── engine.py        # 바뀜: work_dir/audio.wav 만 읽음, PyAV 오류 분류를 지움

tests/
├── server/
│   ├── conftest.py              # 바뀜: 흉내 읽기 소켓(연결마다 진짜 감독이나 대역) — 받기·워커 시험이 저절로 쓴다
│   ├── fake_reader.py           # 새: 읽기 인스턴스 자리의 대역
│   ├── test_reader.py           # 새: 감독, 진짜 자식(길이 재기·해독·faster-whisper 와 같음)
│   ├── test_reader_client.py    # 새: 다섯 결과의 경계
│   ├── test_decoder_us2.py      # 새: US2 — GPU 단계 입력, 작업마다 다른 읽기, 시간
│   ├── test_decoder_us3.py      # 새: US3 — 실패 지도
│   ├── test_decoder_us4.py      # 새: US4 — 계약, 작업 5개, 삭제 다섯 경우, 명령줄
│   └── test_engine.py · test_us1.py · test_us3.py · test_intake.py · test_us5.py   # 바뀜: 내부 경계가 바뀐 곳만(research R11)
└── test_server_security_doc.py  # 새: docs/server-security.md §4 ↔ uv.lock·flake.lock (US5)

nix/
├── module.nix           # 바뀜: hardening 에 P1 세 설정(US1), stt-reader.socket·stt-reader@.service, intake·worker 의 requires·ExecStart(US2)
└── vm-test.nix          # 바뀜: B-P1·B-P2 부분 시험, 읽기 단위 대역 specialisation, 가짜 NVIDIA 노드

docs/
└── server-security.md   # 새(한국어): §1 단위별 제한·예외, §2 녹음 읽기 격리, §3 작업 실패와 단위 비정상 종료 구별, §4 판 기록·갱신 정책·매달 확인 기록

README.md                # 바뀜: Server 절에 읽기 단위 한 줄과 docs/server-security.md 링크(영어)
specs/001-server-transcription/contracts/{module,cli,README}.md   # 바뀜: contracts/module.md §5
```

**Structure Decision**: 단일 프로젝트를 유지한다. 읽기 쪽은 `stt.server.reader`(읽기 단위 안에서 도는 코드)와 `stt.server.reader_client`(받기·워커가 부르는
코드) 두 모듈로 나눈다. 받기·워커는 PyAV 를 불러오지 않게 되고, 읽기 단위의 감독은 표준 라이브러리만 불러온다 — PyAV 는 해독 자식 안에서만
읽힌다. `diarize.py` 는 바뀌지 않는다. `diarize_wav`·`sentence_embeddings`(`src/stt/diarize.py:836`·`:890`)가 이미 WAV 경로를 받으므로, 엔진은
`diarized_blocks` 에서 떼어 낸 WAV 쪽 함수(`ffmpeg` 변환 다음 부분)를 해독된 WAV 로 부르면 된다. 명령줄의 `diarized_blocks`·`enroll` 은 지금처럼
`to_wav` 를 거친다.

## Phase 0 결과 — 주요 결정

| # | 결정 | 근거의 요점 |
|---|---|---|
| R1·R2 | 네 단위 공통 `@system-service` + `native`. 어기면 SIGSYS. 예외는 관측(`SystemCallLog`)한 것만 | 🟢 이 호스트에서 관측 기록이 커널 로그에 남음 |
| R3 | 쓰기·실행 메모리 금지가 기본. 워커만 호스트에서 판정 | 🟢 PyAV 경로는 켠 채로 동작 |
| R4 | 소켓 활성화 `Accept=yes` + `DynamicUser` + SCM_RIGHTS 기술자 넘기기 | 연결마다 인스턴스·UID·cgroup. 인계가 든 수단과 같음. Landlock·systemd-run·특권 중개는 기각 |
| R5 | 감독(녹음 안 읽음) + 해독 자식. 끊김·상한에 감독이 자식을 죽임 | 장악돼도 FR-013. 🟢 좁힌 묶음으로 해독 동작(`@resources` 는 막지 않음 — `mbind`) |
| R6 | 「시작」 표시가 `engine_error` 와 `unreadable_audio` 를 가름 | 녹음을 처음 해석하는 것은 자식 |
| R7 | 해독기 하나(PyAV). 서버 경로에서 `ffmpeg` 명령을 뺀다 | 🟢 faster-whisper·ffmpeg 명령과 바이트까지 같음 |
| R8 | 작업마다 해독, 작업 폴더의 WAV, 파이프로 받아 워커가 씀, 크기 상한 | 새 삭제 경로 불필요 |
| R9 | 길이 재기 30초, 해독 240초, 768M, 파일 쓰기 0, `RuntimeMaxSec=300` | 🟢 형식 7개 실측(3시간 최악 18초, 최대 104 MB) |
| R10·R11 | 실패 지도, 두 단계에 걸친 작업 상한, 엔진은 WAV 만 | data-model §5 |
| R16 | 서버 경로의 해독기가 하나라 매달 갱신은 `av` 하나. 문서 ↔ 잠금 파일 시험 | 기술 메모 §6 의 `[VERIFY]` 를 설계로 풀었다 |

남은 `[VERIFY]` 는 모두 VM·호스트에서만 확인할 수 있고, quickstart 의 단계에 들어 있다: B-P2-1(단위 파일의 `RestrictAddressFamilies=none`·
`ProtectProc`·`ProcSubset`·`InaccessiblePaths`), B-P2-2(샌드박스 안의 소켓 접속), B-P2-4(인스턴스마다 다른 UID), C-P1-1(CUDA 쪽 호출),
C-P1-2(워커의 쓰기·실행 메모리), C-P2-3(호스트의 해독 시간), C-P2-5(기준 녹음의 두 해독기 같음), C-P2-6(인스턴스 시작을 더한 접수 확인
시간).

## Phase 계획 (tasks 의 뼈대)

| 단계 | 내용 | 닫는 이야기·요구 | 배포 |
|---|---|---|---|
| 0. 관측(호스트, 운영자) | C-0 기준 결과 보관, C-P1-1 관측 → 예외 목록 | US1 준비 | — |
| 1. P1 | VM 시험의 P1 단언(RED) → `hardening` 세 설정 + 관측한 예외(GREEN). `docs/server-security.md` §1·§3. 워커의 쓰기·실행 메모리 판정 | US1-1~6, FR-001~005, FR-023 | **P1 배포**: P1 커밋을 master 에 합치고 push(운영자 승인) → nixos-config 의 `stt` 입력 갱신 → `nxr`. C-P1-2, 회신 `p1-reply.md` |
| 2. 읽기 단위 | `reader.py` 감독·자식, `reader_client.py`, `stt server read`, 흉내 소켓과 대역 | US3-2~5(대역), US2-6·7, FR-006·009·011·015·016·019 | — |
| 3. 받기·워커·엔진 | 받기의 길이 재기 이전, 워커의 읽기 단계·작업 상한·`spec.json`, 엔진의 WAV 입력·분류, `transcribe.py` 안쪽 떼기 | US2-5·8·9, US3-1·6~9, US4-1~3·7, FR-010·012~014·017·018·021·022 | — |
| 4. 모듈·VM | 소켓·템플릿 단위, 받기·워커 연결, B-P2-1~7 | US2-1~4·6, US4-4~6, SC-002~006·008, FR-007·008·013 | — |
| 5. P3 | 판 기록 시험(RED) → `docs/server-security.md` §4(GREEN), 정책, 첫 매달 확인 줄 | US5-1~5, FR-024~026 | **P2·P3 배포**(bhu-llm FR-659 전): 같은 순서. C-P2-1~8, C-P3, 회신 `p2-reply.md` |
| 6. 확인 | 001 계약 문서 고치기(contracts/module.md §5), README, `T-Verify-Final`(project-audit: qa-engineer + adversary — adversary 는 코드·설정만 읽는다) | SC-001~010, FR-020·027·028 | — |

- 단계 1 은 단계 2~5 와 상관없이 배포할 수 있다 — `module.nix` 의 `hardening` 만 바꾸고 Python 코드를 바꾸지 않는다.
- 단계 2~4 는 함께 배포한다. 받기·워커가 읽기 소켓을 `requires` 하므로, 모듈과 코드가 한 판에 있어야 한다.
- 단계 5 는 문서와 시험이라 단계 4 와 같은 배포에 싣는다.
- 회신 두 건은 운영자가 bhu-llm 대장 A18 로 옮긴다.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `transcribe.py` 의 `transcribe_file`·`diarized_blocks` 를 고치는데, 그 안의 기존 한국어 오류 문장(빈 전사 결과, 빈 단어 시각)은 그대로 남는다(헌법 「언어」: 코드에 한글 없음) | 헌법이 기존 한국어의 이전을 별도 변경으로 정했다(1.0.0 언어 절과 Sync Impact Report, 001 plan 의 같은 예외). 이 기능은 떼어 낸 새 함수와 새 코드만 영어로 쓰고, 명령줄의 출력 문장은 바꾸지 않는다(FR-022) | 그 문장을 지금 영어로 바꾸면 명령줄 출력이 바뀌어 FR-022 와 기존 명령줄 시험이 깨진다. 함수를 고치지 않고 엔진에서 같은 설정을 따로 쓰면, 두 경로의 전사 설정이 따로 놀 수 있어 SC-007 이 위험해진다 |
