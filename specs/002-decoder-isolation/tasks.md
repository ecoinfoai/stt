---

description: "Task list for 002-decoder-isolation"
---

# Tasks: 녹음 해독 격리

**Input**: `specs/002-decoder-isolation/` — [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/), [quickstart.md](quickstart.md)

**Prerequisites**: plan.md, spec.md (US1–US5, 수용 시나리오 36개, 경계 사례 9개)

**Tests**: 필수(헌법 VII). 이야기마다 시나리오를 시험으로 옮기는 과제가 먼저 오고, 그 시험이 **실패하는 것(RED)을 본 뒤에** 구현 과제가
통과(GREEN)시킨다. pytest 이름은 `test_decoder_us<N>_<n>_...`(수용 시나리오), `test_decoder_edge_<n>_...`(경계 사례), 그 밖은 뜻을 담은 이름.
VM 시험은 `subtest` 제목에 quickstart 단계 이름(B-P1·B-P2-n)을 넣는다.

**Organization**: plan 의 배포 단계를 따른다. US1(P1)은 Python 코드를 바꾸지 않고 단독 배포되므로 읽기 단위의 기반보다 앞에 온다.
읽기 단위의 기반(Phase 3)은 US2~US4 만 막는다. US2~US4 는 한 판으로 배포되므로 한 단계에 모으고, 과제마다 이야기 표시를 단다.

**용어**: spec 의 「읽기 프로세스」 = 읽기 인스턴스(연결 하나에 systemd 가 띄우는 `stt-reader@…` 하나, data-model §1). 「읽기 단위」 = 그 소켓과
템플릿 단위.

**보정 이력**: 2026-11-09 `/speckit-analyze` 지적 21건(D1~D3, E1~E3, C1~C5, F1~F8, B1, A1)을 반영했다.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 다른 파일이고 끝나지 않은 과제에 기대지 않아 함께 할 수 있다
- **[Story]**: 이 과제가 속한 이야기(US1…US5)
- **RED → GREEN**: 시험 과제는 「verify RED」를 적고, 구현 과제는 자신이 GREEN 으로 바꾸는 시험을 적는다. 동작을 바꿀 수 없는 과제(시험 도구·
  문서·배포·호스트 확인)는 「RED 해당 없음 — <이유>」를 적는다(헌법 VII 의 예외)
- **Scenarios**: 과제마다 닫는 시나리오를 적는다(`closes US1-2`). 기반·배포 과제는 받치는 시나리오나 FR 을 적는다(`supports …`)

## 모든 과제에 걸리는 규칙

- **공격 파일 없음**(FR-028): 해독기를 노리는 파일은 만들지도 보관하지도 않는다. 시험 녹음은 `make_wav`·`encode_tone` 의 사인파와 무음뿐이다.
  「나쁜 첨부」는 녹음 확장자를 붙인 텍스트 파일 하나다. 읽기 실패는 [contracts/reader.md](contracts/reader.md) §9 의 시험용 대역으로 만든다.
  대역은 정해진 방식으로 실패하는 가짜이고, 녹음 내용을 해석하지 않는다.
- **대체 길 없음**(헌법 V): 읽기 단위를 쓸 수 없을 때 받기·워커 안에서 직접 해독하지 않는다. 받기·워커는 PyAV 를 불러오지
  않고(T019(e)), GPU 단계는 `ffmpeg` 를 부르지 않는다(T019(f)).
- **코드에 한글 없음**: 새 코드·시험의 식별자·docstring(Google 형식)·주석·메시지는 영어다. 손대는 `transcribe.py` 함수의 기존 한국어 출력
  문장은 그대로 둔다(plan Complexity Tracking, 운영자 승인 2026-11-09, FR-022).
- **문서 대조 시험은 영어 표식만 읽는다**(헌법 언어 규칙): `docs/server-security.md` 는 한국어 문서지만, 시험이 읽는 자리는 표마다 둔 주석 표식
  (`<!-- stt:syscall-table -->`·`stt:versions`·`stt:policy …`·`stt:advisories`·`stt:monthly-checks`), 영어 칸 이름, 영어 상태값(`on`·`off`·`pending`),
  ISO 날짜다(data-model §7·§8). 시험 코드에는 한글 문자열을 쓰지 않는다. 이유 칸 같은 자유 문장은 한국어로 쓰고, 시험은 비어 있는지만 본다.
- **Python 3.10**: CI 가 3.10·3.12 를 돈다. `tomllib`·`datetime.UTC`·`enum.StrEnum` 을 쓰지 않는다.
- **로그**: 받기·워커는 001 FR-037 범위(작업 ID·상태·이유·시각)만 쓴다. 읽기 단위의 자식은 로그를 쓰지 않고, 감독은 FR-023 덧붙임의 고정된 영어 한 줄만 쓴다(FR-019).
- **셸 없음**: 하위 프로세스는 인자 목록으로만 띄운다. 운영 코드에 시험용 분기를 두지 않는다 — 자식 명령·소켓 경로·시간 값은 인자로 받는다.
- **실행**: 무거운 실행은 모두 `captest` 로 돌린다. pytest: `captest 4G 20m uv run python -m pytest -q`(xdist 없음, `-n` 없이).
  VM: `captest 8G 60m nix build .#checks.x86_64-linux.server-vm -L`. 찍힌 최대값을 보고 상한을 맞춘다.
- **배포·push 는 운영자 승인 뒤에만**. 커밋은 과제 하나 또는 논리 묶음마다(Conventional Commits, 영어).

---

## Phase 1: Setup — 기준과 관측 (호스트, 운영자와)

**Purpose**: P1 을 걸기 전의 기준 결과와, 필터 예외를 정할 관측 근거를 모은다(research R2).

- [X] T001 C-0 기준 결과: 운영자와 함께 `clip60.m4a` 와 1시간 녹음을 1차(힌트·화자 구분 없음)·2차(힌트·화자 구분)로 서버에서 전사하고,
  `transcript.txt` 와 `result.json` 의 `paragraphs` 를 운영자의 기기(저장소 밖)에 보관한다. 보관한 날과 위치만
  `specs/002-decoder-isolation/handoff/p1-reply.md` 초안에 적는다. RED 해당 없음 — 기준 수집. supports US1-5, SC-007
- [X] T002 C-P1-1 관측: quickstart C-P1-1 의 순서대로 네 단위에 `SystemCallLog=~@system-service` 를 런타임 drop-in 으로 걸고, 1차·2차 전사, 텍스트
  첨부 하나, 「다 지우기」, 청소 1분, `systemctl restart stt-models` 를 거친 뒤 `journalctl -k -g 'type=1326'` 을 모은다. 호출 번호는
  `scmp_sys_resolver` 로 이름을 찾고, 단위·호출·쓰는 곳·관측한 날을 `docs/server-security.md` 초안의 §1 표 자리에 적는다. 끝나면 drop-in 을
  지운다. RED 해당 없음 — 관측. supports US1-3, FR-002, 경계 사례 7
- [X] T003 [P] `docs/server-security.md` 뼈대(한국어): §1 단위별 제한·예외, §2 녹음 읽기 격리, §3 작업 실패와 단위 비정상 종료 구별, §4 판 기록·
  갱신 정책·보안 공지·매달 확인 기록의 제목과 칸 머리만 둔다. 표마다 위 규칙의 영어 표식과 칸 이름(data-model §7·§8)을 둔다. RED 해당 없음 — 문서 뼈대. supports FR-002, FR-023, FR-024

---

## Phase 2: User Story 1 - 녹음을 다루는 단위가 쓸 수 있는 커널 기능을 좁힌다 (Priority: P1) 🎯 MVP

**Goal**: 네 단위에 `@system-service` 필터, `native` 아키텍처, 쓰기·실행 메모리 금지를 건다. 예외는 T002 에서 관측한 것만, 이유와 함께.

**Independent Test**: 이 단계만 배포한 뒤 `systemctl show` 로 네 단위의 값을 보고, 기준 녹음의 1차·2차 결과를 T001 과 비교한다.

### Tests for User Story 1 (write first) ⚠️

