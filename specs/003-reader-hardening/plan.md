# Implementation Plan: 녹음 읽기 추가 좁히기와 알아차리기

**Branch**: `003-reader-hardening` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-reader-hardening/spec.md` · 시드 `idea/reader-hardening/idea.md` · 002 감사
`specs/002-decoder-isolation/audit/{adversary,qa-engineer}.md`(adversary L1·L5·L6·L8·L9·L11, qa-engineer L1·L2·L3)

## Summary

002 의 일회용 읽기 단위 위에서 녹음 읽기를 음성 해독기로 더 좁히고, 운영자가 기록만으로 알아볼 수 있는 것을 셋 더한다. 계약(스키마·실패 이유·
고정 문장·지우기·재전사·23:58)과 단위 설정은 그대로다. 배포는 한 번이다.

- **US1 해독기 목록**(P1): 읽기 자식이 FFmpeg 의 `codec_whitelist` 를 두 겹으로 건다 — 형식 단계(`avformat_find_stream_info` 의 해독기)와, 해독할 흐름의
  코덱 문맥(PyAV 가 여는 해독기). 길이 재기는 첫 음성 흐름의 해독기 이름을 확인한다. 목록은 음성 해독기 22개다(R1·R2). 이 호스트에서 잰 결과,
  지금은 영상·표지 그림이 든 녹음을 읽을 때 `h264`·`mpeg4`·`mjpeg`·`png`·`wmv2` 해독기가 실제로 열리고, 결정한 방식에서는 하나도 열리지 않으며
  목록 안 녹음의 출력은 바이트까지 같다.
- **US2 잘림 줄**(P2): 워커가 읽기 뒤 `처음 잰 길이 − 해독된 길이 ≥ 1초` 이면 고정된 영어 한 줄을 쓴다(R4·R5). 결과는 그대로 `done` 이다.
- **US3 원인 종류**(P2): `<request_id> failed engine_error <cause>` — 다섯 이름(R6). 녹음 열기를 `RecordingError` 하나로 모으고, 녹음이 없거나 일반 파일이
  아니거나 FIFO 여도 워커가 멈추지 않게 한다.
- **US4 작은 손질**(P3): 감독의 SIGINT 무시(R8), systemd 257 단언(R7), `audio.wav` 를 만든 기술자로 쓰기(R11), 손 실행 안내 한 줄(R9), `-m` 으로
  띄우는 두 자식에 `-I`(R10).

## Technical Context

**Language/Version**: Python — 서버는 Nix 의 python312(3.12.13). 저장소 시험은 3.10·3.11·3.12(001·002 그대로). NixOS 모듈은 Nix.

**Primary Dependencies**: 새 의존성 없음.
- PyAV: 서버 3.12 는 19.0.2(휠의 FFmpeg 9.0.2, libavcodec 63.1.102), 3.11 은 18.1.0, 3.10 은 17.1.0(명령줄 판). 세 판 모두 `container_options` 와
  `CodecContext.options` 의 `codec_whitelist` 가 같게 동작한다(R1 실측, 17·18 은 abi3 휠을 Python 3.11 로).
- systemd: 호스트 262, VM(이 flake 의 nixpkgs) 261.1. 단언은 257 이상(R7).
- VM 시험에만 C 로 된 작은 감시 라이브러리(`pkgs.runCommandCC`, 002 의 `hangingCuda` 와 같은 방식).

**Storage**: 새 자료 없음. `job.json` 의 `duration_s`(이미 있음)를 잘림 판정에 읽는다. `work/<id>/audio.wav` 의 만드는 방식만 바뀐다(0600 그대로).

**Testing**: [quickstart.md](quickstart.md) 의 세 층.
- A pytest: PyAV 로 만든 사인파·단색 영상·단색 표지 그림 녹음과 그 끝을 자른 것, 흉내 읽기 소켓(진짜 감독·자식), 가짜 엔진. GPU·망·systemd·C 컴파일러 없이 돈다.
- B Nix: `checks.x86_64-linux.module-eval`(새 평가 검사), `checks.x86_64-linux.server-vm`(읽기 단위에 해독기 감시 라이브러리를 실은 VM).
- C 호스트: 기준 녹음 회귀, 배포된 읽기 단위로 영상·표지 그림 녹음, 손 실행 안내.

**Target Platform**: NixOS x86_64 서버 miniecoinfo(systemd 262, 커널 6.18.60). 명령줄은 지금처럼 다른 Linux·Windows 에서도 돈다(바뀌지 않음).

**Project Type**: 명령줄 도구 + 시스템 서비스. 단위는 늘지 않는다.

**Performance Goals**: 바뀌지 않는다. 10분 사인파 + 단색 H.264 녹음의 길이 재기 0.09초·해독 0.56초가 목록 유무와 무관하게 같았다(R1).

**Constraints**:
- 계약 불변(FR-015). 응답의 고정 문장도 그대로다 — 원인 이름과 잘림은 운영자 기록에만 있다.
- 운영자 명령줄의 해독은 바꾸지 않는다(FR-005): `open_container` 의 새 키워드는 읽기 자식만 넘긴다.
- 기록 줄에 작업 ID(잘림 줄)·경로·파일 이름·길이 값·오류 문구가 없다.
- 시험 녹음은 사인파·무음·단색 영상과 텍스트 파일뿐(FR-017). 코드에 한글을 쓰지 않는다.

**Scale/Scope**: 이용자 약 14명. 바뀌는 코드는 `stt.audio_decode`, `stt.server.{reader,reader_client,worker}`, `nix/module.nix` 의 단언 하나.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* — 아래는 Phase 1 설계 뒤 다시 확인한 결과다. Phase 0 전의 판정도 같았다.

| 원칙 | 이 설계에서 | 판정 |
|---|---|---|
| I. 최소 역할 | 새 기능·판단·알림 경로가 없다. 해독기 목록은 「받는다」 안의 읽기를 좁힐 뿐이다. 더하는 기록 줄은 셋이고 모두 운영자용이다. **잘림 줄**: 호출하는 쪽은 처음 잰 길이를 받지 않지만 자기 원본으로 같은 비교를 할 수 있으므로 응답에는 아무것도 더하지 않는다. 운영자는 서버 녹음을 열지 않는 규칙(002) 때문에 다른 확인 길이 없어 한 줄만 둔다(002 경계 사례 10 의 줄과 같은 근거). **원인 이름**: 서버 안의 고장 종류라 호출하는 쪽이 결과로 알 수 없다. 응답은 그대로 둔다. **손 실행 안내**: 운영자의 오해를 막는 한 줄이고 동작은 같다 | 통과 |
| II. 교내 처리 | 바뀌는 것이 없다. 읽기 단위는 여전히 망 없이 돈다 | 통과 |
| III. 삭제 보장 | 새 저장 자료가 없다. `audio.wav` 는 「다 지우기」와 겹쳐도 넓은 권한으로 다시 생기지 않는다(R11, FR-012) — 지금의 틈(0640 재생성)을 닫는다. 삭제 경로는 그대로다 | 통과 |
| IV. 원문 무결성 | 목록 안 코덱의 녹음은 길이 재기 값과 해독 바이트가 같다(형식 8가지·기준 녹음 둘 실측, R1). 목록 밖 코덱은 다른 해독으로 대신하지 않고 `unreadable_audio` 로 끝난다 | 통과 |
| V. 말없는 실패 금지 | 말없이 지나가던 것 넷이 드러난다: 끝이 잘린 녹음(줄), 서버 오류의 원인(이름), 낮은 systemd 의 무시된 격리 설정(빌드 거부), 일반 파일이 아닌 녹음에서 워커가 멈추거나 막히던 것(그 작업만 `engine_error`). 잘린 녹음을 `done` 으로 두는 것은 002 경계 사례 10·FR-007 의 운영자 결정이고, 결과의 `audio_duration_s` 가 해독된 길이를 알려 준다. 대체 해독 길은 없다 | 통과 |
| VI. 계약으로만 | 스키마 바이트·실패 이유·고정 문장·경로·지우기·23:58 이 그대로다. 호출하는 쪽에 보이는 바뀜은 「목록 밖 음성 코덱의 녹음이 `unreadable_audio`」 하나이고 이미 있는 이유다 — 회신을 낼지는 운영자 결정 항목(R14). 고칠 내부 문서는 002 contracts/reader.md 의 가리킴 줄뿐이다 | 통과 |
| VII. 테스트 우선 | 과제마다 RED → GREEN 과 닫는 시나리오를 적는다. 해독기 목록은 「영상 흐름의 `pix_fmt is None`·`decode(video=0)` 의 `ArgumentError`」(A)와 VM 감시 라이브러리의 `killed by signal 6`(B)를 먼저 보고 넣는다. systemd 단언은 평가 검사를 먼저 넣어 실패를 본다. 기본 시험은 GPU·망·모델·systemd 없이 돈다. 시험 이름은 `test_hardening_us<N>_<n>_…` 다 | 통과 |
| 저작권·단어장·개인정보 | 단어장 흐름은 그대로다. 새 줄에는 요청자·힌트·녹음 내용이 없다. 잘림 줄은 작업 ID 도 없다. 셸 보간 없음(인자 목록) | 통과 |
| 개발 환경·언어 | Nix + uv, 새 의존성 없음, 무거운 실행은 `captest`, 최종 `project-audit`. 새 코드·로그·시험 이름은 영어다. 문서 대조 시험은 영어 표식만 읽는다. VM 의 C 감시 라이브러리는 시험 전용이다(운영 모듈에 없음) | 통과 |

위반이 없어 Complexity Tracking 은 비어 있다.

## Project Structure

### Documentation (this feature)

```text
specs/003-reader-hardening/
├── spec.md              # 기능 정의
├── plan.md              # 이 문서
├── research.md          # Phase 0 — R1~R14 (실측)
├── data-model.md        # Phase 1 — 해독기 목록·잘림 판정·원인 종류·RecordingError·실패 지도의 바뀐 칸
├── quickstart.md        # Phase 1 — 확인 순서 A·B(B-003-1~6)·C(C-003-1~5)
├── contracts/
│   ├── reader.md        #   읽기 자식·감독·클라이언트·워커 읽기 준비의 바뀜(002 contracts/reader.md 에 더함)
│   ├── operator-log.md  #   운영자 기록의 줄과 docs/server-security.md 의 표식·표·명령
│   └── module.md        #   systemd 단언, module-eval 검사, VM 노드의 감시 라이브러리·시험 녹음
├── checklists/requirements.md
├── audit/               # T-Verify-Final(project-audit) 결과
└── tasks.md             # /speckit-tasks 가 만든다
```

### Source Code (repository root)

```text
src/stt/
├── audio_decode.py      # 바뀜: open_container(..., codec_whitelist=None) — 형식 옵션 + 흐름마다 코덱 문맥 옵션(R1). 명령줄 경로는 그대로
└── server/
    ├── reader.py        # 바뀜: AUDIO_DECODERS·MANUAL_RUN_LINE, DEFAULT_CHILD_COMMAND 에 -I, serve() 의 SIGINT 무시와 손 실행 줄, _open_container 가 목록을 넘김, 길이 재기의 해독기 이름 확인
    ├── reader_client.py # 바뀜: RecordingError(OSError), _open_recording 에 O_NONBLOCK
    └── worker.py        # 바뀜: DEFAULT_ENGINE_CMD 에 -I, TRUNCATION_THRESHOLD_S·TRUNCATION_LINE·ENGINE_ERROR_CAUSES, _prepare 의 녹음 경로, _write_audio(fdopen), 읽기 단계의 예외 → 원인, _settle 의 원인 낱말, 잘림 줄

