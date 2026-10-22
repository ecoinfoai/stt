---

description: "Task list for 001-server-transcription"
---

# Tasks: 교내 서버 전사

**Input**: `specs/001-server-transcription/` — [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/), [quickstart.md](quickstart.md)

**Prerequisites**: plan.md, spec.md (US1–US5, 수용 시나리오 40개, 경계 사례 8개)

**Tests**: 필수(헌법 VII, NON-NEGOTIABLE). 이야기마다 시나리오를 시험으로 옮기는 과제가 먼저 오고, 그 시험이 **실패하는 것(RED)을 본 뒤에**
구현 과제가 통과(GREEN)시킨다. 시험 이름은 `test_us<N>_<n>_...`(수용 시나리오), `test_edge_<n>_...`(spec 경계 사례 n번째 줄), 그 밖은 뜻을 담은 이름.

**Organization**: 이야기별 단계. 기반(Phase 2)이 끝나야 이야기를 시작한다.

**보정 이력**: 2026-10-22 `/speckit-analyze` 지적 36건과 보정 중 발견 1건(research R19·R20), 두 번째 분석 지적 38건(research R21)을 반영했다. 병합 뒤 운영자 결정(research R22 — 삭제 단순화·GPU 전용)은 Phase 10 이다.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 다른 파일이고 끝나지 않은 과제에 기대지 않아 함께 할 수 있다
- **[Story]**: 이 과제가 속한 이야기(US1…US5)
- **RED → GREEN**: 시험 과제는 「verify RED」, 구현 과제는 자신이 GREEN 으로 바꾸는 시험을 적는다. 동작을 바꿀 수 없는 과제(설정 선언·시험 도구·
  문서·확인)는 「RED 해당 없음 — <이유>」를 적는다(헌법 VII 의 예외)
- **Scenarios**: 과제마다 닫는 시나리오를 적는다(`closes US1-2`). 기반·배포 과제는 받치는 시나리오나 FR 을 적는다(`supports …`)

## 모든 과제에 걸리는 규칙

- **코드에 한글 없음**(헌법 언어 규칙, research R19): 새 파일(`src/stt/server/**`, `src/stt/gpu_lock.py`, `tests/server/**`, `tests/test_gpu_lock.py`,
  `tests/test_cli_gpu_lock.py`, `nix/**`)과 기존 시험 파일에 더하는 새 시험의 식별자·docstring(Google 형식)·주석·메시지·시험 자료는 영어다.
  이 기능이 고치는 기존 함수는 docstring·주석을 영어로 바꾸되, **그 함수가 지금 내는 사용자 출력 문자열은 그대로 둔다**(FR-040·SC-013).
  새로 쓰는 메시지는 영어다.
- **한국어 산출물은 함수로 비교**: `화자N`·전사록 머리글은 기존 코드가 만든다. 시험은 글자를 적지 않고 `diarize.speaker_label("SPEAKER_00")`·
  `transcribe.build_header(...)` 를 불러 비교한다(R16). 가짜 전사 문장은 영어로 쓴다.
- **Python 3.10**: CI 가 3.10·3.12 를 돈다. `datetime.UTC`·`enum.StrEnum`·`typing.Self`·`tomllib` 등 3.11 이후의 것을 쓰지 않는다(`timezone.utc`).
- **로그**: stdout/stderr 에는 `request_id`·상태·실패 이유·시각만(FR-037). 전사문·힌트·요청자 표식은 어디에도 출력하지 않는다.
- **detail**: `invalid_request` 는 `<JSON 경로>: <규칙마다 정한 고정 문구>`, 다른 이유는 이유마다 고정 문장. 500자 이하, 요청 값·예외 원문·
  내부 경로를 싣지 않는다(data-model §2.4·§9).
- **셸 없음**: 하위 프로세스는 인자 목록으로만 띄운다(`shell=True` 금지).
- **시험은 GPU·망·모델 없이**: 무거운 라이브러리는 엔진 안에서만 늦게 불러오고, 시험은 가짜 모델·가짜 엔진·주입한 시계를 쓴다(R15).
  운영 코드에 시험용 분기를 두지 않는다 — 엔진 명령·시계·폴링 간격·처리 시간 상한 함수·GPU 잠금 파일은 인자로 받는다.
- **시각**: 계약 시각은 UTC `YYYY-MM-DDTHH:MM:SSZ`, 자정만 `Asia/Seoul`(R7).
- 검증 명령: `uv run python -m pytest -q`(개발 셸, `uv sync --locked --extra server` 뒤).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 의존성·패키지 뼈대·시험 도구

- [X] T001 `pyproject.toml`: 판 `0.3.0`, 선택 묶음 `server = ["jsonschema>=4.18"]`, `[tool.setuptools.package-data] "stt.server" = ["schemas/*.json"]`
  를 더하고 `uv lock` 으로 `uv.lock` 을 갱신한다(R6). RED 해당 없음 — 의존성·패키지 자료 선언(휠에 실리는지는 T005(g)가 시험한다). 확인:
  `uv sync --locked --extra server` 성공, 기존 243개 시험 통과 — supports SC-013
- [X] T002 [P] `src/stt/server/__init__.py`(영어 모듈 docstring 한 줄)와 `src/stt/server/schemas/{request,response,result}.schema.json` 을
  `specs/001-server-transcription/contracts/` 에서 바이트 그대로 복사한다. RED 해당 없음 — 계약 사본(T005 가 바이트 일치를 시험). 확인: `cmp` 세 번 일치 — supports SC-012
- [X] T003 [P] `tests/server/__init__.py`(빈 파일)와 `tests/server/conftest.py`. RED 해당 없음 — 시험 도구. 담을 것:
  `root`(tmp 폴더), `FakeClock`(`now() -> datetime`(UTC aware), `advance(seconds)`, `set(iso)`), `make_wav(path, seconds, silent=True) -> Path`
  (표준 라이브러리 `wave`, 16kHz mono 16bit), `new_id() -> str`(소문자 uuid4), `write_transcribe_request(inbox, *, request_id, requested_by,
  attachment: Path | None, from_request=None, language="ko", diarize=False, hints=None, delete_audio="after_job", mode=0o640) -> Path` 와
  `write_delete_request(...)` — contracts/README §3 순서(첨부 먼저, `.json.tmp` 로 쓴 뒤 이름 바꾸기), `leftovers(root) -> list[Path]`(녹음 파일 폴더
  `audio/<id>/`, `work/`·`queue/` 아래 파일, 게시 중간 폴더 `outbox/.*.tmp/`, 어디든 `.*.tmp` 임시 파일, `*.wav`, job 기록에 남은 `hints` — R21-2),
  **`run_worker(layout, clock, mode="done", **kwargs)`**·**`run_worker_once(...)`**(fake_engine 으로 `worker.run`·`worker.run_one` 을 부르는 도우미 —
  워커 인자가 늘면 이 도우미만 고친다), **`gpu_present`**(엔진의 `cuda_device_count`·`torch_cuda_available` 을 「있음」으로),
  **`pinned_models`**(tmp `HF_HOME` 에 `models.PINNED` 의 `refs/main` 을 써 두고 환경 변수를 둠). 모듈은 도우미·fixture 안에서 불러온다(아직 없어도
  수집이 깨지지 않게). 새 시험 파일 이름은 `tests/*.py` 와 겹치지 않게 한다. 확인: `uv run python -m pytest -q tests/server` 수집 오류 없음 — supports 모든 이야기
- [X] T004 [P] `.github/workflows/ci.yml` 의 설치 단계를 `uv sync --locked --extra server` 로 바꾼다(R15). RED 해당 없음 — CI 설정 — supports SC-013

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 계약 검사·시각 계산·폴더 배치와 파일 안전·기록·응답·GPU 잠금. 모든 이야기가 쓴다.

**⚠️ CRITICAL**: 이 단계가 끝나야 이야기를 시작한다.