- [X] T004 [US1] `nix/vm-test.nix` 의 `isolation settings` 부분 시험에 B-P1-1 단언을 더한다: `server` 노드의 `stt-intake`·`stt-worker`·`stt-sweep`,
  `fetcher` 노드의 `stt-models` 에서 `SystemCallArchitectures=native`, `SystemCallFilter=` 값이 비어 있지 않고 `~` 로 시작하지 않음,
  `MemoryDenyWriteExecute` 가 [contracts/module.md](contracts/module.md) §1 의 값. 시험 끝에 B-P1-3 단언 — 두 노드의 `journalctl -k -g type=1326`
  이 비어 있음. verify RED: VM 시험이 새 단언에서 실패한다(지금은 `SystemCallFilter=~`·`SystemCallArchitectures=`). closes US1-1, US1-2(VM)
- [X] T005 [P] [US1] `tests/test_server_security_doc.py`(새): `nix/module.nix` 에서 `SystemCallFilter` 를 가진 모든 단위를 이름으로 모은다(공통
  `hardening` 묶음을 쓰는 단위는 그 묶음의 값을 받고, 나중에 생길 템플릿 `stt-reader@` 도 같은 방식으로 잡힌다). 모은 단위를
  `docs/server-security.md` 의 `stt:syscall-table` 표와 대조한다: (1) 네 단위(`stt-intake`·`stt-worker`·`stt-sweep`·`stt-models`)와 모은 단위마다
  표에 줄이 있다. (2) `@system-service` 밖에 더한 허용은 표의 `added` 와 같고, 그런 줄은 `reason`·`observed` 가 비어 있지 않다. (3) `~` 로 시작하는
  항목은 표의 `narrowed` 와 같다(좁힘이라 관측 근거는 묻지 않는다). (4) `mdwe` 가 모듈의 `MemoryDenyWriteExecute` 와 맞고(호스트 판정 전의
  `pending` 은 모듈 값이 켬일 때만 받는다), `off` 이면 `mdwe_evidence` 가 비어 있지 않다. 표식·영어 칸 이름으로만 읽고 시험 코드에 한글을 쓰지
  않는다. 문자열로 읽는다(`tomllib`·Nix 평가 없음). verify RED: 모듈에 필터가 없고 표가 비어 있어 (1)에서 실패한다.
  closes US1-3, US1-4(저장소 쪽)

### Implementation for User Story 1 (turn T004–T005 GREEN)

- [X] T006 [US1] `nix/module.nix` 의 `hardening` 묶음에 `SystemCallArchitectures = "native"`, `SystemCallFilter = [ "@system-service" ]`(+ T002 에서
  관측한 예외만, 줄마다 `docs/server-security.md` §1 의 줄을 가리키는 영어 주석), `MemoryDenyWriteExecute = true` 를 더한다.
  `SystemCallErrorNumber` 는 두지 않는다(R1). 다른 001 설정은 건드리지 않는다. RED → GREEN: turns T004 GREEN(기존 부분 시험도 필터를 건 채 모두 통과 —
  B-P1-2). closes US1-1, US1-2, US1-6(VM)
- [X] T007 [US1] `docs/server-security.md` §1 표를 채운다(네 단위: 필터, 더한 예외와 이유·관측한 날·호출 번호, 쓰기·실행 메모리 금지의 상태) —
  워커는 `mdwe=pending` 으로 두고 T009 에서 `on`·`off` 로 확정한다. §3 에 R15 의 확인 방법을 적는다(작업 실패 = 응답의 `failure.reason`·단위 로그,
  단위 비정상 종료 = `systemctl --failed`·`systemctl show -p Result -p ExecMainCode -p ExecMainStatus`·`journalctl -k -g 'type=1326'`).
  RED → GREEN: turns T005 GREEN. closes US1-3, US1-4, supports FR-023

### Deploy P1 (운영자 승인 뒤)

- [X] T008 [US1] P1 배포: 운영자 승인을 받아 이 브랜치의 P1 까지의 커밋(문서 + T004~T007)을 master 에 합치고 push 한다. 운영자가
  nixos-config 의 `stt` 입력을 갱신하고 `nxr` 한다. 배포한 커밋과 날을 `specs/002-decoder-isolation/handoff/p1-reply.md` 에 적는다.
  P1 합치기는 기능 브랜치를 마치는 것이 아니다 — 설정만 바뀌는 중간 배포라 운영자 승인으로 합치고, 최종 감사(T040)가 P1 의 바뀜도 함께 본다.
  RED 해당 없음 — 배포. supports US1-1~6
- [X] T009 [US1] C-P1-2 호스트 확인(운영자와): [contracts/module.md](contracts/module.md) §4 의 첫 `systemctl show` 출력을 그대로
  `handoff/p1-reply.md` 에 싣는다. T001 의 두 녹음을 같은 두 방식으로 다시 전사해 전사록·문단 목록을 비교한다(다르면 운영자가 받아들인 뒤에만
  완료). 워커가 쓰기·실행 메모리 금지로 CUDA 에서 거부되면 워커만 `false` 로 바꾸고 거부 지점을 §1 에 적고(`mdwe=off`), `nix/vm-test.nix` 의
  T004 단언도 그 값으로 고쳐 T004·T005 를 다시 GREEN 으로 만든 뒤 T008 을 되풀이한다. 거부가 없으면 `mdwe=on` 으로 적는다. 받기의 받아들임·거절, 「다 지우기」, 청소, 모델 받기가 평소대로 끝나고 `journalctl -k -g 'type=1326'` 이 비어 있음을
  본다. RED 해당 없음 — 호스트 확인. closes US1-1, US1-2, US1-4, US1-5, US1-6(호스트), 경계 사례 7
- [X] T010 [US1] C-P1-3 회신 `specs/002-decoder-isolation/handoff/p1-reply.md`: data-model §9 의 P1 열 — 확인 결과, 요청 1(단위별 필터·
  아키텍처·예외와 이유·쓰기·실행 메모리), 요청 2·3(지금 상태와 P2 계획), 요청 4(지금 판과 날짜, FR-024 를 먼저), §5 질문, 질문 6(보관 기간,
  R17), 남는 위험(P1 만 배포된 동안의 같은 계정·같은 저장 위치·GPU 단위의 해독). 운영자에게 넘긴다. RED 해당 없음 — 회신 문서.
  closes SC-010(P1 몫), supports FR-027

**Checkpoint**: P1 이 배포돼 있고 회신이 운영자에게 넘어갔다. 이후 단계는 P1 과 상관없이 진행한다.

---

## Phase 3: Foundational — 읽기 프로토콜과 읽기 단위의 코드 (US2~US4 를 막는다)

**Purpose**: [contracts/reader.md](contracts/reader.md) 의 감독·해독 자식·호출하는 쪽과 명령줄. 받기·워커는 아직 바꾸지 않는다.

**⚠️ CRITICAL**: 이 단계가 끝나야 Phase 4 를 시작한다.

### Tests (write first) ⚠️

- [X] T011 [P] 시험 도구: `tests/server/fake_reader.py`(새, 읽기 인스턴스 자리의 대역 — 동작은 [contracts/reader.md](contracts/reader.md) §9 의
  「읽기 인스턴스 전체」 줄 그대로)와 `tests/server/conftest.py` 의 흉내 읽기 소켓 장치. 장치는 임시 SEQPACKET 소켓에서 연결을 받을 때마다 지정한
  명령(진짜 감독 `python -m stt server read` 또는 대역)을 새 프로세스로 띄우고 그 연결을 표준 입력으로 준다 — systemd `Accept=yes` 와 같다.
  띄운 횟수와 각 프로세스가 받은 기술자 수를 시험이 볼 수 있게 남기고, 시험이 끝나면 남은 프로세스가 없음을 단언한다. RED 해당 없음 — 시험
  도구. supports US2-6, US2-7, FR-028
- [X] T012 [P] `tests/server/test_reader.py`(새) — 감독. 해독 자식 자리에는 [contracts/reader.md](contracts/reader.md) §9 의 「해독 자식」 대역을
  인자로 넣는다. 단언: (a) §2 에 어긋나는 요청(모르는 키, 틀린 형, 범위 밖 `limit_s`, 기술자 개수·종류가 틀림)에 `{"started": false}` 를 보내고
  자식을 띄우지 않는다. (b) 정상 요청에 `{"started": true}`. (c) 자식이 끝 코드 0 이고 결과가 규칙에 맞을 때만 `ok`, 그 밖의 모든 끝은
  `failed`(§5-6). (d) 호출하는 쪽이 끊으면 자식이 죽고 거둬지며, 감독은 더 보내지 않고 끝난다. (e) `limit_s` 가 지나면 자식이 죽고 `failed`.
  (f) 감독 프로세스가 표준 라이브러리 밖의 모듈(`av` 등)을 불러오지 않는다. (g) 감독이 표준 출력·오류에 아무것도 쓰지 않는다.
  (h) `stt server read` 는 표준 입력이 연결 소켓이 아니면 끝 코드 2. verify RED: `stt.server.reader` 가 없어 ImportError.
  closes US2-7(프로토콜 쪽), supports US3-2~5, FR-009, FR-011, FR-015, FR-019