tests/
├── server/
│   ├── conftest.py              # 바뀜: PyAV 로 사인파 + 단색 영상, MP3 + 단색 PNG 표지, 색인이 앞인 M4A, MP4 안 AC-3 를 만드는 도우미와 끝 자르기
│   ├── fake_decode_child.py     # 바뀜: 동작 sigint-parent
│   ├── test_hardening_us1.py    # 새: US1 — 목록, 영상·표지 그림, 영상만, 목록 밖, 명령줄 그대로
│   ├── test_hardening_us2.py    # 새: US2 — 잘림 줄, 정상 녹음, 묶음 없음, 재전사, 비교 함수의 경계
│   ├── test_hardening_us3.py    # 새: US3 — 다섯 원인, 일반 파일 아님·FIFO, 다음 작업
│   ├── test_hardening_us4.py    # 새: US4 — SIGINT, 0600 경주, 심은 모듈
│   ├── test_server_cli.py       # 바뀜: 끝 코드 2 의 안내 줄(research R9)
│   ├── test_decoder_us3.py      # 바뀜: engine_error 줄 끝의 원인, 녹음 열기 오류의 주입 자리(research R6)
│   └── test_reader_client.py    # 바뀜: 열기 오류의 예외 형 RecordingError, errno 유지(research R6)
└── test_server_security_doc.py  # 바뀜: stt:audio-decoders·stt:engine-error-causes·stt:truncation 표식과 두 줄의 대조