- [X] T005 [P] `tests/server/test_contract.py` — `stt.server.contract` 가 없으므로 RED:
  (a) 패키지 사본 세 개와 `specs/001-server-transcription/contracts/*.schema.json` 의 바이트 일치;
  (b) `contracts/README.md` 의 ```json 블록 네 개 — 요청 셋은 `validate_request` 통과, 응답 하나는 `validate_response` 통과;
  (c) 틀린 요청이 거절되고 detail 이 data-model §2.4 의 꼴: `schema_version` 없음·`2`, 대문자 UUID, 공백 든 `requested_by`, `audio` 에 `file`·
  `from_request` 둘 다, 확장자 `.exe`, `language: "KO"`, `hints: []`, 중복 힌트, 앞 공백 힌트, 제어 문자 힌트, 101자 힌트, `delete_audio: "never"`,
  모르는 칸, delete 의 `scope: "all"`(detail 이 `scope` 를 가리킴 — `oneOf` 잡음이 아님), `kind` 없음(detail 이 `kind` 를 가리킴);
  (d) **100자 힌트 200개를 중복으로** 보내면 detail 이 `hints: items must be unique` 꼴, 500자 이하, 힌트 글자가 없음(R20-4 측정: 원문 메시지 21,107자);
  (e) `failure.cause` 규칙: `audio_deleted` 에 `cause` 없음·다른 이유에 `cause` 있음은 응답 스키마 위반;
  (f) `contract.language_supported("ko")` True, `"xx"` False, 그리고 `contract.LANGUAGE_CODES` 가 `faster_whisper.tokenizer._LANGUAGE_CODES` 와 같음
  (faster-whisper 는 이 시험 안에서만 불러온다 — R5);
  (g) `uv build --wheel` 로 만든 휠 안에 `stt/server/schemas/*.json` 세 개가 있음(`uv` 가 없으면 그 이유로 건너뜀, R21-21) — supports US1-8, US5-6, SC-012 — verify RED
- [X] T006 `src/stt/server/contract.py`: `load_schema(name: Literal["request", "response", "result"]) -> dict`(importlib.resources),
  `validate_request(doc) -> str | None`(`kind` 로 `$defs/transcribe`·`$defs/delete` 갈래를 골라 검사, 첫 오류를 §2.4 의 detail 로 — 규칙 이름
  (`required`·`pattern`·`uniqueItems`·`enum`·`const`·`additionalProperties`·`minItems`·`maxItems`·`minLength`·`maxLength`·`type`·`oneOf`)마다 고정 문구),
  `validate_response(doc) -> str | None`, `validate_result(doc) -> str | None`(내부 확인용), `LANGUAGE_CODES: frozenset[str]`(faster-whisper 1.2.1 의
  100개를 옮긴 상수)와 `language_supported(code: str) -> bool` — faster-whisper 를 불러오지 않는다(torch 가 따라 올라온다, R5) — turns T005 GREEN — supports US1-8, US5-6, SC-012
- [X] T007 [P] `tests/server/test_deadlines.py` — RED: `next_midnight` — `2026-10-22T14:59:59Z`(23:59:59 KST) → `2026-10-22T15:00:00Z`,
  정확히 `15:00:00Z` → `2026-10-23T15:00:00Z`, `15:00:01Z` → `2026-10-23T15:00:00Z`, `00:00:00Z` → 같은 날 `15:00:00Z`, 시간대 없는 datetime →
  `ValueError`; `hard_deadline(t) == t + 23h58m`, `expires_at(t) == t + 23h58m`(24시간을 넘기지 않는 여유 2분, R21-1); `processing_limit(10.0) == 300.0`, `processing_limit(300.0) == 300.0`, `processing_limit(3600.0) == 3600.0`;
  `audio_delete_at(policy, hard_deadline)` 이 data-model §5 대로(`{"at": t}` → min(t, hard), `{"after_job": id}` → hard); `format_time` 이
  마이크로초를 버리고 `Z` 로 끝남, `parse_time(format_time(t))` 왕복 — supports US2-3, US2-5, US3-5, US5-3, US5-9 — verify RED
- [X] T008 `src/stt/server/deadlines.py`: 위 함수들(순수 함수, `zoneinfo.ZoneInfo("Asia/Seoul")`, `MIN_PROCESSING_S = 300.0`,
  `MAX_AUDIO_S = 10_800.0`, `RETENTION_DUE = timedelta(hours=23, minutes=58)`) — turns T007 GREEN — supports US2-3, US2-5, US3-5, US5-3, US5-9
- [X] T009 [P] `tests/server/test_layout.py` — RED: `Layout(root)` 의 경로(`inbox`, `outbox`, `jobs`, `audio`, `work`, `queue`, `state_lock`,
  `worker_lock`, `intake_lock`, `sweep_lock`); `ensure_layout()` 가 없는 내부 폴더를 0700 으로 만들고 있는 폴더의 권한은 바꾸지 않음;
  `is_request_name`(`<소문자 UUID>.json` 만 True, `.json.tmp`·대문자·다른 이름 False), `attachment_id`(contracts 의 확장자 목록);
  `atomic_write_text(path, text, mode=0o640)` — 최종 권한이 정확히 그 값, 실패해도 임시 파일이 남지 않음;
  `hold(path)` 배타 잠금 문맥 관리자와 `try_hold(path) -> contextmanager | None` — 다른 프로세스(`subprocess` 로 띄운 `python -c` 가 `fcntl.flock`)가
  쥐면 None; **파일 안전**(R20-3): `read_request_bytes(path, limit=1 MiB) -> bytes` 와 `open_attachment(path) -> BinaryIO` 가 심볼릭 링크(대상이 있어도),
  하드 링크(링크 수 2), FIFO·폴더, 크기 초과(요청만), 읽기 권한 없음(root 로 돌면 건너뜀)에서 `UnsafeFileError`(이유 코드)를 내고 내용을 읽지 않음 — supports US1-1, US2-4, US5-6 — verify RED
- [X] T010 `src/stt/server/layout.py`: 위 배치·이름 규칙·원자적 쓰기(fsync 뒤 rename)·flock 도우미·`O_NOFOLLOW` 열기와 `fstat`(일반 파일·링크 수 1)
  확인 — turns T009 GREEN — supports US1-1, US2-4, US5-6
- [X] T011 [P] `tests/server/test_records.py` — RED: `Job`·`Audio`(data-model §3·§4 의 칸 — `Audio.policy` 는 `{"after_job": id}` | `{"at": time}`,
  `received_at`, `hard_deadline`, `Job.failure.cause`, frozen dataclass)의 JSON 왕복; `save_job`/`load_job`/`load_jobs`, `save_audio`/`load_audio`
  (원자적, 0600, 녹음 기록은 `audio/<audio_id>.json` — 녹음 파일 폴더 `audio/<audio_id>/` 밖, R21-4);
  `next_seq(jobs)` = 최대값 + 1(없으면 1); `queue_position(job, jobs)` — `queued` 가운데 seq 가 작은 것의 수 + 1, `running` 은 세지 않음, `queued` 가
  아니면 None; `queued_in_order(jobs)` — supports US1-7 — verify RED
- [X] T012 `src/stt/server/records.py`: 위 기록 형식과 읽기·쓰기(`dataclasses.replace` 로 바꾼 사본을 저장) — turns T011 GREEN — supports US1-7
- [X] T013 [P] `tests/server/test_responses.py` — RED: `job_status(job, audio, jobs, now) -> dict` 가 상태마다 `contract.validate_response` 를
  통과: `queued` 만 `queue_position`, `done` 은 `result.transcript == "<id>/transcript.txt"`·`result.data == "<id>/result.json"`, 결과를 지웠으면
  `result.deleted_at`, `failed` 는 `failure.reason`·`detail`(·`audio_deleted` 면 `cause`), `deleted` 는 `deleted.at`, 녹음 칸 `delete_policy`·
  `delete_after`(`after_job` 일 때만)·`delete_at`·`deleted_at`, `duplicate_refused_at`; `deleted` 응답은 결과가 있었으면 `result`(`deleted_at` 포함)·
  `started_at`·`finished_at` 을 남기고 `failure` 는 없음(R21-12); 받기에서 거절된 transcribe(`accepted_at` 없음)의 `rejected_status(request_id,
  reason, detail, now)`; `delete_outcome(request_id, target, scope, outcome, now)` 네 가지; `refusal(request_id, detail, now)`(`kind: invalid`);
  기록 없는 응답 세 꼴 모두 `duplicate_refused_at=None` 인자를 받음(R21-11) — 모든 문서가 response 스키마를 통과 — supports US1-8, US2-5, US3-5, US5-8 — verify RED
- [X] T014 `src/stt/server/responses.py`: 위 순수 함수들(response.schema.json 의 조건부 필수 칸을 따른다) — turns T013 GREEN — supports US1-8, US2-5, US3-5, US5-8
- [X] T015 [P] `tests/test_gpu_lock.py` — RED: `gpu_lock.lock_path_from_env(environ) -> Path | None`; `gpu_lock.acquire(path, on_wait)`
  문맥 관리자 — 파일을 읽기 전용으로 열고 `LOCK_EX`, 다른 프로세스가 쥐고 있으면 `on_wait` 를 정확히 한 번 부르고 기다림, 나오면 풀림;
  `gpu_lock.try_acquire(path)` 는 쥐어져 있으면 None; 연 fd 는 상속되지 않음(`os.get_inheritable` False); 파일이 없으면 `MissingGpuLockError`
  (메시지 `STT_GPU_LOCK points to a missing file: <path>`); `WAIT_MESSAGE == "GPU is in use by another stt job; waiting for it to finish."`;
  **`sys.modules["fcntl"] = None` 에서도 `import stt.gpu_lock` 이 된다**(R20-15) — supports US4-1, US4-2, US4-3, US4-4 — verify RED
- [X] T016 `src/stt/gpu_lock.py`: 위 함수·상수·`MissingGpuLockError`(contracts/cli.md §2, R3). `fcntl` 은 함수 안에서 불러온다 — turns T015 GREEN — supports US4-1, US4-2, US4-3, US4-4

**Checkpoint**: 계약·시각·배치·파일 안전·기록·응답·잠금이 시험으로 고정됐다.

---

## Phase 3: User Story 1 - 녹음을 맡기고 전사록을 받는다 (Priority: P1) 🎯 MVP

**Goal**: inbox 에 맡긴 녹음이 접수(대기 순서)되고, 워커가 엔진을 띄워 전사록·문단 목록·실행 요약을 outbox 에 게시한다.

**Independent Test**: 시험 녹음을 화자 구분·힌트 조합별로 맡기고(가짜 엔진·가짜 모델), 돌아온 응답·결과를 스키마로 검사한다.

이 단계는 받기의 경계 검사(스키마·이름·첨부·언어·파일 안전)와 엔진의 시작 점검(GPU·모델 판)을 함께 만든다 — 경계에서 바로 검증해야 하기
때문이다. 그래서 US5-5·US5-6 과 경계 사례 1(중복)도 여기서 닫는다. GPU 잠금(US4)·삭제(US2)·`from_request`(US3)·처리 시간 상한과 엔진 오류
분류(US5)는 뒤 단계가 더한다.

### Tests for User Story 1 (MANDATORY - write first) ⚠️

- [X] T017 [P] [US1] `tests/server/fake_engine.py`: `python fake_engine.py --mode MODE [--record FILE] <work_dir>`. RED 해당 없음 — 시험 도구.
  `work_dir/spec.json` 을 읽고 MODE 에 따라: `done` — `out/transcript.txt`(영어 한 줄)·`out/result.json`(spec 의 diarize·hints 를 반영해
  result 스키마를 통과하는 문서)·`outcome.json` `{"status": "done"}`; `fail=<reason>` — `{"status": "failed", "reason": ..., "detail": "Fake failure."}`;
  `sleep=<s>` — `out/partial.txt` 를 먼저 쓰고 s 초 잔 뒤 `done`; `die` — 판정 없이 종료 코드 1; `--record FILE` — 시작·끝 단조 시각과 받은 환경
  (`TMPDIR`·`HF_HOME`·`HF_HUB_OFFLINE`)을 한 줄씩 덧붙임. `result.json` 의 `request_id` 는 `spec.json` 의 것 — supports US1-1, US2-9, US4-3, US5-3
- [X] T018 [P] [US1] `tests/server/test_us1.py` — `stt.server.intake`·`stt.server.worker` 가 없으므로 RED:
  `test_us1_1_accepted_job_reports_queue_then_done` — 2초 WAV 를 맡기고 `intake.run_once(layout, now)` → outbox 응답 `queued`·`queue_position` 1,
  inbox 가 빔, 녹음이 `audio/<id>/audio.wav`, `queue/` 에 표시 하나; `run_worker(...)` → 응답 `done`, `outbox/<id>/transcript.txt`·`result.json`,
  `work/<id>/` 없음, **`queue/` 가 빔**(R20-1);
  `test_us1_7_third_job_gets_position_three_and_moves_up` — 셋을 맡기면 1·2·3, `run_worker_once` 한 번 뒤 남은 둘이 1·2;
  `test_worker_passes_engine_env` — 엔진이 받은 `TMPDIR` 이 그 job 의 작업 폴더, `HF_HOME` 이 `--models`, `HF_HUB_OFFLINE == "1"`, `spec.json` 에
  `request_id`(R21-5·13); `test_engine_crash_without_verdict_is_engine_error`(`die` → `failed(engine_error)`, 고정 문장);
  `test_us1_8_every_response_and_result_validates` — queued·running(`sleep` 모드를 스레드로 돌리며 관찰, 이때 그 job 의 대기 표시 없음)·done·failed
  응답과 result.json 이 스키마 통과 — closes US1-1, US1-7, US1-8, supports FR-015, FR-022 — verify RED
- [X] T019 [P] [US1] `tests/server/test_us5.py`(US5-6)와 `tests/server/test_edge_cases.py`(경계 사례 1·받기 안전) — RED:
  `test_us5_6_invalid_request_refused_with_reason` — 스키마 위반(빠진 칸·모르는 값·빈 `hints`) → `refusal`, detail 이 틀린 칸을 가리킴;
  스키마는 맞고 `request_id` 와 파일 이름 불일치·첨부 없음·모르는 언어(`"xx"`) → `failed(invalid_request)` job 상태(`accepted_at` 없음); 응답의
  `request_id` 는 파일 이름; 첨부와 요청이 inbox 에서 사라짐; job 기록 없음; 본문의 `audio.file` 이 다른 요청의 첨부를 가리켜도 그 첨부는 남음
  (지우는 것은 파일 이름 앞부분뿐, R21-9); UUID 이름이 아닌 `foo.json` 은 답 없이 지워지고 로그에 파일 이름 없는 고정 문구 한 줄(R21-16);
  `test_edge_1_duplicate_request_id_refused` — 새 요청·첨부가 지워지고 기존 응답의 상태는 그대로, `duplicate_refused_at` 만 적힘 — 있던 응답이 refusal 이어도 그 파일에 칸이 더해짐(R21-11);
  `test_unsafe_inbox_files_refused_without_reading` — 요청이 심볼릭 링크(대상 `{"zz": "secretvalue"}`) → `refusal`, outbox 어디에도 `secretvalue` 없음;
  첨부가 심볼릭 링크·하드 링크·FIFO → `failed(invalid_request)`, 링크 대상은 그대로;
  `test_every_inbox_json_is_consumed` — 깨진 JSON·UTF-8 아님·0600 으로 다른 계정이 만든 것 흉내(`chmod 000`, root 면 건너뜀)·1 MiB 초과 →
  모두 `refusal`, 처리 뒤 inbox 에 `*.json` 이 없음 — closes US5-6, 경계 사례 1, supports FR-002 — verify RED
- [X] T020 [P] [US1] `tests/server/test_engine.py` — `stt.server.engine` 이 없으므로 RED. `engine.main([work_dir])` 를 같은 프로세스에서 부르고
  (`gpu_present`·`pinned_models` fixture), `transcribe.load_model` 을 `tests/test_cli_integration.py` 의 `FakeModel` 모양(영어 문장)으로, 화자 파이프라인
  (`diarize.load_pipeline`·`diarize.to_wav`·화자 구간)을 가짜로 바꿔 낀다:
  `test_us1_2_diarized_paragraphs_have_speaker_and_times` — 모든 문단 `speaker` 가 `speaker_label("SPEAKER_0n")` 값, `start <= end`, 1.5초 미만
  문단은 `uncertain` True, 전사록에 그 문단의 불확실 표시, `speaker_label` 밖의 이름 없음;
  `test_us1_3_plain_paragraphs_have_times_no_speaker`; `test_us1_4_hints_within_budget_all_applied`(`applied == total`, `dropped == []`);
  `test_us1_5_hints_over_budget_report_dropped_in_order`(글자 하나 = 토큰 하나인 가짜 토크나이저, `dropped` 가 hints 의 뒤쪽을 순서대로);
  `test_us1_6_no_hints_summary_null`; `test_edge_4_short_backchannel_marked_uncertain_not_dropped`; `test_edge_5_single_overlong_hint_dropped_job_done`;
  `test_engine_uses_cli_defaults_without_fallback` — `load_model` 이 `("large-v3", "cuda")` 로 한 번, `beam_size == 5`, 화자 구분이면 단어 시각,
  문단 경계 2초·800자(R20-11); `test_engine_temp_files_stay_in_work_dir` — 가짜 `to_wav` 가 받은 출력 경로가 `<work_dir>/tmp/` 아래(R21-5); `test_engine_passes_only_applied_hints` — `hotwords == ", ".join(hints[:applied])`(R20-10);
  `test_us5_5_no_gpu_fails_before_loading` — CUDA 장치 0 → `gpu_unavailable`, 모델을 부르지 않음; `diarize` 인데 torch 가 CUDA 를 못 봄 → `gpu_unavailable`(R17);
  `test_models_missing_or_other_revision_is_models_unavailable` — `refs/main` 이 없거나 다른 커밋 → `models_unavailable`, 모델을 부르지 않음(R9);
  모든 result.json 이 스키마 통과, 성공이면 `outcome.json` 이 `{"status": "done"}` — closes US1-2, US1-3, US1-4, US1-5, US1-6, US1-8, US5-5, 경계 사례 4·5 — verify RED
- [X] T021 [P] [US1] `tests/test_transcribe.py` 와 `tests/test_diarize.py` 에 R8 시험을 더한다(기존 시험은 고치지 않는다) — RED:
  `test_build_timed_paragraphs_keeps_end_time` — 새 `build_timed_paragraphs` 의 문단 끝 시각 = 그 문단 마지막 세그먼트의 `end`;
  `test_build_paragraphs_is_projection_of_timed` — 같은 입력에서 `build_paragraphs` 가 `[(start, text) …]` 로 같음; `test_diarized_blocks_split_from_rendering` —
  새 함수(예: `diarized_blocks(...)`)가 `Block` 목록을 내고 `build_diarized_body` 의 글은 지금과 같음; `test_edge_6_offline_pipeline_needs_no_token` —
  `HF_HUB_OFFLINE=1`·`HF_TOKEN` 없음이면 `load_pipeline` 이 `token=None` 으로 `Pipeline.from_pretrained` 를 부름(가짜), 오프라인이 아니면 지금처럼
  토큰을 요구 — supports 경계 사례 6(망 없는 완료는 T076 의 C-7 이 닫는다), US1-2, US1-3, US4-4 — verify RED
- [X] T022 [P] [US1] `tests/server/test_server_cli.py` — RED: `stt server`(하위 명령 없음)·`stt server bogus` → 사용법을 stderr 에, 종료 2;
  `stt --help` 에 `server`; `stt server intake --root R` 이 다른 프로세스가 `intake.lock` 을 쥔 동안 종료 75, `work` 는 `worker.lock` 으로 75;
  빈 루트에서 둘 다 0 — supports US1-1 — verify RED
- [X] T023 [P] [US1] `tests/server/test_logging.py` — RED: 힌트 `"zzhintword"`·요청자 `"zzrequester"` 로 받기 → 워커(가짜 엔진) → 엔진(가짜 모델)을
  돌린 뒤 `capfd` 의 stdout·stderr 에 두 낱말과 전사문이 0번, `request_id` 와 상태 낱말은 있음 — supports FR-037 — verify RED
- [X] T024 [P] [US1] `tests/server/test_models.py` — RED: `models.PINNED` 가 research R9 표의 저장소 다섯·커밋과 같음; `ref_mismatches(hf_home)` 이
  없는 저장소·다른 커밋을 모두 저장소 이름으로 돌려주고, 모두 맞으면 `[]` — supports US5-5(시작 점검), FR-012 — verify RED

### Implementation for User Story 1 (turn T018–T024 GREEN)

- [X] T025 [US1] `src/stt/transcribe.py` R8-1: `build_timed_paragraphs(...) -> list[tuple[float, float, str]]` 를 더하고 `build_paragraphs` 는 그 결과에서
  `(start, text)` 를 뽑는다(반환 모양·`render_txt` 그대로). 두 함수의 docstring 영어 — turns `test_build_timed_paragraphs_keeps_end_time`,
  `test_build_paragraphs_is_projection_of_timed` GREEN (T021) — supports US1-2, US1-3, US4-4
- [X] T026 [US1] `src/stt/transcribe.py` R8-3: `build_diarized_body` 에서 화자 문단 만들기(`Block` 목록)를 따로 떼고, 글 옮기기는 그 결과를 쓴다
  (임시 WAV 는 지금처럼 `TemporaryDirectory` — 엔진에서는 `TMPDIR` 이 작업 폴더라 함께 지워진다). 명령줄 출력 그대로 — turns `test_diarized_blocks_split_from_rendering` GREEN (T021) — supports US1-2, US4-4
- [X] T027 [P] [US1] `src/stt/diarize.py` R8-4: `load_pipeline` 이 `HF_HUB_OFFLINE` 이 켜져 있으면(`"1"`) `HF_TOKEN` 없이 `token=None` 으로 읽는다.
  docstring 영어, 기존 토큰 오류 문구는 그대로 — turns `test_edge_6_offline_pipeline_needs_no_token` GREEN (T021) — supports 경계 사례 6
- [X] T028 [P] [US1] `src/stt/server/models.py`: `PINNED: tuple[tuple[str, str], ...]`(R9 표), `ref_mismatches(hf_home: Path) -> list[str]`
  (`hub/models--<org>--<name>/refs/main` 을 읽어 비교) — turns T024 GREEN — supports US5-5, FR-012
- [X] T029 [US1] `src/stt/server/lifecycle.py`(R20-9): `start(layout, job, now)` — `queued` → `running`, 대기 표시 삭제, 응답 갱신;
  `finish(layout, job, state, now, failure=None)` — 끝 상태로 가는 **유일한 길**: 상태·`finished_at`·`failure`, 대기 표시 삭제, 남은 `queued` 응답의
  순서 갱신. 모두 상태 잠금 아래에서 부른다 — turns T018 의 대기 표시 단언 GREEN (워커와 함께) — closes US1-1, US1-7
- [X] T030 [US1] `src/stt/server/intake.py`: `run_once(layout, now) -> None` — `inbox/*.json` 을 (mtime, 이름) 순으로 처리하고 **본 것은 모두 없앤다**.
  data-model §2.4 의 차례: 이름 → 중복 → `read_request_bytes`(안전·크기·UTF-8 JSON, 실패는 `refusal`) → `validate_request`(`refusal`) → `request_id`
  일치·`open_attachment`(안전)·언어 → `probe_duration(path) -> float`(PyAV, 컨테이너 길이가 없으면 패킷을 훑어 더함 — R4; 길이 상한·해독 실패 분류는
  US5) → 받아들임. 받아들임: 첨부를 `audio/<id>/audio.<ext>`(0600)로 복사(상태 잠금 밖), 상태 잠금 아래 `Audio` 기록 `audio/<id>.json`(정책 `{after_job: R}` 또는
  `{at: next_midnight(accepted_at)}`, `received_at`, `hard_deadline` — data-model §5)·`Job`·`queue/<seq:08d>-<id>` 표시·응답, 모든 `queued` 응답의 순서
  갱신, inbox 의 요청·첨부 삭제. 거절은 §2.4 의 꼴로 답하고 **파일 이름 앞부분의** 첨부만 삭제. 중복은 기록이 있으면 기록에, 없으면 응답 파일에
  `duplicate_refused_at`. 이름이 UUID 가 아닌 파일을 버릴 때 고정 로그 한 줄. `from_request` 는 US3 전까지 `failed(invalid_request)`("from_request is not
  supported.")로, delete 요청은 US2 전까지 `refusal` 로 답한다. 로그는 `<request_id> <state>` 꼴 — turns T018(받기 부분), T019, T023(받기 부분) GREEN — closes US1-1, US1-7, US5-6, 경계 사례 1
- [X] T031 [US1] `src/stt/server/worker.py`: `run(layout, engine_cmd, now, poll_s=2.0) -> int`(줄이 빌 때까지 `run_one`), `run_one(...) -> bool` —
  상태 잠금 아래 seq 가 가장 작은 `queued` 를 `lifecycle.start`, `work/<id>/`(0700)에 `spec.json`(`request_id`, `audio_path`, `original_name`,
  `language`, `diarize`, `hints`, `duration_s`)을 쓰고 `engine_cmd + [work_dir]` 를 `start_new_session=True`·`stdin=DEVNULL`·`env` 에
  `TMPDIR=<work_dir>`·`HF_HOME=<models_dir>`·`HF_HUB_OFFLINE=1` 을 더해 띄움(`run(..., models_dir)`), `poll_s` 마다
  끝났는지 봄, `outcome.json` 이 없거나 깨졌으면 `engine_error`("The engine stopped without a verdict."), 상태 잠금 아래 job 이 아직 `running` 인지 다시
  확인한 뒤에만 `out/` 의 `transcript.txt`·`result.json` 을 `outbox/.<id>.tmp/` 에 0640 으로 복사하고 `outbox/<id>/`(2750)로 rename 해 게시, 그리고
  `lifecycle.finish`, 작업 폴더 삭제. 기본 엔진 명령 `[sys.executable, "-m", "stt.server.engine"]` — turns T018(워커 부분, `test_worker_passes_engine_env`,
  `test_engine_crash_without_verdict_is_engine_error` 포함) GREEN — closes US1-1, US1-7, US1-8
- [X] T032 [US1] `src/stt/server/engine.py`: `main(argv) -> int`, `if __name__ == "__main__"`. `spec.json` 을 읽고 `tempfile.tempdir` 를
  `<work_dir>/tmp` 로 정한 뒤(R21-5) **시작 점검**: `cuda_device_count()`
  (ctranslate2) > 0, `diarize` 면 `torch_cuda_available()` 도 — 아니면 `gpu_unavailable`; `models.ref_mismatches(HF_HOME)` 가 비어 있음 — 아니면
  `models_unavailable`(실패 판정은 `outcome.json` 에 쓰고 종료 0, 고정 문장 detail). 그다음 `transcribe.load_model("large-v3", "cuda")`(대체 없음),
  hints 가 있으면 `build_terms_report("hints", hints, encode, HOTWORD_TOKEN_LIMIT)`(`report_terms_budget` 은 부르지 않음 — 힌트가 로그에 남는다) 뒤
  `transcribe_file`(hotwords = `", ".join(hints[:applied])`, beam 5, 화자 구분이면 단어 시각), `build_timed_paragraphs`(2초·800자), `diarize` 면
  `load_pipeline("cuda")` 와 T026 의 함수로 화자 문단(불확실 1.5초), `build_header`(원본 칸 = `original_name`)로 전사록, `result.json`
  (`request_id` 는 `spec.json` 의 것, 문단 수 칸 없음 — R21-3)을
  `contract.validate_result` 로 확인한 뒤 `out/` 에 쓰고 `{"status": "done"}`. `processing_s` 는 단조 시계. 무거운 import 는 `main` 안에서.
  엔진 오류 분류는 US5 — turns T020, T023(엔진 부분) GREEN — closes US1-2…US1-6, US1-8, US5-5, 경계 사례 4·5
- [X] T033 [US1] `src/stt/server/cli.py` 와 `src/stt/cli.py`: `stt server intake [--root DIR]`, `stt server work [--root DIR] [--models DIR]`(`--models` 는 엔진
  환경의 `HF_HOME` 과 `HF_HUB_OFFLINE=1`), 기본값 `/var/lib/stt`·`/var/lib/stt-models`, 각 단계 잠금을 `try_hold` 로 잡지 못하면 75, 사용법 오류 2,
  도움말 영어. `src/stt/cli.py` 의 `COMMANDS` 에 `"server"`, `_SUMMARIES` 에 영어 한 줄 — turns T022 GREEN — supports US1-1
- [X] T034 [US1] US1 확인: `uv run python -m pytest -q` 전부 통과(기존 243개 포함, 기존 시험 파일의 기존 시험은 고치지 않았음을 `git diff` 로 확인).
  RED 해당 없음 — 확인 — closes US1-1…US1-8, US5-5, US5-6, 경계 사례 1·4·5

**Checkpoint**: 받기 → 워커 → 엔진 → 게시가 가짜 엔진·가짜 모델로 끝까지 돈다(MVP).

---

## Phase 4: User Story 2 - 원본과 결과는 정한 때에 반드시 지워진다 (Priority: P1)

**Goal**: 「끝나면 바로」·「자정」·24시간·「결과 지우기」·「지금 지우기」·죽은 워커의 흔적을 정한 때에 집행한다.

**Independent Test**: 삭제 시기 × 종료 방식(정상·실패·강제 종료) 조합으로 job 을 돌리고 주입한 시계로 시각을 넘긴 뒤 `leftovers(root)` 가 비었는지 본다.

### Tests for User Story 2 (MANDATORY - write first) ⚠️

- [X] T035 [P] [US2] `tests/server/test_us2.py` — `stt.server.sweep` 이 없으므로 RED:
  `test_us2_1_after_job_done_leaves_nothing`(녹음·작업 폴더·job 기록의 `hints` 없음);
  `test_us2_2_after_job_failed_leaves_nothing`(`fail=engine_error`);
  `test_us2_3_midnight_job_audio_gone_after_midnight`(23:10 KST 완료, 23:59 청소 → 남음, 00:00:30 청소 → 없음, 응답 `audio.deleted_at`);
  `test_us2_4_dead_worker_traces_cleared`(job `running` 기록 + 작업 폴더의 가짜 WAV + `after_job` 녹음, `worker.lock` 비어 있음 → `sweep.run_once` 뒤
  `failed(interrupted)`, 작업 폴더·녹음·힌트 없음; 같은 흔적을 `worker.run` 시작이 정리하는 경우도);
  `test_us2_5_queued_midnight_job_fails_when_audio_deleted`(23:50 KST 접수, 자정 뒤 청소 → `failed(audio_deleted)`, `cause: midnight`, 결과 없음);
  `test_us2_6_result_delete_keeps_audio_until_midnight`(`scope: result` → `outbox/<id>/` 없음, `result.deleted_at`, 답 `deleted`, 녹음은 남음);
  `test_result_delete_twice_reports_already_deleted`(두 번째 `scope: result` → `already_deleted`, R20-7);
  `test_us2_7_unfetched_result_gone_after_24h`(접수 + 23h58m − 1초 청소 → 남음, + 23h58m → 응답·결과·job 기록 모두 없음 — 24시간 전에 끝남, R21-1);
  `test_us2_8_delete_everything_on_stored_audio`(녹음·결과 없음, `state: deleted`, `deleted.at`, `audio.deleted_at`, 답 `deleted`; 응답에 `result.deleted_at`·
  `started_at`·`finished_at` 이 남고 `failure` 없음; 그 뒤 `scope: result` → `already_deleted`, 결과 없이 지운 job 이면 `no_result` — R21-12);
  `test_us2_9_delete_everything_stops_running_job`(`sleep=30` 엔진을 워커 스레드로, `poll_s=0.05`; 받기가 지금 지우기를 처리한 뒤 2초 안에 엔진
  프로세스 묶음이 없고 작업 폴더·`outbox/<id>/`·`partial.txt` 없음, `state: deleted`);
  `test_us2_10_other_requester_delete_refused`(답 `refused`, 모든 파일 바이트 그대로);
  `test_us2_11_second_delete_reports_already_deleted`; `test_us2_12_result_delete_without_result_reports_no_result`(queued·running·failed, 아무것도 안 바뀜);
  `test_delete_request_id_mismatch_refused`(본문 `request_id` ≠ 파일 이름 → `kind: invalid`, 응답 이름은 파일 이름, 대상은 그대로);
  `test_duplicate_delete_request_marks_its_response`(같은 delete `request_id` 가 다시 오면 그 delete 답에 `duplicate_refused_at`);
  `test_edge_8_queued_job_past_deadline_not_started`(녹음 삭제 시각이 지났는데 청소가 아직 돌지 않음 → 워커가 시작하지 않고 그 원인으로
  `failed(audio_deleted)`, 녹음 없음 — R21-7);
  `test_sc003_deletion_matrix`(`after_job`/`at_midnight` × 정상/실패/강제 종료(`die`, 워커 강제 종료 흉내, **게시 rename 뒤 마무리 전 죽음**, **받기가
  첨부를 복사한 뒤 기록 전에 죽음**) — 정한 시각 직후와 +23h58m(+1분)에 `leftovers` 0, R21-2) — closes US2-1…US2-12, 경계 사례 8(워커 쪽), SC-003, SC-004, SC-005 — verify RED
- [X] T036 [P] [US2] `tests/server/test_edge_cases.py` 에 더한다 — RED:
  `test_edge_2_orphan_attachment_removed_after_one_hour`(요청 없는 첨부와 `.json.tmp` — ctime +59분 남음, +61분 없음; mtime 은 오래됐지만 ctime 은
  새것인 첨부는 남음 — R21-10; 청소 로그에 파일 이름 없는 고정 문구 한 줄);
  `test_stale_temp_and_orphan_audio_removed_after_one_hour`(`outbox/.<id>.tmp/`·`jobs/<id>/.*.tmp`·기록 없는 `audio/<id>/` — ctime +59분 남음, +61분
  없음; 워커가 없으면 게시 중간 폴더는 청소가 바로 지움 — R21-2);
  `test_edge_3_schedule_holds_without_worker`(워커를 한 번도 돌리지 않아도 청소만으로 자정·24시간 삭제);
  `test_rejected_and_delete_responses_expire_after_24h`(기록 없는 응답은 응답의 `expires_at` 으로) — closes 경계 사례 2·3, supports FR-032, FR-033 — verify RED
- [X] T037 [P] [US2] `tests/server/test_server_cli.py` 에 `stt server sweep --root R` 을 더한다: 빈 루트 0, `sweep.lock` 을 남이 쥐면 75 — supports US2-3 — verify RED
- [X] T038 [P] [US2] `tests/server/test_logging.py` 에 더한다 — RED: 지우기 두 가지·청소(자정·24시간·죽은 워커·오래된 inbox 파일)를 돌린 뒤에도
  힌트·요청자·전사문·inbox 파일 이름 0번 — supports FR-037 — verify RED

### Implementation for User Story 2 (turn T035–T038 GREEN)

- [X] T039 [US2] `src/stt/server/lifecycle.py` 삭제 동작(모두 상태 잠금 아래): `finish` 를 넓힌다 — 끝 상태가 되면 `hints` 를 지우고, 정책이
  `{after_job: 이 job}` 이면 녹음 삭제; `delete_audio(layout, audio_id, cause, now)` — 녹음 파일 폴더만 삭제(기록은 남김), `deleted_at`·`deleted_by`,
  그 녹음을 쓰는 `queued`·`running` job 을 `finish(failed(audio_deleted, cause))`, 쓰는 모든 job 의 응답 갱신; `delete_result(layout, job, now)`;
  `delete_everything(layout, job, now)`(결과가 있었으면 `result_deleted_at` 도); `purge_expired(layout, now)` — data-model §6(결과·응답·job 기록,
  녹음 기록은 마지막 사용 job 의 `expires_at`, 기록 없는 응답은 그 `expires_at`, ctime 으로 1시간 지난 inbox 잔여물·임시 파일·게시 중간 폴더·기록 없는
  녹음 폴더 — 지울 때 파일 이름 없는 로그 한 줄); `clear_dead_worker(layout, now)` — `running` 을 `finish(failed(interrupted))`, 그 job 의
  `outbox/<id>/`(게시 뒤 마무리 전에 죽은 경우)·`work/*`·게시 중간 폴더 삭제 — T040–T042 와 함께 turns `test_us2_1`…`_8`, `_10`…`_12`, T036 GREEN — closes US2-1, US2-3, US2-5, US2-7, US2-8
- [X] T040 [US2] `src/stt/server/intake.py`: delete 요청 — 같은 요청자의 transcribe 가 아니면 `refused`(없음과 같은 답); `scope: result` → 결과가 있으면
  `delete_result`·`deleted`, 이미 지워졌으면 `already_deleted`, 생긴 적 없으면 `no_result`; `scope: everything` → 이미 `deleted` 면 `already_deleted`,
  아니면 `delete_everything`·`deleted`; `deleted` job 에 `scope: result` 는 `result_deleted_at` 이 있으면 `already_deleted`, 없으면 `no_result`;
  본문 `request_id` 가 파일 이름과 다르면 `refusal`; delete 요청 자신의 응답 `outbox/<delete_request_id>.json`. US1 의 임시 `refusal` 을 없앤다 —
  turns `test_us2_6`, `_8`, `_10`, `_11`, `_12`, `test_result_delete_twice_reports_already_deleted`, `test_delete_request_id_mismatch_refused`,
  `test_duplicate_delete_request_marks_its_response` GREEN (T035) — closes US2-6, US2-8, US2-10, US2-11, US2-12
- [X] T041 [US2] `src/stt/server/worker.py`: 매 `poll_s` 마다 상태 잠금 아래 job 이 아직 `running` 인지 봐서 아니면 `os.killpg(SIGKILL)`·작업 폴더 삭제·
  게시하지 않음; 시작할 때(`worker.lock` 을 쥔 뒤) `clear_dead_worker`; job 을 고를 때 그 녹음의 삭제 시각이 지났으면 시작하지 않고 그 원인으로
  `lifecycle.delete_audio`(R21-7) — turns `test_us2_1`, `_2`, `_4`, `_9`, `test_edge_8_queued_job_past_deadline_not_started` GREEN (T035) — closes US2-1, US2-2, US2-4, US2-9, 경계 사례 8(워커 쪽)
- [X] T042 [US2] `src/stt/server/sweep.py` `run_once(layout, now) -> None` — 정책 시각·`hard_deadline` 이 지난 녹음을 `delete_audio`(원인 `midnight`·
  `retention_limit`), 그다음 `purge_expired`, `worker.lock` 을 `try_hold` 로 잡을 수 있으면(워커 없음) `clear_dead_worker`; `src/stt/server/cli.py` 에
  `stt server sweep [--root DIR]`(`sweep.lock`, 75) — turns `test_us2_3`, `_4`, `_5`, `_7`, `test_sc003_deletion_matrix`, T036, T037, T038 GREEN — closes US2-3, US2-4, US2-5, US2-7, 경계 사례 2·3, SC-003
- [X] T043 [US2] US2 확인: 전체 시험 통과. RED 해당 없음 — 확인 — closes US2-1…US2-12, 경계 사례 2·3, SC-003·SC-004·SC-005(가짜 엔진 기준)

**Checkpoint**: 정상·실패·강제 종료·워커 없음 어느 경우에도 정한 때에 남는 것이 없다.

---

## Phase 5: User Story 3 - 고친 단어장으로 같은 녹음을 다시 전사한다 (Priority: P2)

**Goal**: `audio.from_request` 로 1차 녹음을 다시 받지 않고 2차 전사를 맡긴다. 녹음 정책은 가장 나중에 받은 요청이 정한다.

**Independent Test**: 1차(`at_midnight`) → 2차(`from_request`, 힌트, 화자 구분, `after_job`) — 2차가 첨부 없이 끝나고 끝난 직후 녹음이 없다.

**Depends on**: US2(정책 집행·청소). US1 의 임시 `from_request` 거절을 없앤다.

### Tests for User Story 3 (MANDATORY - write first) ⚠️

- [X] T044 [P] [US3] `tests/server/test_us3.py` — RED(지금은 `invalid_request` 로 거절된다):
  `test_us3_1_second_pass_reuses_first_audio`(첨부 없이 `done`, 엔진의 `spec.json` 이 1차 녹음 경로를 가리킴);
  `test_us3_2_after_job_second_pass_deletes_shared_audio`(2차 완료 직후 녹음 없음, 1차 응답의 `audio.deleted_at` 도 갱신);
  `test_us3_3_deleted_source_refused`(`source_unavailable`); `test_us3_4_other_requester_source_refused_without_disclosure`(답이 「없는 job」의 답과
  `request_id`·시각 말고 같음); `test_us3_5_hard_deadline_fails_waiting_second_pass`(1차 접수 T, 2차 T+20h 접수, 청소 T+23h58m → 녹음 없음, 2차
  `failed(audio_deleted)`·`cause: retention_limit`, 2차 응답의 `audio.delete_at` 이 T+23h58m); `test_us3_6_unknown_source_refused`(`source_unavailable`);
  `test_delete_everything_fails_other_users_of_audio`(같은 녹음을 기다리던 2차 → `failed(audio_deleted)`, `cause: deleted_on_request` — 공유 녹음은
  `from_request` 로만 생기므로 이 단계에서 시험한다); `test_edge_8_second_pass_after_deadline_before_sweep_refused`(1차 `at_midnight`, 00:00:30 에 2차,
  청소 없음 → `source_unavailable`, 녹음 없음, 1차 응답 `audio.deleted_at`·원인 midnight — R21-7) — closes US3-1…US3-6, 경계 사례 8(받기 쪽), SC-006 — verify RED
- [X] T045 [P] [US3] `tests/server/test_edge_cases.py` 에 `test_edge_7_second_pass_on_pending_first_runs_after_it` — 1차가 `queued` 인 동안 2차를 받아들이고
  (`queue_position` 2), 워커가 1차 → 2차 순으로 처리 — closes 경계 사례 7 — verify RED

### Implementation for User Story 3 (turn T044–T045 GREEN)

- [X] T046 [US3] `src/stt/server/intake.py`: `from_request` 분기 — 원래 job 이 있고 같은 요청자이고 녹음이 지워지지 않았을 때만 받음, 아니면
  `failed(source_unavailable)`(셋을 가르지 않는 같은 detail); 받으면 `Job.audio_id` = 원래 녹음, 녹음의 `policy` 를 이 요청 것으로 바꾸고 `users` 에 더함,
  그 녹음을 쓰는 모든 job 의 응답 갱신. 그 녹음의 삭제 시각이 지났으면 받기 전에 그 원인으로 `lifecycle.delete_audio` 를 부르고
  `source_unavailable`(R21-7). 임시 거절 삭제 — turns T044, T045 GREEN — closes US3-1, US3-3, US3-4, US3-6, 경계 사례 7·8
- [X] T047 [US3] `src/stt/server/worker.py`·`src/stt/server/responses.py`: 엔진 `spec.json` 의 `audio_path`·`original_name` 을 job 의 `audio_id` 녹음에서
  가져오고, 응답의 `audio.delete_at` 을 그 job 기준(data-model §5)으로 — turns `test_us3_1`, `test_us3_5` GREEN (T044) — closes US3-1, US3-2, US3-5
- [X] T048 [US3] US3 확인: 전체 시험 통과. RED 해당 없음 — 확인 — closes US3-1…US3-6, 경계 사례 7, SC-006

**Checkpoint**: 2패스가 첨부 재전송 없이 돌고, 남의 것·지워진 것·없는 것은 같은 답으로 거절된다.

---

## Phase 6: User Story 4 - 운영자의 손 전사와 서버 작업이 한 줄로 선다 (Priority: P2)

**Goal**: 워커와 명령줄(`transcribe`·`batch`·`enroll`)이 `/run/stt/gpu.lock` 하나로 줄을 선다. 말없던 CPU 화자 구분은 경고한다. 그 밖의 명령줄
동작은 그대로(Windows 포함).

**Independent Test**: 한쪽이 잠금을 쥔 동안 다른 쪽을 시작해, 기다렸다가 끝나는지와 쥔 구간이 겹치지 않는지 본다.

### Tests for User Story 4 (MANDATORY - write first) ⚠️

- [X] T049 [P] [US4] `tests/test_cli_gpu_lock.py` — RED: 잠금 파일을 다른 프로세스가 1초 쥔 동안 `STT_GPU_LOCK` 을 두고
  `transcribe.main([media, "--device", "cuda"])`(가짜 모델) → stderr 에 `gpu_lock.WAIT_MESSAGE` 정확히 한 번, 모델 적재 시각 ≥ 풀린 시각, 종료 0
  (`test_us4_1_cli_waits_for_server_job`); 끝난 뒤 잠금이 풀려 있음; `STT_GPU_LOCK` 없음 → 출력이 잠금 기능 전과 같음, `--device cpu` 는 잠금 파일이
  없어도 잠그지 않고 돈다(`test_us4_4_cli_unchanged_without_lock`); `test_cli_runs_without_fcntl` — `sys.modules["fcntl"] = None`·`STT_GPU_LOCK` 없음에서
  `transcribe.main` 이 지금처럼 끝남(Windows 흉내, R20-15) — `test_us4_4`·`test_cli_runs_without_fcntl` 은 **회귀 방지 시험으로 처음부터 GREEN** 일
  수 있다(지금 동작을 지키는 시험, R21 F5); 파일이 없는데 `cuda` → `STT_GPU_LOCK points to a missing file: <path>` 와 종료 2,
  `stt batch --keep-going` 도 다음 묶음으로 넘어가지 않고 2; `stt enroll` 도 같은 대기 안내 — closes US4-1, US4-4, supports SC-013 — verify RED
- [X] T050 [P] [US4] `tests/test_diarize.py` 에 `test_us4_5_cpu_fallback_warns_once` — RED: 가짜 torch(`cuda.is_available()` False)·가짜 `Pipeline` 으로
  `load_pipeline("cuda")` → 파이프라인을 돌려주고 stderr 에 cli.md §3 의 경고 정확히 한 번; CUDA 가 있으면 경고 없음; `load_pipeline("cpu")` 도 경고 없음 — closes US4-5 — verify RED
- [X] T051 [P] [US4] `tests/server/test_us4.py` — RED: `test_us4_2_server_job_waits_for_cli`(다른 프로세스가 잠금을 쥔 동안 `worker.run(..., gpu_lock=path)`
  를 스레드로 → 응답이 `queued`·`queue_position` 1 로 머물고 엔진이 뜨지 않음, 풀면 `done`); `test_us4_3_no_overlap_when_started_together`(명령줄 쪽
  흉내 = `gpu_lock.acquire` 안에서 구간 기록하는 하위 프로세스, 워커 쪽 = `fake_engine --record` — 두 구간이 겹치지 않고 둘 다 끝남);
  `test_delete_while_waiting_for_gpu`(잠금 대기 중 지금 지우기 → 엔진을 띄우지 않고 `deleted`); `test_cli_gets_turn_between_server_jobs`(줄에 job 둘,
  명령줄 쪽 흉내가 `LOCK_EX` 로 기다림 → 첫 job 뒤 명령줄이 잠금을 받고, 그 뒤 둘째 job — R3); `stt server work` 에 `--gpu-lock` 이 없으면 종료 2 — closes US4-2, US4-3, SC-010 — verify RED

### Implementation for User Story 4 (turn T049–T051 GREEN)

- [X] T052 [US4] `src/stt/transcribe.py` `main`: `STT_GPU_LOCK` 이 있을 때만 `stt.gpu_lock` 을 불러오고, 장치가 `cuda` 면 화자 파이프라인·모델 적재
  전에 `gpu_lock.acquire(path, on_wait=stderr 에 WAIT_MESSAGE)` 를 쥐고 `run_with_fallback` 이 끝날 때까지 유지; `MissingGpuLockError` 는 메시지를
  stderr 에 쓰고 종료 2 — `batch.run_groups` 의 `keep_going` 이 삼키지 않게 한다(예: `SystemExit(2)`). `main` 의 docstring 영어, 기존 출력 그대로 —
  turns `test_us4_1`, `test_us4_4`, `test_cli_runs_without_fcntl`, 없는 파일·batch 시험 GREEN (T049) — closes US4-1, US4-4
- [X] T053 [P] [US4] `src/stt/enroll.py` `main`: 같은 잠금(같은 늦은 불러오기)을 `load_pipeline` 전부터 끝까지 — turns T049 의 enroll 시험 GREEN — closes US4-1
- [X] T054 [P] [US4] `src/stt/diarize.py` `load_pipeline`: `device == "cuda"` 인데 torch 가 CUDA 를 못 보면 CPU 로 계속하되 cli.md §3 의 영어 경고를
  stderr 에 한 번(R17, 헌법 V) — turns T050 GREEN — closes US4-5
- [X] T055 [US4] `src/stt/server/worker.py`·`src/stt/server/cli.py`·`tests/server/conftest.py`: 워커에 `gpu_lock: Path` 인자(명령줄 `--gpu-lock FILE` 필수);
  `queued` job 을 고른 뒤 `poll_s` 마다 `try_acquire` 를 시도하며 그동안 job 이 아직 `queued` 인지(지워짐·만료) 봄; 잠금을 얻은 뒤에만 `lifecycle.start`;
  엔진이 끝나면 풀고 `poll_s` 만큼 쉰 뒤 다음 job 의 잠금을 시도한다(R3). conftest 의 `run_worker`·`run_worker_once` 가 tmp 잠금 파일을 만들어
  넘기게 고치고, 인자가 필수가 되므로 T022 의 `stt server work` 호출 두 곳에 `--gpu-lock <tmp 파일>` 을 더한다(이 기능의 앞 단계 시험을 고치는
  명시적 예외 — 단언은 그대로) — turns T051 GREEN — closes US4-2, US4-3
- [X] T056 [US4] US4 확인: 전체 시험 통과(기존 명령줄 시험 포함). RED 해당 없음 — 확인 — closes US4-1…US4-5, SC-010(가짜 기준), SC-013

**Checkpoint**: GPU 를 둘이 함께 쥐는 경우가 없고, 잠금 변수가 없는 기계(Windows 포함)의 명령줄은 그대로다.

---

## Phase 7: User Story 5 - 한도와 실패를 정직하게 알린다 (Priority: P3)

**Goal**: 길이 초과·읽기 실패·시간 초과·GPU 메모리 부족·그 밖의 엔진 오류를 정해진 이유로 끝내고, 일부 결과를 내지 않는다. 말소리 없는 녹음은
문단 0개로 완료한다. (GPU 없음·모델 없음·잘못된 작업은 US1 에서 닫았다.)

**Independent Test**: 경우마다 시험 파일·가짜 모델을 맡겨 결과 없이 정해진 이유가 나오는지 본다.

### Tests for User Story 5 (MANDATORY - write first) ⚠️

- [X] T057 [P] [US5] `tests/server/test_us5.py` 에 더한다 — RED:
  `test_us5_1_over_three_hours_refused`(`probe_duration` 을 10,800.5 로 바꿔 끼움 → `failed(audio_too_long)`, 첨부 삭제; 10,800.0 은 받아들임);
  `test_us5_2_unreadable_file_refused`(임의 바이트 `.m4a` — 실제 PyAV → `failed(unreadable_audio)`, detail 이 고정 문장이고 경로·PyAV 원문 없음);
  `test_us5_3_timeout_stops_engine_without_result`(`sleep=30` 엔진, `processing_limit=lambda d: 0.5` 주입 → `processing_timeout`, 프로세스 묶음 없음,
  `outbox/<id>/` 없음, 작업 폴더 없음);
  `test_us5_9_short_recording_gets_five_minute_floor`(10초 녹음 job 에서 워커가 `processing_limit(10.0)` 을 부르고 300초 상한을 쓴다 — 감시 함수로 확인)
  — closes US5-1, US5-2, US5-3, US5-9 — verify RED
- [X] T058 [P] [US5] `tests/server/test_engine.py` 에 더한다 — RED:
  `test_us5_4_out_of_memory_fails_without_smaller_model`(가짜 모델이 `RuntimeError("CUDA failed with error out of memory")` → `gpu_out_of_memory`,
  `load_model` 이 `large-v3` 로만 불림, `out/` 비어 있음);
  `test_us5_7_silent_audio_completes_with_zero_paragraphs`(세그먼트 없음 → `done`, `paragraphs == []`, 전사록 = 머리글만, 화자 구분·`render_txt`·
  `render_diarized` 를 부르지 않음, 화자 구분 요청이면 `summary.diarized` 는 true — R21-18);
  `test_us5_8_unexpected_error_hides_internal_text`(예외 메시지 `"internal secret path"` → `engine_error`, detail 은 고정 영어 문장이고 그 글자 없음,
  stdout/stderr 에도 없음);
  `test_decode_failure_is_unreadable_audio`(PyAV 오류 → `unreadable_audio`, 고정 문장) — closes US5-4, US5-7, US5-8, SC-009 — verify RED
- [X] T059 [P] [US5] `tests/test_transcribe.py` 에 `test_transcribe_file_allow_empty`(R8-2: `allow_empty=True` 면 빈 목록, 기본값은 지금처럼 `RuntimeError`) — supports US5-7 — verify RED
- [X] T060 [P] [US5] `tests/server/test_logging.py` 에 더한다 — RED: 시간 초과·엔진 오류·해독 실패 경로에서도 힌트·요청자·전사문·예외 원문 0번 — supports FR-037, US5-8 — verify RED

### Implementation for User Story 5 (turn T057–T060 GREEN)

- [X] T061 [US5] `src/stt/transcribe.py` R8-2: `transcribe_file(..., allow_empty: bool = False)`; docstring 영어, 기존 오류 문구 그대로 — turns T059 GREEN — supports US5-7
- [X] T062 [US5] `src/stt/server/intake.py`: `probe_duration` 이 열 수 없음·음성 흐름 없음을 `failed(unreadable_audio)`, `deadlines.MAX_AUDIO_S` 초과를
  `failed(audio_too_long)` 으로(고정 문장, 첨부 삭제) — turns `test_us5_1`, `test_us5_2` GREEN (T057) — closes US5-1, US5-2
- [X] T063 [US5] `src/stt/server/worker.py`: `processing_limit` 인자(기본 `deadlines.processing_limit`)로 job 의 `duration_s` 에서 상한을 구하고, 엔진을
  띄운 때부터 단조 시계로 재어 넘으면 `os.killpg(SIGKILL)` → `finish(failed(processing_timeout))`, 게시하지 않고 작업 폴더 삭제 — turns `test_us5_3`, `test_us5_9` GREEN (T057) — closes US5-3, US5-9
- [X] T064 [US5] `src/stt/server/engine.py`: `allow_empty=True` 로 전사하고 빈 결과면 화자 구분과 renderer 를 건너뛰어 머리글만 쓴다(R20-11);
  `classify_error(exc) -> tuple[reason, detail]` — OOM(`transcribe.is_oom_error`) → `gpu_out_of_memory`, PyAV 해독 오류 → `unreadable_audio`, 그 밖 →
  `engine_error`, detail 은 이유마다 고정 문장; 실패 판정을 `outcome.json` 에 쓰고 종료 0, `out/` 은 비우고, 예외 원문·traceback 을 출력하지 않음 — turns T058, T060 GREEN — closes US5-4, US5-7, US5-8
- [X] T065 [US5] US5 확인: 전체 시험 통과. RED 해당 없음 — 확인 — closes US5-1…US5-9, SC-009

**Checkpoint**: 모든 이야기가 가짜 엔진·가짜 모델로 GREEN 이다.

---

## Phase 8: 배포 (Cross-Cutting) — 모델 받기·Nix 패키지·NixOS 모듈·VM 시험

**Purpose**: 호스트에 붙일 수 있게 묶고, 실제 systemd 위에서 권한·격리·잠금·삭제를 확인한다(quickstart B). FR-015·FR-035·FR-037·SC-002·SC-011.

- [X] T066 [P] `tests/server/test_models.py` 에 받기를 더한다 — RED: `fetch(models_dir, token, download)` 가 저장소마다 **고정 커밋**
  (`revision=<commit>`)·`cache_dir=<models_dir>/hub`·토큰으로 받기 함수를 부르고(가짜), `refs/main` 을 그 커밋으로 쓰고, 다 받으면 `.complete` 에 고정
  목록을 씀; `.complete` 의 목록이 같으면 받기 함수를 부르지 않음(망 없음); 목록이 다르면 다시 받음; 받기 실패면 `.complete` 를 쓰지 않고 어느
  저장소인지 영어로; 토큰 값이 출력에 없음; `stt server fetch-models` 종료 0·1·2 — supports FR-015, SC-002 — verify RED
- [X] T067 `src/stt/server/models.py` 의 `fetch` 와 `src/stt/server/cli.py` 의 `fetch-models --models DIR`: 위 동작(research R9), 토큰은 환경 변수
  `HF_TOKEN`(모듈이 전용 agenix 파일을 `EnvironmentFile` 로 준다) — turns T066 GREEN — supports FR-015, SC-002
- [X] T068 [P] `.github/workflows/ci.yml` 의 flake 작업을 `nix flake check --no-build` 로 바꾼다(R15). 서버 패키지·VM 확인을 flake 에 더하기
  **전에** 한다 — 순서가 바뀌면 그 사이에 올린 커밋에서 CI 가 CUDA 패키지와 VM 을 빌드한다. RED 해당 없음 — CI 설정. 확인: 로컬
  `nix flake check --no-build` 통과 — supports SC-013
- [X] T069 `flake.nix` 에 `checks.x86_64-linux.server-smoke`(runCommand: `${server}/bin/stt server --help`, `${server}/bin/python -c "from stt.server import
  contract, engine, models; contract.load_schema('request'); import jsonschema, av"`)를 먼저 쓴다 — 패키지가 없으므로 RED(`nix build
  .#checks.x86_64-linux.server-smoke` 실패, R20-18) — supports FR-015, SC-012 — verify RED
- [X] T070 `flake.nix`·`flake.lock`·`nix/package.nix`: 입력 pyproject-nix·uv2nix·pyproject-build-systems(research R1 의 고정 커밋), R1 부록의 덮어쓰기,
  `stt` 원본을 `pyproject.toml`·`uv.lock`·`README.md`·`src/` 로 좁힘, `packages.x86_64-linux.server`(`diarize`+`server` 묶음, ffmpeg 를 `PATH` 에 감싼 `stt`)와
  VM 시험용 `server-light`(`server` 묶음만). devShell 은 그대로. 확인: `nix build .#checks.x86_64-linux.server-smoke -L` 통과, `jsonschema`·`rpds-py` 가
  휠로 들어감, 이 호스트에서 `env -i ./result/bin/python -c "import torch; print(torch.cuda.is_available())"` True(드라이버 없을 때 0·False 는 R1 에서
  확인함) — turns T069 GREEN — supports FR-015, R1
- [X] T071 `nix/vm-test.nix`(nixosTest) 를 먼저 쓴다 — 모듈이 없으므로 RED: quickstart B 의 1–11(호출하는 쪽 역할 계정이 0640 파일로 맡김 → 수 초 안
  `queued`, `gpu_unavailable` 로 끝나고 `after_job` 녹음 삭제; GPU 잠금을 쥔 채 VM 시계 23:59:30 KST 에 맡긴 job → 자정 뒤 1분 안 녹음 없음·
  `failed(audio_deleted)`·`cause: midnight`; 23h58m + 1분 뒤 응답·기록 없음; 지우기 두 가지·남의 것·이미 지움·결과 없음; 중복·잘못된 이름·스키마
  위반·깨진 JSON·0600 요청·심볼릭 링크 첨부·3시간 넘는 녹음(ffmpeg 무음 저비트)·손상 파일·없는 `from_request`; `flock /run/stt/gpu.lock sleep 20`
  동안 `queued`(순서 1); `systemctl show` 의 `MemorySwapMax=0`·`LimitCORE=0`·`PrivateNetwork=yes`·워커 `DeviceAllow`·`RemoveIPC=yes`·
  `TimeoutStartUSec=infinity`·청소 타이머 `AccuracyUSec=1s`·`/var/lib/stt` 751; `journalctl` 에 시험 힌트·요청자 0번; 죽은 워커 흔적 정리; job 이 끝난
  뒤 워커가 다시 깨어나지 않음) — supports US1-1, US2-3, US2-5, US4-2, FR-015, FR-035, FR-037, SC-011 — verify RED(`nix build .#checks.x86_64-linux.server-vm` 실패)
- [X] T072 `nix/module.nix` 와 `flake.nix` 의 `nixosModules.default`·`checks.x86_64-linux.server-vm`: contracts/module.md §1–§3 그대로(선택지 셋, 계정·
  그룹, `StateDirectoryMode=0751`, tmpfiles 폴더·권한·inbox 기본 ACL·`/run/stt/gpu.lock`, `environment.variables.STT_GPU_LOCK`, 단위 넷과
  path·timer(`AccuracySec=1s`), 공통 격리 묶음 + `MemorySwapMax=0`·`LimitCORE=0`·`SuccessExitStatus=75`, 워커만 GPU 장치·`RemoveIPC=true`·
  `TimeoutStartSec=infinity`·`after`/`wants` 모델 받기 단위, 모델 받기 단위만 망·조건 없이 부팅마다·`RemainAfterExit=true`·`EnvironmentFile`,
  **`HF_HOME`·`HF_HUB_OFFLINE` 을 단위 환경에 두지 않음**(워커가 엔진에만 넣는다), `LD_LIBRARY_PATH` 없음). 확인: `nix build
  .#checks.x86_64-linux.server-vm -L` 통과 — turns T071 GREEN — supports FR-015, FR-035, FR-037, SC-011
- [X] T073 [P] `README.md` 에 서버 절(영어): 하는 일·하지 않는 일, 요청함 규약은 `specs/001-server-transcription/contracts/README.md` 로 연결,
  `stt server` 명령, `STT_GPU_LOCK`, 호스트의 몫(module.md §4 — 전용 토큰 비밀, `nvidia_uvm`), 개인정보 삭제 약속. RED 해당 없음 — 문서 — supports FR-023

---

## Phase 9: Polish & 최종 확인

- [X] T074 [P] 언어 규칙 확인: `rg -n '[\x{1100}-\x{11FF}\x{3130}-\x{318F}\x{AC00}-\x{D7AF}]' src/stt/server src/stt/gpu_lock.py tests/server
  tests/test_gpu_lock.py tests/test_cli_gpu_lock.py nix` 0건; `git diff master -- src/stt/transcribe.py src/stt/diarize.py src/stt/enroll.py src/stt/cli.py
  tests/test_transcribe.py tests/test_diarize.py` 의 더해진 줄에서 한글이 나오면 하나하나 「기존 사용자 출력 문자열을 그대로 옮긴 것」인지 확인하고,
  아니면 고친다(R19). RED 해당 없음 — 확인 — supports 헌법 언어 규칙
- [X] T075 quickstart A: `uv sync --locked --extra server && uv run python -m pytest -q` 전부 통과, 시나리오 40개 + 경계 사례 8개가 각각 시험 하나
  이상에 대응하는지 `rg -o 'def test_(us[1-5]_[0-9]+|edge_[1-8])_' tests` 로 세어 빠짐이 없음. RED 해당 없음 — 확인 — supports SC-012, SC-013
- [ ] T076 quickstart C(호스트 시험, 운영자와 함께, 배포 뒤): 1–11 을 실행하고(11 은 고정 모델 목록을 처음 바꾸는 배포 때) `specs/001-server-transcription/handoff/<날짜>.md` 에 기록 — SC-001 실측
  (1시간 녹음 1차·2차), SC-002 connect 시도 0(strace), R2 `[VERIFY]`(GPU 장치 넷으로 엔진이 GPU 를 봄) 닫기, 재부팅 뒤 첫 job(`nvidia_uvm`),
  SC-005·SC-007·SC-010, FR-037, 경계 사례 6(C-7: 망 없는 환경에서 화자 구분 전사 완료), 모델 받기 단위가 판이 바뀐 배포에서 다시 도는지(R9 `[VERIFY]`). RED 해당 없음 — 운영 확인 — closes 경계 사례 6, supports SC-001, SC-002, SC-007
- [ ] T077 `docs/handoff/bhu-llm-2026-10-15.md` §7 의 S1–S11 칸을 T076 결과와 계약 위치로 채우고, 호출하는 쪽에 넘길 계약 요점(응답 꼴 두 가지,
  파일 권한 0640·0660, 5분 응답 없음 = 실패, 응답이 사라지면 만료, 지우기는 「다 지우기」와 매일 23:58 정리뿐(R22), 「GPU 없음」과 매일 정리의
  이용자 안내는 호출하는 쪽 몫)을 적는다. RED 해당 없음 — 문서 — supports 헌법 VI
- [ ] T078 T-Verify-Final: `project-audit` 을 한 번 돌리고, 지적을 고치거나 이유와 함께 기록한다(헌법 「개발 환경과 흐름」). RED 해당 없음 — 감사 — supports 헌법 「개발 환경과 흐름」

---

## Phase 10: 운영자 결정 R22 — 삭제 단순화와 GPU 전용 (2026-10-22, 병합 뒤)

**Purpose**: spec 개정(`b0112ae`, 헌법 1.1.0)을 코드에 옮긴다. 근거: research R22, data-model §1·§2.2·§2.3·§3·§4·§5·§6·§9, 계약 스키마(이미 고침 —
`contracts/*.schema.json` 과 `src/stt/server/schemas/` 사본이 같다). 001 의 다른 규칙(위 「모든 과제에 걸리는 규칙」)이 그대로 걸린다.
시나리오 번호는 개정된 spec 의 것이다 — 이름이 옛 번호인 시험은 새 번호로 고쳐 단다.

### 서버: 「다 지우기」와 매일 정리 (US2, US3)

- [X] T079 [US2] 서버 시험을 새 규칙으로 바꾼다 — RED: `tests/server/test_deadlines.py`(`next_purge`: KST 23:57:59 → 그날 23:58, 23:58:00 →
  다음 날 23:58, 23:58:01 → 다음 날, UTC 로 돌려줌; `RETENTION_DUE`·`next_midnight` 없음), `test_contract.py`(`delete_audio` 있는 transcribe·`scope`
  있는 delete → `invalid_request`, 없는 것은 통과; 응답 `audio` 에 `delete_policy` 가 있으면·delete 답에 `scope`·`no_result` 가 있으면·`failure`
  에 `cause` 가 있으면 스키마 위반), `test_us2.py`(개정 US2-1…10 을 `test_us2_<n>_…` 로 — 2 는 1차·2차 둘 다 `deleted` 이고 녹음·두 결과 0개, 5 는
  완료·실패·대기·처리 중 job 이 23:58 직후 응답·기록·결과·녹음 0개, 6 은 23:58:30 에 받은 job 이 그날 정리 뒤 남고 다음 날 23:58 에 사라짐),
  `test_us3.py`(개정 US3-1…4), `test_edge_cases.py`(경계 사례 2·3·8 새 문구), `test_lifecycle.py`·`test_records.py`·`test_responses.py`·
  `test_intake.py`·`test_logging.py`·`conftest.py`·`inbox_helpers.py`(요청에서 `delete_audio`·`scope` 를 뺀다). 옛 규칙만 확인하던 시험
  (「끝나면 바로」·「결과 지우기」·`no_result`·`retention_limit`·`cause`·I2 after_job 복구)은 지운다 — 보고서에 지운 시험 이름을 적는다.
  SC-003 표(지우는 길 2 × 종료 3)를 시험 하나로 둔다 — closes US2-1…10, US3-1…4, 경계 사례 2·3·8, SC-003, SC-004, SC-005 — verify RED
- [X] T080 [US2] 서버 구현 — `deadlines.next_purge`(23:58 `Asia/Seoul`, data-model §1) 로 모든 기한을 바꾸고 `RETENTION_DUE`·`next_midnight`
  을 없앤다; `records`(Job 의 `delete_audio`, Audio 의 `policy`·`hard_deadline` → `delete_at`, `deleted_by` ∈ {`purge`,`request`});
  `responses`(`audio` = `{delete_at, deleted_at?}`, delete 답에 `scope` 없음, `failure` 에 `cause` 없음); `lifecycle`(`finish` 가 녹음을 지우지
  않음, `delete_result` 없앰, 「다 지우기」가 `audio.users` 의 모든 job 을 `deleted` 로 — 대기 표시·힌트·결과·작업 폴더 정리, 처리 중이면 워커가
  엔진을 멈춤, 녹음 기한 도래 때 대기·처리 중 job 은 `failed(audio_deleted)`); `intake`(정책 설정·`scope` 분기 없앰); `worker`·`sweep`(기한 판정을
  `delete_at` 으로, 청소 차례는 그대로). 이 기능에서 생긴 남은 지적(I2 「기록 없음」 갈래)은 대상이 없어지므로 함께 없앤다 — turns T079 GREEN
- [ ] T081 [P] 서버 배포·문서 — `nix/vm-test.nix` 의 요청에서 `delete_audio`·`scope` 를 빼고 지우기 확인을 「다 지우기」로 바꾼다(매일 정리는
  VM 시계를 옮기지 않으면 볼 수 없으므로 pytest 몫); `quickstart.md`·`contracts/README.md`(요청·응답 설명, §7 「응답이 사라지면 만료」 곁에 매일
  정리)·`README.md` 서버 절을 R22 로 고친다. 확인: `nix build .#checks.x86_64-linux.server-vm -L` 통과, `rg -n 'delete_audio|at_midnight|after_job|
  no_result|retention_limit|RETENTION_DUE|23h58|23 h 58' src nix specs/001-server-transcription/{quickstart.md,contracts} README.md` 0건(`tests` 는 없음을
  확인하는 시험이 이 낱말을 쓰므로 뺀다), `tests/server/test_contract.py::test_readme_examples_validate` 통과.
  RED 해당 없음 — 배포 선언·문서 (VM 시험이 확인) — supports US2-1, US2-3, FR-005

### 명령줄: GPU 전용 (US4)

- [ ] T082 [US4] 명령줄 시험 — RED: `tests/test_transcribe.py` 에 `--device cpu` → argparse 오류(종료 2); GPU 없음(`ctranslate2.get_cuda_device_count`
  → 0)에서 `--device auto`·`cuda` 모두 모델을 올리기 전에 stderr 에 `No usable GPU was found; stt runs on the GPU only.` 를 내고 종료 1(모델 적재
  함수가 불리지 않음), GPU 잠금은 장치와 상관없이 쥠; `tests/test_diarize.py` 에 torch 가 CUDA 를 못 쓰면 `load_pipeline` 이 `RuntimeError(
  "Speaker diarization needs a GPU, but torch cannot use CUDA; stt runs on the GPU only.")` 을 내고 CPU 경고는 없음; `enroll`(torch CUDA 없음 →
  같은 문구, 종료 1)·`batch`(설정 `device: cpu` → 실행 전에 거절) 시험. 기존 시험 가운데 CPU 실행을 전제한 것(CPU 경고·`small` 기본 모델·`detect_device("cpu")`
  등)만 GPU 전용 동작에 맞게 바꾸고 보고서에 이름과 까닭을 적는다(SC-013) — closes US4-5, US4-6, SC-014 — verify RED
- [ ] T083 [US4] 명령줄 구현 — `transcribe`·`enroll` 의 `--device` 는 `auto`·`cuda` 만, `detect_device` 는 CUDA 장치가 없으면 위 문구로 멈춤(CPU 를
  돌려주지 않음), `CPU_DEFAULT_MODEL`·CPU 갈래·CPU 경고·`--device cpu` 안내 문구를 없앰, `diarize.load_pipeline` 은 CUDA 가 없으면 위 오류,
  `batch` 는 `device` 설정을 같은 두 값으로 검사; 고치는 함수의 docstring 은 영어로. GPU 메모리 부족 때 작은 모델로 넘어가는 폴백은 그대로(FR-040).
  `README.md`·`INSTALL.md` 의 CPU 안내(CPU-only 서버 절, `--device` 표, 문제 해결 표)를 GPU 전용으로 고친다 — turns T082 GREEN

### 확인

- [ ] T084 언어 규칙(T074 와 같은 `rg`) 0건, `uv run python -m pytest -q` 전부 통과, 개정 spec 의 시나리오(US1 8 · US2 10 · US3 4 · US4 6 · US5 9 = 37개)
  와 경계 사례 8개가 각각 시험 하나 이상에 대응(`rg -o 'def test_(us[1-5]_[0-9]+|edge_[1-8])_' tests`), Python 3.10 환경에서도 전부 통과,
  `nix flake check --no-build` 통과. RED 해당 없음 — 확인 — supports SC-012, SC-013

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → 이야기들.
- **US1 (Phase 3)**: Phase 2 뒤. 다른 이야기에 기대지 않는다.
- **US2 (Phase 4)**: US1 뒤(받기·워커·lifecycle 에 삭제를 더한다).
- **US3 (Phase 5)**: US2 뒤(US3-2·US3-5 가 정책 집행·청소를 쓴다).
- **US4 (Phase 6)**: US1 뒤. 명령줄 쪽(T049, T050, T052–T054)은 Phase 2 뒤 바로 할 수 있다. 워커 쪽(T051, T055)의 `test_delete_while_waiting_for_gpu` 는 US2 가 필요하다.
- **US5 (Phase 7)**: US1 뒤. `test_us5_3` 의 「작업 폴더 없음」은 US2 의 정리를 쓴다 → US2 뒤에 하는 것이 안전하다.
- **배포 (Phase 8)**: 모든 이야기 뒤(VM 시험이 모든 경로를 본다). T066–T067 은 US1 뒤 언제든. T068(CI)은 T069·T071 이 flake 에 확인을 더하기 전에.
- **Polish (Phase 9)**: T076 은 운영자의 배포(nixos-config — module.md §4, `boot.kernelModules`·전용 토큰 비밀 포함) 뒤.

### Within Each Phase

- 시험 과제를 먼저 쓰고 실패(RED)를 본 뒤 구현. 구현 과제는 적힌 시험만 GREEN 으로 바꾸고, 기존 시험이 깨지면 구현을 고친다. 이 기능 전부터
  있던 시험(243개)은 고치지 않는다. 이 기능의 앞 단계 시험을 고쳐야 하면 그 과제에 명시한다(T055).
- 같은 파일을 고치는 과제는 차례로: `intake.py`(T030 → T040 → T046 → T062), `worker.py`(T031 → T041 → T047 → T055 → T063),
  `lifecycle.py`(T029 → T039), `engine.py`(T032 → T064), `models.py`(T028 → T067), `transcribe.py`(T025 → T026 → T052 → T061),
  `diarize.py`(T027 → T054), `server/cli.py`(T033 → T042 → T055 → T067), `flake.nix`(T069 → T070 → T072), `.github/workflows/ci.yml`(T004 → T068),
  `tests/server/test_edge_cases.py`(T019 → T036 → T045), `tests/server/test_logging.py`(T023 → T038 → T060), `tests/server/test_models.py`(T024 → T066),
  `tests/server/test_server_cli.py`(T022 → T037 → T055), `tests/server/test_us5.py`(T019 → T057), `tests/server/test_engine.py`(T020 → T058),
  `tests/test_diarize.py`(T021 → T050), `tests/test_transcribe.py`(T021 → T059), `tests/server/conftest.py`(T003 → T055).

## Parallel Opportunities

- Phase 1: T002, T003, T004.
- Phase 2: 시험 T005, T007, T009, T011, T013, T015 를 함께 쓰고, 구현 T006, T008, T010, T012, T014, T016 은 서로 다른 파일이라 함께 할 수 있다
  (T012 는 T010 의 `atomic_write_text` 를, T014 는 T012 를 쓴다 — 그 순서만 지킨다).
- US1: 시험 T017–T024 를 함께. 구현은 T025·T027·T028 함께 → T026·T029 → T030·T032 함께 → T031 → T033.
- US4 의 명령줄 쪽(T049·T050·T052·T053·T054)은 US2·US3 와 함께 할 수 있다.
- Phase 8: T066, T068, T073 함께.

### Parallel Example: User Story 1

```text
# 시험을 함께 쓴다(모두 RED 확인):
T017 tests/server/fake_engine.py
T018 tests/server/test_us1.py
T019 tests/server/test_us5.py + test_edge_cases.py
T020 tests/server/test_engine.py
T021 tests/test_transcribe.py + tests/test_diarize.py
T022 tests/server/test_server_cli.py
T023 tests/server/test_logging.py
T024 tests/server/test_models.py

# 서로 다른 파일의 구현을 함께:
T025 src/stt/transcribe.py (R8-1)   T027 src/stt/diarize.py (R8-4)   T028 src/stt/server/models.py
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 → Phase 2 → Phase 3(US1).
2. **멈추고 확인**: 가짜 엔진·가짜 모델로 받기 → 게시가 돈다. 배포는 하지 않는다 — 삭제(US2)가 없는 서버는 헌법 III 때문에 열 수 없다.

### Incremental Delivery

1. US1 + US2 = 배포할 수 있는 최소(P1 둘).
2. US3(2패스) → US4(GPU 줄) → US5(정직한 실패).
3. Phase 8 로 묶고 VM 시험 → 운영자가 nixos-config 에 붙임 → Phase 9 호스트 시험.
4. 실제로 호스트에 여는 것은 US4 까지 끝난 뒤다 — 운영자가 이 서버에서 손 전사를 하므로 GPU 줄 없이 열면 US4-3 위반이 난다.

## Notes

- 시나리오 ↔ 과제: US1-1·7·8 → T018 · US1-2…6 → T020 · US5-5 → T020 · US5-6 → T019 · US2-* → T035 · US3-* → T044 · US4-1·4 → T049 ·
  US4-5 → T050 · US4-2·3 → T051 · US5-1·2·3·9 → T057 · US5-4·7·8 → T058 · 경계 사례 1 → T019, 2·3 → T036, 4·5 → T020, 6 → T021(토큰)·T076(망 없는 완료), 7 → T045, 8 → T035(워커)·T044(받기).
- spec 이 바뀌면 spec 을 먼저 고치고 과제를 고친다(전역 §3.5).
- 과제 하나 또는 논리 묶음마다 커밋(Conventional Commits, 영어).