- [X] T013 [P] `tests/server/test_reader.py` — 진짜 해독 자식. `encode_tone` 의 사인파(M4A·MP3·ADTS·Ogg·FLAC)와 `make_wav` 로: (a) probe 의 길이가
  지금 `probe_duration` 과 같다 — 실제 파일의 길이는 하위 프로세스 자식으로 잰다(지금 `tests/server/test_intake.py` 의
  `test_us5_10_formats_of_the_schema_extensions_are_measured`·`test_probe_duration_reads_container_duration` 의 기대값). 「컨테이너 길이가 없으면
  첫 오디오 흐름의 패킷 길이 합」은 자식이 쓰는 길이 고르기 순수 함수를 프로세스 안에서 가짜 컨테이너로 시험한다(지금
  `test_probe_duration_sums_packet_durations_without_container_duration` 을 옮김, reader.md §6). (b) 녹음 확장자를 붙인 텍스트 파일 →
  `failed`. (c) 허용목록 밖 형식의 사인파 파일 → `failed`, 허용된 확장자를 붙여도 같음(경계 사례 9, 지금
  `test_us5_10_formats_outside_the_list_are_unreadable`·`test_us5_10_format_outside_the_list_is_refused_even_with_a_listed_extension`). (d) decode 출력이 `faster_whisper.audio.decode_audio(path)` 의 `int16`
  값과 바이트까지 같다(형식마다). (e) 자식은 경로를 받지 않고 기술자만으로 읽는다(인자에 경로가 없음). verify RED: 모듈 없음.
  closes US3-1(프로토콜 쪽), 경계 사례 9, supports SC-007, FR-006
- [X] T014 [P] `tests/server/test_reader_client.py`(새) — 호출하는 쪽, T011 의 장치와 대역으로. 다섯 결과의 경계(§7): 접속 실패·「시작」 전
  끊김·`started: false`·시작 시간 넘김 → `unavailable`. 「시작」 뒤의 판정 없는 끊김·`failed`·규칙에 어긋난 판정·판정 시간 넘김·홀수 바이트 출력·
  `MAX_DECODED_BYTES` 를 넘는 출력 → `failed`. 작업이 `running` 이 아니게 됨 → `stopped`(연결과 파이프를 닫았음을 단언). 작업 상한 → `timeout`.
  녹음이 기호 링크이면 접속하기 전에 오류(`O_NOFOLLOW`). 시간 값은 시험에서 짧게 주입한다. verify RED: 모듈 없음.
  supports US3-2~5, US3-8, FR-015, FR-016, FR-018
- [X] T015 [P] `tests/server/test_server_cli.py` 에 더한다: `stt server read` 가 등록돼 있고, `stt server intake`·`stt server work` 가
  `--reader-socket` 을 받으며 기본값이 `/run/stt-reader.sock` 이다. verify RED: 인자 파싱 오류. supports FR-006

### Implementation (turn T012–T015 GREEN)

- [X] T016 `src/stt/server/reader.py`(새): [contracts/reader.md](contracts/reader.md) §4 상수(여기 한 번만), §5 감독(표준 라이브러리만, 자식 명령은
  인자로 받고 기본값은 진짜 자식), §6 해독 자식(`python -m stt.server.reader child probe|decode <녹음 fd> <출력 fd>`, PyAV 는 자식 안에서만
  불러온다. 길이 고르기는 열린 컨테이너를 받는 순수 함수). `AUDIO_FORMATS` 는 여기에만 둔다 — 같은 과제에서 `src/stt/server/intake.py` 의
  `_AUDIO_FORMATS` 를 지우고 `reader.AUDIO_FORMATS` 를 불러 쓰게 한다(`probe_duration` 은 T027 까지 받기에 남는다). RED → GREEN: turns T012,
  T013 GREEN, 기존 받기 시험 그대로 통과.
  supports US2-7, US3-1~5, FR-006, FR-009, FR-011, FR-019
- [X] T017 `src/stt/server/reader_client.py`(새): `ReaderConfig`(소켓 경로·시작 시간·판정 여유·`poll_s`), `probe()`·`decode()` 와 다섯 결과,
  §7 의 열기·검사 규칙. RED → GREEN: turns T014 GREEN. supports US3-2~5, US3-8, FR-015, FR-016, FR-018
- [X] T018 `src/stt/server/cli.py`: `stt server read`(→ `reader.serve`), `intake`·`work` 의 `--reader-socket` → `ReaderConfig`. 받기·워커에는 아직
  넘기지 않는다(T025·T027). RED → GREEN: turns T015 GREEN. supports FR-006

**Checkpoint**: 읽기 프로토콜이 pytest 에서 진짜 감독·진짜 자식·대역으로 돈다. 받기·워커는 그대로다.

---

## Phase 4: User Story 2·3·4 — 읽기 격리, 실패 지도, 계약과 삭제 (Priority: P2)

**Goal**: 받기의 길이 재기와 워커의 해독을 읽기 단위로 옮기고, GPU 단계는 `work/<id>/audio.wav` 만 받게 한다. 실패 이유는 data-model §5 대로
정하고, 계약·삭제·명령줄은 그대로 둔다.

**Independent Test**: pytest 에서 흉내 소켓(진짜 감독 또는 대역)과 가짜 엔진으로 US2·US3·US4 시나리오를 돌린다. systemd 위의 격리는 Phase 5.

### Tests for User Story 2·3·4 (write first) ⚠️

- [X] T019 [P] [US2] `tests/server/test_decoder_us2.py`(새), T011 의 장치 + 진짜 감독 + 가짜 엔진(`--record`): (a) US2-5 — 엔진이 받은
  `spec.json` 에 `audio_path` 가 없고, 엔진이 연 파일은 `work_dir/audio.wav` 하나이며 그 WAV 는 16 kHz 모노 16-bit 이고 0600 이다(SC-003).
  (b) US2-6 — 작업 둘의 해독은 연결 둘(프로세스 둘)이고, 연결마다 녹음 기술자가 하나다. (c) US2-8 — 정상 첨부와 텍스트 첨부가 든 받기 한 번이
  진짜 감독을 거쳐 응답 둘을 10초 안에(벽시계 — 「직후」 1분보다 넉넉히 짧게) 낸다. (d) 경계 사례 1·2 — 1차가 대기 중일 때 그 녹음을 가리킨 2차와, 길이 재기와 해독이 겹칠 때 각 읽기가
  다른 연결이다. (e) 하위 프로세스에서 `stt.server.intake`·`stt.server.worker` 를 불러온 뒤 `av` 가 `sys.modules` 에 없다(대체 길 없음).
  (f) 진짜 `stt.server.engine` 을 가짜 모델로, `ffmpeg` 가 없는 PATH 와 부르면 실패하도록 바꾼 `diarize.to_wav` 로 돌려도 화자 구분 2차까지
  끝난다(GPU 단계는 `ffmpeg` 를 부르지 않는다, research R7). US2-1~4 의 권한과 US2-9 의 처리 시간은 Phase 5·8 에서 본다. verify RED: 받기·워커가
  아직 읽기 단위를 쓰지 않아 연결 수 0 · `audio_path` 있음 · 받기가 `av` 를 불러옴 · 엔진이 `to_wav` 를 부름. closes US2-5, US2-6, US2-8(pytest),
  경계 사례 1·2, supports FR-010