nix/
├── module.nix           # 바뀜: assertions(systemd 257, contracts/module.md §1)
├── module-eval.nix      # 새: 단언의 평가 시험(contracts/module.md §2) — flake.nix 의 checks.x86_64-linux.module-eval
└── vm-test.nix          # 바뀜: 해독기 감시 라이브러리(LD_PRELOAD, VM 노드만), 시험 녹음 10개, 부분 시험 B-003-1~4

flake.nix                # 바뀜: checks.x86_64-linux.module-eval
docs/server-security.md  # 바뀜: §2 해독기 목록, §3 워커의 두 줄·원인 표·손 실행 줄, §4 후속 후보 줄과 4.2 한 항목(contracts/operator-log.md §5)
specs/002-decoder-isolation/contracts/reader.md   # 바뀜: §4~§8 에 이 기능을 가리키는 줄(research R14)
```

**Structure Decision**: 단일 프로젝트 그대로다. 해독기 목록은 읽기 단위의 상수가 있는 `reader.py` 에, 그것을 FFmpeg 에 거는 일은 공유 해독 모듈
`audio_decode.open_container` 에 둔다(받기·워커는 여전히 PyAV 를 불러오지 않는다). 잘림·원인은 처리 단계의 일이라 `worker.py` 에 둔다. 녹음 열기의
예외는 그것을 여는 `reader_client.py` 에 둔다.

## Phase 0 결과 — 주요 결정

| # | 결정 | 근거의 요점 |
|---|---|---|
| R1 | 형식 단계 `codec_whitelist` + 흐름마다 코덱 문맥 `codec_whitelist` + 길이 재기의 해독기 이름 확인. 읽기 자식만 | 🟢 지금은 영상·그림 해독기가 열린다. 형식 단계만으로는 해독 단계(`ac3`·`mp2`)가 열려 있다. 두 겹이면 46개 파일에서 비음성 해독기 0, 목록 안 출력 바이트 같음. PyAV 17·18·19 같음 |
| R2 | 음성 해독기 22개(AAC·ALAC·MP3·FLAC·Vorbis·Opus·WMA 1·2·선형 PCM 12·G.711 2) | 🟢 FFmpeg 이 고르는 해독기 이름을 형식마다 잼(`mp3float`, 내장 `opus`). AC-3·MP2·ADPCM·WMA Pro 등은 이제 거절 |
| R3 | 분석기·형식은 범위 밖, 문서에 남는 범위로 | 🟢 H.264 분석기는 길이 재기에서 2번, 해독 중 21번(버리면 3번). 길이 재기 쪽은 끌 수 없다 |
| R4 | 잘림 기준 1.0초, `job.duration_s` 와 해독된 바이트로 | 🟢 정상 녹음의 최대 양의 차이 0.046초(WMA). 머리가 길이를 적는 형식에서만 잘림이 보인다. 경계 사례 5 의 전제는 생기지 않는다(spec `**Revised**` 2026-11-10) |
| R5 | 고정 줄 `decoded audio is shorter than the probed length`, 읽기 뒤·GPU 전, 작업 ID 없음 | 앞의 `<id> running` 줄로 작업을 안다. VM(GPU 없음)에서도 보인다 |
| R6 | `<id> failed engine_error <cause>`, 다섯 이름. `RecordingError`, `O_NONBLOCK`, 결정적 녹음 경로 | 🟢 녹음 없음은 지금 `StopIteration` 으로 워커가 멈추고, FIFO 는 열기에서 막힌다 |
| R7 | `assertions`: `lib.versionAtLeast config.systemd.package.version "257"`, `module-eval` 검사 | 🟢 `PrivatePIDs=` 가 257(systemd 262 설명서). 판을 바꾼 평가에서 단언이 그 문장으로 실패 |
| R8 | `serve()` 맨 앞 `SIGINT` → `SIG_IGN` | 🟢 PID 1 에서 지금은 `KeyboardInterrupt`, `SIG_DFL` 은 PID 1 에서만 살아남음, `SIG_IGN` 은 둘 다 |
| R9 | 끝 코드 2 일 때 표준 오류에 고정 한 줄 | 감사 L8. 끝 코드 그대로 |
| R10 | 해독 자식·엔진 명령에 `-I` | 🟢 `-m` 은 현재 폴더를 넣어 심은 `json.py` 가 읽혔다(끝 99). `-I` 로 막힘. 주 프로세스는 스크립트라 해당 없음. `-P` 는 3.10 에 없다 |
| R11 | `audio.wav` 를 만든 기술자로 끝까지 | 🟢 지금은 지운 뒤 다시 열면 0640 으로 생긴다. 기술자로 쓰면 다시 생기지 않는다 |
| R12 | A: PyAV 로 만든 시험 녹음과 `pix_fmt`·`ArgumentError`. B: VM 읽기 단위에 `abort()` 감시 라이브러리 | 🟢 시제품: 지금 코드에서 SIGABRT, 결정한 코드에서 없음 |
| R13 | 문서 §2·§3·§4 와 영어 표식, 시험이 상수와 맞춤 | FR-004·FR-016 |
| R14 | 계약 그대로, 002 내부 계약에 가리킴 줄 | 헌법 VI |

**남은 `[VERIFY]`**(모두 quickstart 단계에 들어 있다):
- B-003-1: VM 의 해독기 감시 라이브러리가 읽기 단위의 제한(동적 사용자·`MemoryDenyWriteExecute`·호출 필터·`TemporaryFileSystem`) 안에서 실려 일한다 —
  지금 코드에서 먼저 `killed by signal 6`(RED)를 본다.
- B-003-2: VM 빌드 안에서 `head -c` 로 자른 녹음이 이 세션의 측정처럼 읽힌다.
- B-003-6: CI 의 `nix flake check --no-build` 에서 `module-eval` 평가의 시간·메모리(평가 한 번 최대 약 4.2 GB 실측).

## 이야기별 시험 층

| 이야기 | A (pytest) | B (Nix·VM) | C (호스트) |
|---|---|---|---|
| US1 해독기 목록 | `test_hardening_us1.py` 1~5·경계 1·2·FR-005, 문서 대조(US1-6) | B-003-1(감시 아래 영상·표지 그림·영상만·AC-3), B-P2-8(형식 8가지, 감시 아래) | C-003-3, C-003-2(기준 녹음 같음) |
| US2 잘림 줄 | `test_hardening_us2.py` 1~5·비교 경계·FR-006, 문서 대조 | B-003-2(문서의 확인 명령으로 넷 + 대조 하나) | C-003-2(기준 녹음에 줄 없음), C-003-4(선택) |
| US3 원인 종류 | `test_hardening_us3.py` 1~4·경계 6, 바뀐 `test_decoder_us3.py`·`test_reader_client.py`, 문서 대조 | B-003-3(`reader-not-started` 의 줄, FIFO 녹음) | — |
| US4 작은 손질 | `test_hardening_us4.py`(1·3·5), `test_server_cli.py`(4) | B-003-6 `module-eval`(2), B-003-4(4) | 배포가 단언을 통과(2), C-003-5(4) |

성공 기준: SC-001 → A US1-2·3 + B-003-1 + C-003-3. SC-002 → A US1-1 + B-P2-8 + C-003-2. SC-003 → A US2 + B-003-2 + C-003-2. SC-004 → A US3 + B-003-3.
SC-005 → A US4 + B-003-6. SC-006 → A 전부(3.12·3.11 로컬, 3.10·3.12 CI) + B-003-5 + 스키마 파일 바뀐 줄 0.

## Phase 계획 (tasks 의 뼈대)

| 단계 | 내용(과제마다 RED → GREEN) | 닫는 이야기·요구 |
|---|---|---|
| 1. 시험 도구 | conftest 의 PyAV 시험 녹음 도우미(사인파 + 단색 영상, MP3 + PNG 표지, 색인이 앞인 M4A, AC-3, 끝 자르기), 대역 자식 `sigint-parent` | 아래 단계의 준비 |
| 2. US1 | RED: 영상 흐름 `pix_fmt is None`·`ArgumentError`, AC-3 의 받기 거절, 형식별 같음 → GREEN: `open_container` 의 `codec_whitelist`, `AUDIO_DECODERS`, 길이 재기 이름 확인. 문서 §2 표와 대조 시험(RED → GREEN). VM: 감시 라이브러리와 시험 녹음을 먼저 넣어 B-003-1 의 `killed by signal 6` 을 본 뒤(RED) 코드와 함께 GREEN | US1-1~6, FR-001~005, SC-001·002 |
| 3. US3 | RED: 다섯 원인의 줄, 녹음 없음·디렉터리·FIFO·권한, 다음 작업 → GREEN: `RecordingError`·`O_NONBLOCK`, `_prepare` 의 녹음 경로, 읽기 단계의 예외 나누기, `_settle` 의 원인 낱말. 기존 시험 셋 고침(research R6). 문서 §3 원인 표. VM B-003-3 | US3-1~4, FR-008·009, SC-004 |
| 4. US2 | RED: 잘린 셋·묶음 없음·재전사의 줄, 정상 녹음 0줄, 비교 경계 → GREEN: 비교 함수와 줄. 문서 §3 잘림 절과 대조 시험. VM B-003-2 | US2-1~5, FR-006·007, SC-003 |
| 5. US4 | (a) SIGINT: 하위 프로세스 감독 + `sigint-parent`(RED: 판정 없음·트레이스백) → `SIG_IGN`. (b) `module-eval` 검사(RED) → 모듈 단언. (c) 0600 경주(RED: 다시 생김) → fdopen. (d) 끝 코드 2 의 안내(RED: 빈 표준 오류) → 한 줄, VM B-003-4. (e) 심은 모듈(RED: 읽힘) → `-I`. 문서 §2·§3 의 줄 | US4-1~5, FR-010~014, SC-005 |
| 6. 확인 | §4 후속 후보 줄과 4.2 한 항목, 002 contracts/reader.md 의 가리킴 줄, A 전부(3.12·3.11)·CI(3.10·3.12)·B 전부(`module-eval`, `server-vm`), 스키마 바뀐 줄 0, `T-Verify-Final`(project-audit: qa-engineer + adversary) | FR-015·016·017, SC-006 |
| 7. 배포 | `T-Verify-Final` 통과 뒤 master 합치기·push(운영자 승인) → nixos-config 의 `stt` 입력 갱신 → `nxr`. C-003-1~5. §4 의 배포 커밋·날짜 채우기. 회신은 운영자 결정 | SC-001~003 의 호스트 몫 |

- 단계 2~5 는 서로 기다리지 않지만 같은 파일(`worker.py`·`reader.py`·문서 §3·`vm-test.nix`)을 고치므로 한 줄로 차례대로 한다. 3(US3)을 4(US2)보다 먼저
  하는 까닭: 잘림 줄이 놓일 읽기 단계의 예외 구조를 US3 이 먼저 정한다.
- VM 시험은 무거우므로(60분) 단계마다 돌리지 않고, 2·3·4·5 의 VM 단언을 모은 뒤 한 번 RED, 구현 뒤 한 번 GREEN 을 본다(RED 를 본 기록은 과제에 남긴다).
- 배포는 stt 서비스를 잠깐 다시 띄운다(spec Assumptions). 운영자가 시각을 맞춘다.

## 운영자 결정 항목

1. 해독기 목록(R2) — 이번에 거절되기 시작하는 코덱(AC-3·E-AC-3, `.mp3` 이름의 MP2, ADPCM WAV, WMA Pro·Voice·Lossless, AMR, Speex, GSM) 가운데 받아야 할
   것이 있는가. 권장: 목록 그대로 배포하고, `reader child probe exited with status 1` 이 늘면 그때 이름을 더하는 별도 변경.
2. 해독 단계에서 비음성 흐름을 버리는 것(R3, 두 줄) — 영상 분석기의 호출을 해독 중에서 없앤다. 길이 재기 쪽은 남는다. 권장: 이번에는 넣지 않고 후속 후보.
3. 호출하는 쪽에 한 단락 회신을 낼지(R14) — 실패 이유·문장은 그대로이고 바뀌는 것은 목록 밖 음성 코덱의 녹음이 `unreadable_audio` 가 되는 것뿐이다.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

위반 없음.