- [X] T020 [P] [US3] `tests/server/test_decoder_us3.py`(새), [contracts/reader.md](contracts/reader.md) §9 의 대역으로 실패 방식마다 작업 하나:
  (a) US3-1 — 텍스트에 녹음 확장자를 붙인 첨부는 받기에서 `unreadable_audio`, 같은 묶음의 정상 요청은 `queued`. (b) US3-2 — 받기의 길이 재기에서
  「시작」 뒤 실패하는 대역 → 그 요청만 `unreadable_audio`, 그 요청의 녹음 사본과 기록이 남지 않으며(지금 `tests/server/test_intake.py` 의
  `test_probe_failure_leaves_no_audio_copy_and_no_record` 를 이리로 옮김), 같은 묶음의 다른 요청은 처리된다. (c) US3-3·4·5 — 처리 단계의 해독에서 오류·판정
  없는 끝·상한 초과에 해당하는 대역 → 그 작업은 `unreadable_audio`, 결과 없음, 워커는 계속 돈다. (d) US3-6 — 각 실패 바로 뒤의 정상 작업이 `done`.
  (e) US3-7 — 읽기는 성공하고 가짜 엔진이 GPU 메모리 부족·GPU 없음·모델 없음·판정 없음을 내면 이유가 지금 분류 그대로. (f) US3-8 — 읽기 단위를
  띄우지 못함(소켓 없음, `started: false`) → 처리에서는 `engine_error` 와 고정 문장 「The server could not start the audio reader.」, 받기에서는
  001 FR-032a(로그에 ID·이유, inbox 에서 치움, 응답 없음). (g) US3-9 — 모든 실패의 `failure.detail` 이 data-model §5 의 고정 문장과 같다.
  (h) 경계 사례 4 — 작업 상한이 읽기 상한보다 먼저 오면 `processing_timeout`, 반대면 `unreadable_audio`(시간 값 주입). (i) 경계 사례 5 — WAV 를
  쓰는 중 저장 오류(쓰기 함수에 `OSError` 주입) → `unreadable_audio`, 남은 파일 0. verify RED: 지금 경로는 읽기 단위를 쓰지 않는다.
  closes US3-1~9, 경계 사례 4·5, SC-004, SC-005(pytest)
- [X] T021 [P] [US4] `tests/server/test_decoder_us4.py`(새): (a) US4-1 — `src/stt/server/schemas/*.json` 이 `specs/001-server-transcription/contracts/`
  와 바이트까지 같다(기존 `tests/server/test_contract.py` 가 이미 보면 그 시험을 가리키는 것으로 갈음하고 새로 쓰지 않는다). (b) US4-2 — 한 녹음에
  1차·자동 2차·재전사 3번, 작업 5개가 모두 `done` 이고 읽기 연결이 5개다(경계 사례 6: 작업마다 새로 해독). (c) US4-3 — 그 뒤 「다 지우기」 →
  `leftovers()` 가 0. (d) US4-4 — 멈춰 있는 읽기 대역을 붙잡아 둔 채 「다 지우기」 → 워커가 `stopped` 로 연결을 닫고, 대역 프로세스가 끝나며,
  파일 0. (e) US4-5 — 같은 상태에서 23:58 정리 → 같음. (f) US4-6 — 같은 상태에서 워커 프로세스를 죽이고 청소 → `interrupted`, `work/` 비어 있음.
  (g) SC-008 — 정상·실패로 끝난 작업 뒤에도 `audio.wav` 가 0개. US4-7 은 명령줄 시험(`tests/test_cli*.py`·`tests/test_transcribe.py`·
  `tests/test_diarize.py` — 단언은 그대로이고 빈 `.m4a` 대역은 T044 가 실제 무음 WAV 로 바꿨다)과 `tests/test_cli_integration.py::
  test_main_gives_the_model_decoded_samples_not_a_path` 로 본다. verify RED: 읽기 연결 수 0, 대역이 띄워지지 않음.
  closes US4-1~6, 경계 사례 6, SC-006, SC-008(pytest)
- [X] T022 [US2] 기존 시험의 단언을 새 내부 경계로 고친다(research R11, 파일마다 까닭을 커밋 메시지에): `tests/server/test_engine.py:104`·
  `tests/server/test_us1.py:201` 은 spec 의 `audio_path` 대신 `audio.wav`, `tests/server/test_us3.py:80`·`:103` 은 2차가 1차의 녹음을 읽는지를 읽기
  연결이 받은 기술자의 inode 로, `tests/server/test_us5.py:147`·`:163` 은 길이를 주입하는 자리를 흉내 소켓의 대역으로. `tests/server/test_intake.py`
  의 길이 재기 시험은 T013(길이·형식)과 T020(b)(사본 없음)로 옮긴 뒤 지운다. `tests/server/conftest.py` 도우미의 `ReaderConfig` 연결은 이 과제에서
  하지 않는다 — 워커·받기가 그 인자를 받기 전에 넣으면 기존 서버 시험이 한꺼번에 인자 오류로 깨져 T019~T021 의 RED 이유가 가려진다(T025·T027 에서
  한다). verify RED: 고친 단언이 지금 코드에서 실패한다(`audio_path` 있음, 읽기 연결 없음). supports US2-5, US4-7, SC-007
- [X] T023 [P] [US2] `tests/test_transcribe.py`·`tests/test_diarize.py` 에 더한다: `transcribe_file`·`diarized_blocks`(`src/stt/transcribe.py:745`·
  `:871`)에서 떼어 낼 「해독된 음성을 받는」 두 함수가, 같은 사인파에서 지금 두 함수와 같은 결과(가짜 모델로 넘긴 인자·빔·VAD·힌트·단어 시각,
  화자 배정)를 낸다. 지금 두 함수의 기존 시험은 바뀌지 않는다. verify RED: 함수 없음. supports US4-7, FR-010, FR-022, SC-007

### Implementation for User Story 2·3·4 (turn T019–T023 GREEN)

- [X] T024 [US2] `src/stt/transcribe.py`: `transcribe_file` 의 모델 호출 부분을 float32 샘플을 받는 함수로, `diarized_blocks` 의 `to_wav` 다음 부분을
  WAV 경로를 받는 함수로 떼어 내고, 지금 두 함수는 그 함수를 부르게 한다. 새 함수의 docstring 은 영어다. 기존 한국어 오류 문장은 바꾸지 않는다 — 새 함수로 옮겨 가도 같다(plan Complexity Tracking, 운영자
  승인 2026-11-09).
  RED → GREEN: turns T023 GREEN, 기존 명령줄 시험 그대로 통과. supports US4-7, FR-022
- [X] T025 [US2] `src/stt/server/worker.py`: 읽기 단계 — `reader_client.decode()` 로 받은 바이트를 `wave` 로 `work/<id>/audio.wav`(0600)에 쓴다.
  `spec.json` 에서 `audio_path` 를 뺀다. 작업 상한(`deadlines.processing_limit`)을 읽기 단계 시작부터 재고 GPU 단계에 남은 시간을 준다.
  `run()` 이 `ReaderConfig` 를 받는다. 같은 커밋에서 `tests/server/conftest.py` 의 `run_worker`·`run_worker_once` 가 흉내 소켓의 `ReaderConfig` 를
  저절로 넘기게 한다 — 다른 호출부는 고치지 않는다(R14). RED → GREEN: turns T019 의 워커 쪽, T021(b)~(g), T022 의 워커 쪽 GREEN.
  closes US2-5, US2-6, US4-2~6. 메모: US4-4~6 시험은 받기가 길이 재기 연결을 띄우는 T027 에서 GREEN 이 된다(시험이 연결 2개를 기대한다).
- [X] T026 [US3] `src/stt/server/worker.py`: data-model §5 의 처리 열 — `unavailable` → `engine_error`(고정 문장), `failed` → `unreadable_audio`,
  `timeout` → `processing_timeout`, `stopped` → 로그만. 엔진에서 받는 이유에서 `unreadable_audio` 를 빼고, 엔진이 그 이유를 내면 판정 없음으로
  보아 `engine_error`. RED → GREEN: turns T020(c)~(i) GREEN. closes US3-3~9, 경계 사례 4·5
- [X] T027 [US3] `src/stt/server/intake.py`: 길이 재기를 `reader_client.probe()` 로 바꾸고 `probe_duration` 을 지운다(`_AUDIO_FORMATS` 는 T016 에서
  옮김). `failed` → `unreadable_audio`(지금 문장), `unavailable` → 예외를 올려 001 FR-032a 의 요청별 처리로. `run_once` 가 `ReaderConfig` 를 받고,
  같은 커밋에서 `tests/server/conftest.py` 의 받기 도우미가 흉내 소켓의 `ReaderConfig` 를 저절로 넘기게 한다. 받기는 PyAV 를 불러오지 않는다.
  RED → GREEN: turns T019(c)(e), T020(a)(b)(f), T022 의 받기 쪽 GREEN. closes US2-8, US3-1, US3-2, US3-8(받기)
- [X] T028 [US2] `src/stt/server/engine.py`: `work_dir/audio.wav` 만 읽고 머리(1채널·2바이트·16 kHz)가 다르면 `engine_error`. 전사는 int16 →
  float32 / 32768.0 으로 T024 의 샘플 함수에, 화자 구분은 T024 의 WAV 함수에 넘긴다. `classify_error` 와 `_DETAILS` 에서 `unreadable_audio`
  를 지운다. RED → GREEN: turns T019(a)(f), T022 의 엔진 쪽 GREEN. closes US2-5, US3-7, SC-003, supports FR-010
- [X] T029 [US4] 전체 시험: `captest 4G 20m uv run python -m pytest -q` 가 모두 통과한다(`tests/` 전체, 기존 시험 포함). 찍힌 최대 메모리를 커밋 메시지에 적는다.
  RED 해당 없음 — 확인. supports SC-007, US4-7

**Checkpoint**: 서버 경로의 모든 녹음 읽기가 읽기 프로토콜을 지나고, pytest 가 US2·US3·US4 의 pytest 몫을 모두 닫는다.

---

## Phase 5: User Story 2·3·4 — 모듈과 VM 시험 (Priority: P2)

**Goal**: 읽기 소켓·템플릿 단위를 모듈에 넣고, 진짜 systemd 위에서 격리·권한·실패·삭제를 본다(quickstart B-P2).

**Independent Test**: `captest 8G 60m nix build .#checks.x86_64-linux.server-vm -L` 의 B-P2 부분 시험이 모두 통과한다.

### Tests (write first) ⚠️

- [X] T030 [US2] `nix/vm-test.nix` 에 B-P2-1~3 부분 시험: (1) [contracts/module.md](contracts/module.md) §4 의 읽기 단위·소켓 조회 값이 §2 의 표와
  같다 — `MemorySwapMax=0`·`LimitCORE=0`·`KillSignal`·`CollectMode`·`PrivateTmp` 포함(FR-012 의 스왑·덤프 없음). (2) 2초 사인파 job 이 읽기 단계를 지나 `gpu_unavailable` 로 끝나고, 001 VM 시험의 `hangingCuda` 로 엔진을 붙잡아 둔 동안
  `work/<id>/audio.wav`(16-bit 모노 16 kHz)가 있고 `spec.json` 에 `audio_path` 가 없다 — 받기·워커가 자기 샌드박스에서 소켓에 접속했음도 이것으로
  성립한다(B-P2-2). (3) 기존 `corrupt.mp3` 와 정상 녹음을 같은 묶음으로 맡기면 텍스트는 `unreadable_audio`, 정상은 `queued`, `systemctl --failed`
  에 stt 단위가 없다. verify RED: 읽기 단위가 없어 조회와 job 이 실패한다. closes US2-5(VM), US3-1(VM), SC-004, supports FR-012, 해결 `[VERIFY]` B-P2-1·B-P2-2
- [X] T031 [US2] `nix/vm-test.nix` 에 B-P2-4 권한 점검: specialisation 이 읽기 단위의 `ExecStart` 를 권한 점검 대역으로 바꾼다. 대역은 quickstart
  B-P2-4 목록의 자원(쓰기 가능한 자리의 자료 쓰기 포함)을 열어 보고, 성공 수, 자기가 받은 녹음 기술자를 읽을 수 있었는지, 자기 UID 를 판정 자리에
  담아 돌려준다. VM 에는 장치 번호만 같은 가짜 `/dev/nvidia0`·`nvidiactl`·`nvidia-uvm` 노드를 먼저 만든다. 요청자가 다른 작업 A·B 와 A 의 재전사
  A′ 를 두고, A 의 읽기와 A′ 의 읽기에서 각각 돌린다. 단언: 모든 성공 수 0. 두 읽기 모두 자기 녹음 기술자는 읽힘(US2-2 — A′ 는 공유 녹음에
  기술자로만 닿고 A 의 결과·힌트·해독된 음성에는 닿지 못함). 길이 재기와 해독을 겹쳐 돌린 두 읽기의 UID 가 모두 `stt` 와 다르고, 각각 `/proc` 에서 자기 인스턴스의 프로세스만 봄(대역이 보이는 프로세스 수를 보고). 각 읽기가 끝난 뒤
  `systemctl list-units --all 'stt-reader@*'` 가 비어 있고 호스트 `/tmp`·`/var/tmp` 에 그 인스턴스의 사설 폴더가 없음. 대역은 녹음 내용을 해석하지
  않는다. verify RED: 읽기 단위가 없다. closes US2-1, US2-2, US2-3, US2-4, US2-6, US2-7(VM, 정상 끝), 경계 사례 2, SC-002,
  해결 `[VERIFY]` B-P2-4
- [X] T032 [US3] `nix/vm-test.nix` 에 B-P2-5 실패 대역: [contracts/reader.md](contracts/reader.md) §9 의 VM 줄을 specialisation 으로 하나씩 넣는다.
  단언: 받기에서는 그 요청만 거절되거나 치워지고, 처리에서는 그 작업만 `unreadable_audio`(띄우지 못함은 `engine_error`)이며, 바로 뒤의 정상
  작업이 `gpu_unavailable` 까지 가고, `systemctl --failed` 에 stt 단위가 없으며(단, `started: false` 대역에서는 받기의 요청 하나가 처리되지 못해
  `stt-intake.service` 가 끝 코드 1 로 끝나 `--failed` 에 오른다 — 001 FR-032a, 운영자 결정 2026-11-08 유지. 이 경우에도 다른 단위는 없고, 다른 세 대역은
  모든 단위가 없다), 각 실패 뒤 `stt-reader@*` 인스턴스가 남지 않는다.
  verify RED: 읽기 단위가 없다. closes US3-2, US3-3, US3-4, US3-5, US3-6, US3-8(VM), US2-7(VM, 실패·강제 종료), SC-005
- [X] T033 [US4] `nix/vm-test.nix` 에 B-P2-6·7: 멈추는 대역으로 읽기를 붙잡아 둔 채 (1) 「다 지우기」 → 1분 안에 `stt-reader@*` 인스턴스가 없고 그
  작업의 파일 0, (2) VM 시계를 23:58 로 옮겨 청소 → 같음, (3) `systemctl stop stt-worker` → 인스턴스가 끝나고 청소 뒤 `interrupted`, `work/` 비어
  있음. 그리고 `journalctl -u 'stt-reader@*'` 에 작업 ID 가 하나도 없다. verify RED: 읽기 단위가 없다. closes US4-4, US4-5, US4-6(VM),
  SC-008(VM), supports FR-013, FR-019

### Implementation (turn T030–T033 GREEN)

- [X] T034 [US2] `nix/module.nix`: `stt-reader.socket`(§2.1)과 `stt-reader@.service`(§2.2) — [contracts/module.md](contracts/module.md) 의 표 그대로.
  받기·워커에 `wants`·`after` `stt-reader.socket` 과 `--reader-socket /run/stt-reader.sock`(§3). VM 에서 `RestrictAddressFamilies=none` 의 단위
  파일 꼴이 거부되면 원인을 적고 운영자와 대안을 정한다(말없이 바꾸지 않는다). RED → GREEN: turns T030–T033 GREEN. closes US2-1~4, US2-6, US2-7, US4-4~6(VM)
- [X] T035 [US2] `docs/server-security.md`: §1 표에 `stt-reader@` 줄(`narrowed` 에 더 좁힌 항목, `reason` 에 그 까닭, `mdwe=on`), §2 에 읽기 격리
  (누가 무엇을 읽는가, 무엇에 닿지 못하는가, 상한, 끊김에 바로 멈춤), §3 에 읽기 단위를 쓸 수 없을 때 보이는 것 — 워커 로그의
  `<request_id> failed engine_error`, 받기 로그의 그 요청 ID 와 이유(001 FR-032a, 응답 없음), `systemctl status stt-reader.socket` — 과 배포 메모
  (소켓 단위는 설정이 바뀔 때만 다시 뜨고, 그동안 들어온 요청·작업은 위와 같이 끝난다 — 운영자 결정 2026-11-08 그대로). RED → GREEN: T005 가
  모듈에서 `stt-reader@` 를 찾아 표와 대조하므로 T034 뒤에 RED 가 되고 이 과제로 GREEN 이 된다. closes SC-001(저장소 쪽), supports FR-002,
  FR-008, FR-023

**Checkpoint**: VM 시험이 P1·P2 의 모든 부분 시험을 통과한다.

---

## Phase 6: User Story 5 - 녹음을 여는 구성 요소의 판과 갱신 정책을 기록한다 (Priority: P3)

**Goal**: `docs/server-security.md` §4 에 판 기록, 갱신 정책, 매달 확인 기록표를 둔다(research R16).

**Independent Test**: 문서를 열어 서버 경로의 해독 구성 요소가 빠짐없이 판·고정한 곳·날짜와 함께 있고, `tests/test_server_security_doc.py` 가 통과한다.

### Tests for User Story 5 (write first) ⚠️

- [X] T036 [US5] `tests/test_server_security_doc.py` 에 더한다(영어 표식·칸 이름으로만 읽음): (a) `stt:versions` 의 `av` 줄(Python 3.12·3.10 둘)의
  `version` 이 `uv.lock` 과 같다. (b) nixpkgs 줄의 `version`(고정 커밋)이 `flake.lock` 과 같다. (c) 줄마다 `version`·`pinned_in`·`since`·
  `last_updated` 가 차 있고 날짜는 ISO 꼴이다. (d) `stt:policy` 표식에 `owner=operator`·`cadence=monthly`·`advisory_days=7`·볼 곳 셋
  (`ffmpeg-security`·`pyav-releases`·`nixpkgs-ffmpeg`)이 있다. (e) `stt:monthly-checks` 에 줄이 하나 이상이고, 첫 달과 마지막 달 사이에 빠진 달이
  없다(US5-4). (f) `stt:advisories` 의 줄마다 `updated` 가 `published` + 7일 이하이거나 `reason` 이 차 있다(US5-5, 줄이 없으면 통과).
  (g) `role=decodes-recordings` 줄마다 `comparison`(기준 녹음을 비교한 날)이 `last_updated` 와 같거나 뒤다(US5-3). 모두 기록된 날짜끼리의 비교이고
  오늘 날짜를 쓰지 않는다(R16). 문자열로 읽는다(3.10 에 `tomllib` 없음). verify RED: §4 가 뼈대뿐이라 표식 안의 줄이 없다.
  closes US5-1, US5-2, US5-3, US5-4, US5-5(기록 쪽), SC-009

### Implementation for User Story 5 (turn T036 GREEN)

- [X] T037 [US5] `docs/server-security.md` §4: 구성 요소 표(data-model §8 의 칸, 서버 경로에서 녹음을 해석하는 것은 PyAV 휠의 FFmpeg 하나), 정책
  (FR-025 그대로 — 매달 확인, 새 판이 있을 때만 올림, 보안 공지 뒤 7일 안에 올리거나 이유를 적음, 휠이 아직 없을 때의 적는 법, 올리는 순서:
  잠금 파일 → 빌드·시험 → 배포 → 기준 녹음 비교 → 표 고치기)과 `stt:policy` 표식, 보안 공지 기록표(첫 확인에서 본 공지가 있으면 그 줄), 매달
  확인 기록표(첫 줄은 이 기능을 쓰는 날의 확인). 들어온 날·마지막 갱신일은 `git log -S` 로 찾은 커밋과 날짜이고, `av` 줄의 `comparison` 은 T009 의
  기준 녹음 비교 날이다(T042 에서 고친다). RED → GREEN: turns T036 GREEN. closes US5-1, US5-2, US5-4, US5-5, supports US5-3, FR-024~026

- [X] T044 [US5] 첫 매달 확인(2026-11-09)의 보안 공지 5건을 닫는 갱신(운영자 결정 2026-11-10): `uv lock --upgrade-package av` 로 Python 3.12
  이상을 PyAV 19.0.2(FFmpeg 9.0.2)로 올린다. PyAV 19 는 `av.open` 의 `metadata_errors` 를 없앴고 faster-whisper 1.2.1 의 `decode_audio` 가 그것을
  써서 깨지므로, 명령줄도 녹음을 faster-whisper 에 경로로 넘기지 않고 stt 의 해독(읽기 단위와 같은 방식, 결과 바이트가 같음)으로 얻은 표본을
  넘긴다(FR-022: 명령줄 출력은 그대로). `docs/server-security.md` §4 의 `av` 줄·보안 공지·매달 확인 기록을 고치고, 기준 녹음 두 개의 s16 해독
  결과를 PyAV 18 과 19 로 `cmp` 비교한다(정책의 올리는 순서 (4)). RED → GREEN: 잠금 파일만 올린 상태에서 읽기·명령줄 시험이 실패하는 것을 본 뒤
  고친다. VM 시험으로 읽기 단위의 호출 필터·쓰기·실행 메모리 금지 아래에서 FFmpeg 9 해독을 확인한다. closes US5-3(갱신 쪽), supports FR-022, FR-025, FR-026

**Checkpoint**: 판 기록과 정책이 한 문서에 있고, 잠금 파일과 어긋나면 CI 가 실패한다.

---

## Phase 7: Polish & 최종 확인

**Purpose**: 내부 계약 문서·README 를 맞추고, P2·P3 배포 전에 최종 감사를 돈다. 최종 감사는 기능 브랜치를 마치기 전에 한 번 돈다(헌법
「최종 검증」) — 이 기능에서 브랜치를 마치는 것은 P2·P3 를 master 에 합치는 T041 이다. P1 합치기(T008)는 설정만 바뀌는 중간 배포라 운영자
승인으로 하고, T040 이 P1 의 바뀜도 함께 본다. plan 의 단계표도 감사를 P2 배포 앞으로 옮겼다.

- [X] T038 [P] 001 의 내부 계약 문서를 [contracts/module.md](contracts/module.md) §5 대로 고친다: `specs/001-server-transcription/contracts/module.md`
  §3(읽기 단위 줄, 공통 세 설정), `contracts/README.md` §2(그림에 `/run/stt-reader.sock`), `contracts/cli.md` §1(`stt server read`, `--reader-socket`).
  스키마 파일은 건드리지 않는다. RED 해당 없음 — 내부 문서. supports FR-020, US4-1
- [X] T039 [P] `README.md` 의 Server 절에 읽기 단위 한 줄과 `docs/server-security.md` 링크를 더한다(영어). RED 해당 없음 — 문서. supports FR-023
- [ ] T040 T-Verify-Final: `project-audit` 스킬로 최종 감사 — 범위 qa-engineer(7-Layer 모듈·통합·E2E) + adversary(20+ 페르소나 + silent-skip).
  adversary 는 코드와 설정만 읽고, 입력 파일을 만들거나 돌리지 않는다(FR-028, 001 T078 의 교훈). BOUNDARY_MISMATCH / INTEGRATION_MISSING /
  SILENT_SKIP_NEW / PERSONA_ATTACK_SUCCESS 가 나오면 고칠 때까지 T041 로 가지 않는다. 결과를 `specs/002-decoder-isolation/audit/` 에 남긴다.
  RED 해당 없음 — 감사(지적마다 RED → GREEN 과제를 이 파일 끝에 더한다). supports SC-001~010

---

## Phase 8: P2·P3 배포와 회신 (운영자 승인 뒤, bhu-llm FR-659 전)

- [ ] T041 P2·P3 배포: T040 이 통과한 뒤 운영자 승인을 받아 master 에 합치고 push, 운영자가 nixos-config 의 `stt` 입력을 갱신하고 `nxr` 한다. 배포한
  커밋과 날을 `specs/002-decoder-isolation/handoff/p2-reply.md` 에 적는다. RED 해당 없음 — 배포. supports US2~US5
- [ ] T042 C-P2-1~7·C-P3 호스트 확인(운영자와, quickstart C-P2): 설정 조회 세 명령 출력을 그대로 싣고, 인계 §6 의 정상 + 텍스트 첨부, 3시간 사인파의
  해독 시간·최대 메모리(`captest 1G 10m`, 기대 48초·153 MB 이하 — 넘으면 상한을 고치는 과제를 더한다), 한 녹음에 작업 5개와 「다 지우기」, 기준
  녹음 비교(먼저 `cmp` 로 s16 비교, 다르면 운영자가 받아들인 뒤에만 완료), 1시간 녹음의 처리 시간과 접수 확인 시간, (선택) 도는 인스턴스의 `/dev`
  와 UID. §4 의 첫 매달 확인 줄과 `av` 줄의 `comparison` 을 이날로 고친다(T036 이 GREEN 인지 다시 본다). 결과를 `handoff/p2-reply.md` 에 적는다. RED 해당 없음 — 호스트 확인.
  closes US2-8, US2-9, US4-2, US4-3, US4-7, US5-3, US5-4(호스트), 경계 사례 3, SC-001, SC-004, SC-006, SC-007, 해결 `[VERIFY]` C-P2-3·C-P2-5·C-P2-6
- [ ] T043 C-P2-8 회신 `specs/002-decoder-isolation/handoff/p2-reply.md`: data-model §9 의 P2 열 — 다섯 단위 확인 결과와 권한 점검, 요청 1(읽기 단위
  덧붙임), 요청 2·3(배포된 격리), 요청 4(정책과 첫 매달 확인), §5 질문, 남는 위험. 운영자에게 넘긴다. RED 해당 없음 — 회신 문서.
  closes SC-010, supports FR-027

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1(T001·T002) → Phase 2(US1). T003 은 언제든.
- Phase 2 의 배포(T008~T010)는 Phase 3 이후와 상관없이 진행한다 — P1 은 `nix/module.nix` 의 `hardening` 만 바꾼다.
- Phase 3(기반) → Phase 4 → Phase 5. Phase 4·5 는 한 판으로 배포된다(받기·워커가 읽기 소켓을 `wants` 한다).
- Phase 6(US5)은 T003 뒤 언제든 할 수 있고, Phase 8 의 배포에 함께 싣는다. T005 의 시험 파일을 T036 이 넓히므로 T005 뒤다.
- Phase 7 은 Phase 4~6 이 끝난 뒤. Phase 8 은 T040 통과 뒤.

### Within Each Phase

- 시험 과제(RED 확인) → 구현 과제(GREEN) → 다음.
- 같은 파일의 과제는 차례로: `worker.py`(T025 → T026), `intake.py`(T016 → T027), `tests/server/conftest.py`(T011 → T025 → T027),
  `nix/vm-test.nix`(T004, T030 → T033), `nix/module.nix`(T006 → T034), `docs/server-security.md`(T003 → T007 → T035 → T037),
  `tests/test_server_security_doc.py`(T005 → T036).
- T024(`transcribe.py`) → T028(`engine.py`).
- T009 에서 워커의 쓰기·실행 메모리 금지가 거부되면 T004·T006·T007 을 고치고 T008 을 되풀이한다.

### User Story Dependencies

- US1: Phase 1 뒤. 다른 이야기에 기대지 않는다.
- US2·US3·US4: Phase 3 뒤. 서로 시험은 함께 쓸 수 있지만 구현은 `worker.py` 를 나눠 쓴다.
- US5: 코드에 기대지 않는다. 판 기록의 내용(서버 경로의 해독기가 하나)은 US2 의 설계를 따른다.

## Parallel Opportunities

- Phase 1: T003 은 T001·T002 와 함께.
- Phase 2: T004(VM)와 T005(pytest)를 함께.
- Phase 3: T011~T015 모두 함께(서로 다른 파일 — T012·T013 은 같은 파일이므로 한 사람이 차례로).
- Phase 4: T019·T020·T021·T023 함께. 구현은 T024·T025 함께(다른 파일), 그 뒤 T026 → T027(`conftest.py` 를 T025 와 나눠 씀), T028 은 T024 뒤.
- Phase 7: T038·T039 함께.

### Parallel Example: Phase 4

```text
# 시험을 함께 쓴다(모두 RED 확인):
T019 tests/server/test_decoder_us2.py
T020 tests/server/test_decoder_us3.py
T021 tests/server/test_decoder_us4.py
T023 tests/test_transcribe.py + tests/test_diarize.py

# 서로 다른 파일의 구현을 함께:
T024 src/stt/transcribe.py   T025 src/stt/server/worker.py + tests/server/conftest.py
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 → Phase 2(US1) → 배포(T008) → 호스트 확인(T009) → 회신(T010).
2. **멈추고 확인**: 네 단위에 필터가 있고 기준 녹음 결과가 같다. 시범 기간의 남는 위험이 하나 줄었다.

### Incremental Delivery

1. P1 = US1(시범 기간, 단독 배포).
2. P2·P3 = Phase 3~6 을 한 판으로, 최종 감사(T040) 뒤 배포 — bhu-llm 이 실제 이용자에게 열기(FR-659) 전.
3. 회신 두 건(T010, T043)을 운영자가 bhu-llm 대장 A18 로 옮긴다.

## Notes

- 시나리오 ↔ 과제: US1-1·2 → T004·T006·T009 · US1-3 → T005·T007 · US1-4 → T005·T007·T009 · US1-5 → T001·T009 · US1-6 → T006·T009 ·
  US2-1~4 → T031·T034 · US2-5 → T019·T025·T028·T030 · US2-6 → T019·T031 · US2-7 → T012·T031·T032·T033·T034 · US2-8 → T019·T027·T042 · US2-9 → T042 ·
  US3-1 → T013·T020·T030 · US3-2 → T020·T032 · US3-3~6 → T020·T026·T032 · US3-7 → T020·T028 · US3-8 → T020·T026·T027·T032 · US3-9 → T020·T026 ·
  US4-1 → T021 · US4-2·3 → T021·T042 · US4-4~6 → T021·T033 · US4-7 → T023·T024·T029·T042 · US5-1·2 → T036·T037 · US5-3 → T036·T037·T042 ·
  US5-4·5 → T036·T037·T042 · 경계 사례 1·2 → T019(2 는 T031 도), 3 → T042, 4·5 → T020, 6 → T021, 7 → T002·T009, 8 → 기존 명령줄 시험(T029), 9 → T013.
- 남은 `[VERIFY]` 의 자리: B-P2-1·B-P2-2 → T030, B-P2-4 → T031, C-P1-1 → T002, C-P1-2 → T009, C-P2-3·C-P2-5·C-P2-6 → T042.
- 규칙의 시험: 「받기·워커는 PyAV 를 불러오지 않는다」→ T019(e), 「GPU 단계는 `ffmpeg` 를 부르지 않는다」→ T019(f)·T028, 「문서 대조는 영어
  표식만」→ T005·T036.
- spec 이 바뀌면 spec 을 먼저 고치고 과제를 고친다(전역 §3.5).
- 과제 하나 또는 논리 묶음마다 커밋(Conventional Commits, 영어).

---

## Phase 9: 최종 감사(T040) 지적 고치기 (운영자 결정 2026-11-10, T041 전)

감사 결과: `audit/qa-engineer.md`(PASS, Medium 1), `audit/adversary.md`(FAIL, High 2·Medium 3). 운영자 결정(2026-11-10): High 2건과 권장
항목(qa M1, adversary M1·M2·M3, 문서 표현)을 모두 고친다. 깨진 조각이 있는 녹음은 지금처럼 그 앞까지 전사하고 `done` 으로 끝내되 숨기지 않는다.
읽기 단위가 누구나 읽을 수 있는 호스트 파일을 읽고 그 내용을 자기 전사록으로 내보낼 수 있다는 남는 위험을 그 표현으로 다시 받아들인다.

- [X] T045 spec·계약 먼저: `spec.md` 에 경계 사례 10(녹음 가운데 깨진 조각 — 그 앞까지 전사해 `done`, 해독이 일찍 멈췄다는 고정된 한 줄이
  서버 기록에 남는다), US2-10(장악된 읽기 프로세스는 호스트의 `/run`·`/var`·`/nix/var` 아래 파일을 열거나 잠그지 못한다), FR-023 덧붙임(읽기 단위의
  감독 프로세스는 자식이 비정상으로 끝나거나 해독이 일찍 멈추면 작업 ID·경로·해독기 문구 없는 고정된 영어 한 줄을 기록에 쓴다)을 더한다.
  `contracts/reader.md` §5.7 과 :83(「건너뛴다」→ 첫 깨진 조각에서 멈춘다), `contracts/module.md`(읽기 단위 설정, 닫힌 `[VERIFY] B-P2-1` 표식),
  `research.md` R12(호스트 잠금 파일의 이유)를 고친다. RED 해당 없음 — spec. supports US2-10, 경계 사례 10, FR-023
- [X] T046 [US2] 호스트 잠금 파일 가리기(adversary H1): RED — `nix/vm-reader-double.py`·`nix/vm-test.nix` 의 권한 점검 대상에 0644 root 파일
  `/run/nixos/switch-to-configuration.lock`·`/nix/var/nix/db/` 아래 0644 파일(VM 에 없으면 만든다)을 더해 열기·`flock -n` 이 실패해야 한다고
  단언한다(지금은 성공해 실패). GREEN — `stt-reader@` 가 `/run`·`/var` 를 빈 읽기 전용 tmpfs 로 덮고 `/nix/var` 를 가린다(`DynamicUser` 의 사설
  `/tmp`·`/var/tmp` 와 함께 되는지 `[VERIFY]` VM — 안 되면 `/var` 는 그 아래 디렉터리를 가리는 쪽으로 하고 이유를 R12 에 적는다). 가리는 경로는
  모두 `-` 를 붙여 없는 경로로 단위가 실패하지 않게 한다. 기존 해독 VM 시험이 GREEN 인지(Python·PyAV 가 뜨는지) 다시 본다. closes US2-10
- [X] T047 [US3] 깨진 조각에서 멈춘 해독을 드러내기(adversary H2): RED — 녹음 파일 없이, 한 번 `InvalidDataError` 를 내는 가짜 프레임 열로
  `src/stt/audio_decode.py` 가 그 앞까지의 표본을 쓰고 「일찍 멈춤」을 알린다고 단언한다. 읽기 자식은 그 알림을 감독에게 넘기고 감독은 `ok` 로 답하며
  T048 의 한 줄을 쓴다. 명령줄은 표준 오류에 영어 경고 한 줄을 낸다. GREEN — 구현, `# intentional-skip:` 표식과 참된 이유(faster-whisper 와
  같은 동작, 첫 깨진 조각에서 멈춤), docstring. 깨끗한 녹음의 바이트 동일성 시험은 그대로 GREEN. closes 경계 사례 10
- [X] T048 [US3] 읽기 단위의 비정상 종료 기록(adversary M1): RED — 자식 대역이 끝 코드 1 로 끝남·신호(SIGSYS 31)로 죽음·일찍 멈춤을 알림의 세
  경우에, 감독이 표준 오류에 고정된 영어 한 줄씩만 쓰고(작업 ID·경로·해독기 문구 없음) 판정은 그대로임을 단언한다. GREEN — `src/stt/server/reader.py`.
  supports FR-023
- [X] T049 [US2] 형식별 격리 해독 확인(qa M1): VM 에서 사인파로 만든 녹음 한 개씩을 `AUDIO_FORMATS` 의 형식마다(m4a/AAC, mp3, wav, flac, ogg
  Vorbis·Opus, ADTS aac, asf/WMA) 실제 읽기 소켓으로 해독해 `ok` 이고 `journalctl -k -g 'type=1326'` 가 비어 있음을 단언한다(FR-028 — 사인파만).
  RED 해당 없음 — 확인 시험. 어느 형식이 막히면 원인을 고치는 과제를 더한다. supports US2-8, SC-007
- [X] T050 문서(adversary M2·M3, qa 표현): `docs/server-security.md` §2 남는 위험을 「누구나 읽을 수 있는 호스트 파일을 읽고 그 내용을 자기 작업의
  전사록으로 내보낼 수 있다(느림, 다른 이용자의 녹음·전사록은 닿지 않음)」로 고치고 운영자의 다시 받아들임(2026-11-10)을 적는다. §3 에 「서버 녹음을
  명령줄·`ffprobe`·`ffmpeg` 로 직접 열지 않는다」 규칙, T048 의 기록 줄과 그때 볼 것(`type=1326` 과 시각으로 받기·워커 기록을 맞춰 보기), 한 형식이
  늘 `unreadable_audio` 일 때 볼 것을 더한다. §4 의 `av (Python 3.12)` 를 「3.12 이상」으로, T021 의 「그대로」 표현을 고친다. RED 해당 없음 — 문서.
  supports FR-023, FR-027
- [ ] T051 T-Verify-Fix: `project-audit` 로 T045~T050 의 바뀜만 다시 감사(qa-engineer + adversary, adversary 는 코드·설정만 읽음). High 이상이
  없어야 T041 로 간다. 결과를 `audit/` 에 더한다. RED 해당 없음 — 감사.

### 재감사(T051) 뒤 마무리 (운영자 결정 2026-11-10)

재감사 결과: `audit/reaudit-qa-engineer.md`(PASS, Medium 2·Low 8), `audit/reaudit-adversary.md`(PASS, Medium 3·Low 7). 운영자 결정(2026-11-10):
읽기 단위에서 `/mnt`·`/srv`·`/opt` 도 가린다(호스트의 자료 디스크 `/mnt/hdd-3_6t` 등).

- [ ] T052 [US2] 자료 디스크 가리기(reaudit RM2·RL1·RL2): RED — VM 권한 점검 대상에 `/mnt`·`/srv`·`/opt` 아래의 0644 root 파일(VM 에서 만든다)을
  더해 열기·잠그기가 실패해야 한다고 단언한다(지금은 성공해 실패). GREEN — `stt-reader@` 의 `InaccessiblePaths` 에 `-/mnt`·`-/srv`·`-/opt`·`-/media`
  를 더한다. `docs/server-security.md` §2 남는 위험에 「`/mnt`·`/srv`·`/opt` 는 가렸다, 남는 것은 `/etc`·`/nix/store`·`/usr` 의 누구나 읽을 수 있는
  파일」과 운영자 결정(2026-11-10)을 적고, US2-10 과 R12 에 같은 경로를 더한다. closes US2-10
- [ ] T053 [US3] 메모리 상한에서의 기록(reaudit RM1, qa L2): `stt-reader@` 에 `OOMPolicy=continue` 를 두어 자식이 메모리 상한으로 죽어도 감독이 살아
  `failed` 를 보내고 `killed by signal 9` 줄을 쓴다. RED/GREEN — VM 에서 단위 설정을 단언하고, 생산 코드에 시험용 길을 더하지 않고 감독 아래 메모리를
  쓰는 무해한 자식을 띄울 수 있으면 그 줄이 기록에 오는지 `[VERIFY]` 한다(못 하면 그 이유를 R12 에 적는다). §3 에서 「유일한 흔적」을 지우고, 확인
  명령에 systemd 자신의 인스턴스 줄(`oom-kill`·시간 상한)을 더한다(systemd 262 의 문구 `[VERIFY]` VM). supports FR-023
- [ ] T054 [US3] `decode_samples` 의 일찍 멈춤 알림을 빠뜨릴 수 없게(reaudit RM3): RED — 알림 받는 인자 없이 부르면 `TypeError` 라고 단언한다.
  GREEN — 그 인자를 키워드 필수로 하고, 상관없는 시험은 명시적으로 넘긴다. 명령줄 경고에 파일 이름을 넣는다(qa L8, RL4). closes 경계 사례 10
- [ ] T055 [US3] 경계 사례 10 의 끝 결과 시험(reaudit qa M1): RED — 시험용 읽기 대역이 「일찍 멈춤」(끝 코드 3)과 그 앞까지의 표본을 돌려줄 때 워커를
  거친 작업이 `done` 으로 끝나고 전사가 그 표본만 다루며, 첫 프레임에서 멈추면 빈 전사의 `done` 임을 `test_decoder_edge_10_*` 로 단언한다(녹음 파일은
  사인파·무음만). GREEN — 시험이 드러낸 것만 고친다. closes 경계 사례 10
- [ ] T056 문서·시험 정리(reaudit qa M2·L1·L3~L5, RL3·RL5~RL7): `quickstart.md` 의 B-P2-1·B-P2-3·B-P2-4 와 C-P2 단계, T042 문구에 새 설정(`/run`·`/var`
  덮개, 가린 경로)과 감독의 기록 줄 확인을 더한다. `docs/server-security.md`·`contracts/module.md` 의 「두 겹」을 「설정은 두 번, 실제로는 tmpfs 가
  가린다」로, §3 에 가장 흔한 줄(`probe exited with status 1`)의 뜻, `type=1326` 보다 감독 줄을 믿을 것, 운영자의 안전한 재확인 방법(VM 의
  `readFormats` 처럼 `stt` 로 `reader_client` 를 거쳐 기술자만 넘기는 명령)을 적는다. 감독 줄은 경고 수준으로 쓴다(`<4>` 앞붙임 또는 `SyslogLevel`,
  RL5). VM 의 「줄 없음」 확인이 늦게 온 줄을 놓치지 않게 하고(L4), 가짜 자식의 `--count 0` 을 고친다(L5). RED 해당 없음 — 문서·시험(RL5 는 RED 먼저).
  supports FR-023, FR-027
- T043 에 더함: 회신은 `done` 결과가 녹음 앞부분만일 수 있음(경계 사례 10)과 그 확인 방법을 bhu-llm 에 알리고, `p1-reply.md:104` 의 「밖으로 빼낼 길이
  없다」를 바로잡는다(reaudit qa L6, T040 adversary M2).
