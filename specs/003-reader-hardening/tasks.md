---

description: "Task list for 003-reader-hardening"
---

# Tasks: 녹음 읽기 추가 좁히기와 알아차리기

**Input**: `specs/003-reader-hardening/` — [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md),
[contracts/](contracts/) ([reader.md](contracts/reader.md)·[operator-log.md](contracts/operator-log.md)·[module.md](contracts/module.md)), [quickstart.md](quickstart.md)

**Prerequisites**: plan.md, spec.md (US1–US4, 수용 시나리오 20개, 경계 사례 6개, SC-001~006)

**Tests**: 필수(헌법 VII). 이야기마다 시나리오를 시험으로 옮기는 과제가 먼저 오고, 그 시험이 **실패하는 것(RED)을 본 뒤에** 구현 과제가
통과(GREEN)시킨다. pytest 이름은 `tests/server/test_hardening_us<N>.py` 의 `test_hardening_us<N>_<n>_...`(수용 시나리오),
`test_hardening_edge_<n>_...`(경계 사례), 그 밖은 뜻을 담은 이름(research R12). VM 시험은 `subtest` 제목에 quickstart 단계 이름(B-003-n)을 넣는다.

**Organization**: plan 의 단계표를 따른다. 배포는 한 번이다. US1(P1)이 먼저이고, P2 둘 가운데 US3 을 US2 보다 먼저 한다 — 잘림 줄이 놓일 워커 읽기
단계의 예외 구조를 US3 이 정한다(plan 「Phase 계획」). VM 시험은 무거우므로(60분) 모든 이야기의 VM 단언을 Phase 2 에서 한꺼번에 쓰고 한 번 RED 를
본 뒤(T005), 모든 구현이 끝나고 한 번 GREEN 을 본다(T028).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 다른 파일이고 끝나지 않은 과제에 기대지 않아 함께 할 수 있다
- **[Story]**: 이 과제가 속한 이야기(US1…US4)
- **RED → GREEN**: 시험 과제는 「verify RED」와 무엇으로 실패하는지를 적고, 구현 과제는 자신이 GREEN 으로 바꾸는 시험을 적는다. 동작을 바꿀 수
  없는 과제(시험 도구·문서·배포·호스트 확인·감사)는 「RED 해당 없음 — <이유>」를 적는다(헌법 VII 의 예외)
- **Scenarios**: 과제마다 닫는 시나리오를 적는다(`closes US1-2`). 받치기만 하는 과제는 `supports …` 를 적는다

## 모든 과제에 걸리는 규칙

- **공격 파일 없음**(FR-017, 002 FR-028): 시험 녹음은 PyAV·nixpkgs `ffmpeg` 로 만든 사인파·무음·단색 영상·단색 그림과 그 끝을 자른 것, 녹음 확장자를
  붙인 무해한 텍스트 파일 하나뿐이다. 해독기를 노리는 파일·퍼징한 파일은 만들지도 내려받지도 보관하지도 않는다. 인코더가 없는 코덱(WMA Pro·Voice·
  Lossless, AMR-WB)의 시험 파일을 밖에서 받지 않는다(research R2).
- **운영자 명령줄 그대로**(FR-005): `open_container` 의 새 키워드는 읽기 자식만 넘긴다. `decode_samples` 는 바뀌지 않는다.
- **계약 그대로**(FR-015): `src/stt/server/schemas/*.json`, 실패 이유, 응답의 고정 문장, 「다 지우기」, 재전사, 23:58 정리는 바꾸지 않는다. 원인 이름과
  잘림은 운영자 기록에만 있다.
- **코드에 한글 없음**: 새 코드·시험의 식별자·docstring(Google 형식)·주석·로그 문장은 영어다. 과제 글과 `docs/server-security.md` 의 설명은 한국어다.
- **문서 대조 시험은 영어 표식만 읽는다**: `<!-- stt:audio-decoders -->`·`<!-- stt:engine-error-causes -->`·`<!-- stt:truncation threshold_s=… -->`,
  영어 칸 이름(`decoder`·`cause`), 영어 줄 그대로(contracts/operator-log.md §5). 시험 코드에 한글 문자열을 쓰지 않는다.
- **Python 3.10**: CI 가 3.10·3.12 를 돈다. `-P`·`tomllib`·`datetime.UTC` 를 쓰지 않는다(research R10).
- **셸 없음·시험용 분기 없음**: 하위 프로세스는 인자 목록으로만. 운영 코드에 시험용 길을 내지 않는다 — 감시 라이브러리는 VM 노드 설정에만 있다.
- **실행**: 무거운 실행은 모두 `captest` 로 돌린다(전역 §5). pytest: `captest 4G 20m uv run --locked python -m pytest -q <파일>`(xdist 없음).
  Python 3.11: `UV_PROJECT_ENVIRONMENT="$HOME/.cache/stt-py311" captest 4G 25m uv run --locked --python 3.11 --extra server python -m pytest -q`.
  평가 검사: `captest 8G 15m nix build .#checks.x86_64-linux.module-eval -L`. VM: `captest 8G 60m nix build .#checks.x86_64-linux.server-vm -L`.
  찍힌 최대값을 보고 상한을 맞추고, 137·124 이면 원인을 찾는다(그냥 다시 띄우거나 상한을 올리지 않는다).
- **커밋**: 과제 하나 또는 논리 묶음마다 Conventional Commits(영어). RED 를 본 시험은 그 실패 모양(예외·단언)을 커밋 메시지에 적는다.
  **push·합치기·배포는 운영자 승인 뒤에만**.

---

## Phase 1: Setup — 시험 도구

**Purpose**: 아래 이야기들의 pytest 가 쓸 시험 녹음 도우미와 대역. 동작을 바꾸지 않는다.

- [X] T001 [P] `tests/server/conftest.py` 에 PyAV 로 시험 녹음을 만드는 도우미를 더한다(research R12): (a) MP4 안 AAC 사인파 + 단색 MPEG-4·H.264 영상,
  (b) MP3(`libmp3lame`) 사인파 + 단색 PNG 표지(`Disposition.attached_pic` 흐름에 PNG 묶음 하나), (c) 단색 H.264 만 든 MP4(음성 없음), (d) 색인이 앞인
  M4A(`movflags=+faststart`), (e) 음성 흐름 둘인 MP4(서로 다른 주파수), (f) 더한 녹음기 코덱 — WAV 안 IMA·Microsoft ADPCM, MP2(`.mp3`·`.wav`),
  MP4 안 AC-3·E-AC-3, 설치된 PyAV 에 `libopencore_amrnb` 인코더가 있을 때만 3GP 안 AMR-NB(`.m4a`) — 인코더가 없으면 이유를 적은 `pytest.skip`,
  (g) 목록 밖 코덱 — WAV 안 Yamaha ADPCM, MOV(`.m4a`) 안 QuickTime IMA4, (h) 파일을 주어진 비율로 `truncate` 하는 끝 자르기와, 색인이 앞인 M4A 를
  `mdat` 머리(8바이트) 바로 뒤에서 자르는 도우미. 모두 사인파·단색만(FR-017). 도우미마다 만든 파일의 흐름 종류·코덱 이름을 한 번 단언하는 작은
  자기 시험을 둔다(인코더가 없는 판은 건너뜀). RED 해당 없음 — 시험 도구(자기 시험은 도구가 맞게 만들었는지만 본다). supports US1-1~5, US2-1~5,
  경계 사례 1·2, FR-017
- [X] T002 [P] `tests/server/fake_decode_child.py` 에 동작 `sigint-parent` 를 더한다: 부모(감독)에게 `SIGINT` 를 보내고, 정상 probe 결과를 쓴 뒤 끝 코드 0
  (contracts/reader.md §7). 녹음 내용을 해석하지 않는다. RED 해당 없음 — 대역(이 동작을 쓰는 시험 T021(a)가 RED 를 본다). supports US4-1

---

## Phase 2: Foundational — VM 시험 기반과 한 번의 RED (모든 이야기의 B 층)

**Purpose**: 진짜 읽기 단위 위에서 US1~US4 를 볼 VM 단언을 먼저 모두 쓰고, 지금 코드로 한 번 돌려 실패를 본다(plan 「VM 시험은 무거우므로 …
한 번 RED」). 구현은 Phase 3~6 에서, GREEN 은 T028 에서 본다.

**⚠️ CRITICAL**: T005(RED 기록)가 끝나야 Phase 3 의 구현 과제(T008·T009)를 시작한다 — 감시 라이브러리의 RED 는 목록이 없는 코드에서만 볼 수 있다.

- [X] T003 `nix/vm-test.nix`: 해독기 감시 라이브러리(contracts/module.md §3) — `pkgs.runCommandCC` 로 만든 공유 라이브러리(002 `hangingCuda` 와 같은
  방식, 영어 주석). `avcodec_open2` 를 가로채, 프로세스에 실린 `libavcodec` 의 진짜 함수(`dl_iterate_phdr` → `dlopen(RTLD_NOLOAD)`)가 0 을 돌려줬는데
  문맥의 `codec_type` 이 음성이 아니면 `abort()`. `server` 노드에만 `systemd.services."stt-reader@".environment.LD_PRELOAD` 로 싣는다(운영 모듈에는
  없음). 같은 과제에서 `audio` 파생물에 시험 녹음 15개를 더한다(contracts/module.md §4 표 그대로: `tone-video.m4a`·`tone-video.wma`·`tone-cover.mp3`·
  `video-only.m4a`·`tone-adpcm-ima.wav`·`tone-adpcm-ms.wav`·`tone-mp2.mp3`·`tone-ac3.m4a`·`tone-eac3.m4a`·`tone-speex.ogg`·`cut-tone.mp3`·
  `cut-tone.flac`·`cut-faststart.m4a`·`nopackets-faststart.m4a`·`tone10.mp3`). 모두 nixpkgs `ffmpeg` 의 사인파·단색 영상·단색 PNG 와 `head -c` 로 자른
  것이다(FR-017). RED 해당 없음 — 시험 도구(T005 가 이 도구로 RED 를 본다). supports SC-001, SC-003, US1-1~5, US2-1·2·4
- [X] T004 `nix/vm-test.nix` 에 부분 시험을 쓴다(quickstart B 그대로). 자리는 `journal_cursor()` 가 정의된 `nix/vm-test.nix:711` 뒤다 — B-P2-8
  (`:483`)은 커서 없이 읽기 단위 저널 전체에 `reader child` 줄이 없음을 보므로, B-003-1(3)의 줄이 그 앞에 쓰이면 B-P2-8 이 깨진다. B-003 부분
  시험들은 단언마다 멈추지 않고 실패를 목록에 모았다가 B-003 끝에서 한 번에 단언한다(실패 목록을 모두 찍는다) — 그래야 T005 의 한 번 실행이 모든
  B-003 RED 를 보인다. **B-003-1** — (1) B-P2-8 목록에 더한 녹음기 코덱 다섯을 더해 감시 아래에서 probe·
  decode `ok`, 2초 ± 0.35, 최대 진폭 0.08~0.2. (2) `tone-video.m4a`·`tone-video.wma`·`tone-cover.mp3` 를 `readFormats` 로 읽어 모두 `ok`, 같은 길이·
  진폭, 커서 이후(`journalctl --sync` 뒤) `type=1326`·`reader child` 줄 없음. (3) `video-only.m4a`·`tone-speex.ogg` 는 probe·decode 모두 `failed`, 파일마다
  `reader child probe exited with status 1`·`reader child decode exited with status 1` 하나씩, `killed by signal` 없음. **B-003-2** — 잘린 넷과
  `tone10.mp3` 를 한 묶음으로 맡겨 모두 `queued`, 문서의 확인 명령(`journalctl -u stt-worker -g ' running$|shorter than the probed length'`)을 커서
  이후로: 잘린 넷은 각자의 `<id> running` 바로 다음이 잘림 줄, `tone10.mp3` 다음에는 없음, 잘림 줄 정확히 4. **B-003-3** — (1) 기존 B-P2-5 의
  `reader-not-started` 대역에서 워커 줄이 `<id> failed engine_error reader_unavailable`. (2) `hold_gpu` 동안 맡긴 작업의 `audio/<id>/audio.wav` 를 root 로
  지우고 같은 이름의 FIFO 를 둔 뒤 잠금을 풀면: 응답 `engine_error`·「The server could not start the audio reader.」, 줄 `… recording_unusable`,
  바로 뒤의 정상 작업이 `gpu_unavailable` 까지 가고 `systemctl --failed` 에 stt 단위가 없음. 모든 기다림은 `wait_until_succeeds(…, timeout=…)` 로
  묶어 지금 코드의 FIFO 막힘이 VM 전체를 붙잡지 않고 실패로 나오게 한다. **B-003-4** — `${package}/bin/stt server read </dev/null`(root): 끝 코드 2,
  표준 오류가 `MANUAL_RUN_LINE` 한 줄. 시험 녹음은 T003 의 것뿐이다. RED 는 T005 에서 본다(이 과제는 단언을 쓰기만 한다).
  supports US1-1~5, US2-1·2·4, US3-1~3, US4-4, SC-001~004
- [X] T005 VM RED 기록: `captest 8G 60m nix build .#checks.x86_64-linux.server-vm -L` 를 지금 코드(T003·T004 만 더한 상태)로 돌려 실패를 보고, 실패한
  부분 시험과 줄을 커밋 메시지에 적는다. 기대: B-003-1(2)에서
  `reader child probe killed by signal 6`(영상·표지 그림 해독기가 열림), B-003-1(3)의 `tone-speex.ogg` probe `ok`, B-003-2 잘림 줄 0, B-003-3 줄 끝
  원인 없음과 FIFO 의 시간 넘김, B-003-4 빈 표준 오류. T004 의 모은 실패 목록이 한 번의 실행에서 B-003 RED 를 모두 보인다(다시 돌리지 않는다). **해결 `[VERIFY]` B-003-1**: B-003-1(2)에서 `killed by signal 6` 이 나오면 감시 라이브러리가 읽기 단위의 제한
  (동적 사용자·`MemoryDenyWriteExecute`·호출 필터·`TemporaryFileSystem`) 안에서 실려 일한다는 것이 확인된다. 나오지 않으면(라이브러리가 실리지
  않거나 호출 필터에 막힘) 구현으로 가지 않고 원인을 research R12 에 적은 뒤 운영자와 대안을 정한다. verify RED: 위 기대대로 실패.
  closes `[VERIFY]` B-003-1, supports SC-001

**Checkpoint**: VM 단언이 모두 있고, 지금 코드에서 실패하는 것을 봤다. 감시 라이브러리가 진짜 읽기 단위 안에서 일한다.

---

## Phase 3: User Story 1 - 서버는 음성 해독기만 연다 (Priority: P1) 🎯 MVP

**Goal**: 읽기 자식이 `codec_whitelist` 를 형식 단계와 흐름마다의 코덱 문맥 두 겹으로 걸고, 길이 재기가 첫 음성 흐름의 해독기 이름을 확인한다.
목록은 `reader.AUDIO_DECODERS`(32개) 한 곳과 보안 문서 §2 표에만 있다.

**Independent Test**: `test_hardening_us1.py` 와 문서 대조 시험이 통과하고, 영상·표지 그림이 든 녹음의 결과가 음성만 든 녹음과 같으며 비음성 흐름의
해독기가 열리지 않는다(`pix_fmt is None`).

### Tests for User Story 1 (write first) ⚠️

- [X] T006 [US1] `tests/server/test_hardening_us1.py`(새), T001 의 녹음과 기존 흉내 읽기 소켓(진짜 감독·자식)으로:
  (a) `test_hardening_us1_1_*` — 형식 8가지(기존 `encode_tone`·`make_wav`)와 ALAC·PCM 변형을 `open_container(..., codec_whitelist=reader.AUDIO_DECODERS)`
  로 연 길이 재기 값과 해독 바이트가 목록 없이 연 것과 같고, 진짜 감독·자식으로 probe·decode 가 `ok`·`ok`. (b) `test_hardening_us1_2_*`·
  `test_hardening_edge_1_*` — AAC + 단색 MPEG-4/H.264: 진짜 자식의 probe 값·decode 바이트가 같은 음성만 든 녹음과 같다. 프로세스 안에서
  `reader._open_container` 로 연 영상 흐름의 `codec_context.pix_fmt is None` 이고 `decode(video=0)` 이 `av.error.ArgumentError`. 받기 → 워커(가짜 엔진)
  로 `done`. (c) `test_hardening_us1_3_*` — MP3 + 단색 PNG 표지로 (b)와 같은 단언. (d) `test_hardening_us1_4_*` — 영상만 든 MP4 → 받기
  `unreadable_audio`, 영상 흐름 `pix_fmt is None`. (e) `test_hardening_us1_5_*` — Yamaha ADPCM WAV·QuickTime IMA4 `.m4a` → 받기 `unreadable_audio`.
  해독 자식을 직접 부르면 decode 끝 코드 1(`ArgumentError`). 배포 전에 받아 둔 같은 녹음의 작업(작업 기록을 도우미로 직접 둔다)의 처리 → 해독
  `failed` → `unreadable_audio`. 길이 재기의 이름 확인 순수 함수 `reader.first_audio_decoder_allowed(container)` 를 가짜 컨테이너로(음성 흐름 없음·`codec_context is None`·
  목록 밖 이름 → 거짓, 목록 안 이름 → 참, contracts/reader.md §3).
  (f) `test_hardening_edge_2_*` — 음성 흐름 둘: 첫 흐름만, 바이트 그대로. (g) 더한 녹음기 코덱 — IMA·MS ADPCM, MP2(`.mp3`·`.wav`), AC-3·E-AC-3,
  (인코더가 있으면) AMR-NB: 진짜 감독·자식으로 probe·decode `ok`, 사인파 길이. WMA Pro·Voice·Lossless·AMR-WB 는 인코더가 없어 시험이 없음을
  모듈 docstring 에 적는다(T034 의 운영 확인으로 넘김). (h) FR-005 — `stt.audio_decode.decode_samples` 는 목록 없이 Yamaha ADPCM 도 해독한다.
  (i) `AUDIO_DECODERS` 의 각 이름이 설치된 PyAV 에서 `av.codec.Codec(name, "r").type == "audio"` 이고 영상·이미지·자막 이름이 없다(FR-001).
  verify RED: `reader.AUDIO_DECODERS` 가 없어 ImportError, `open_container` 가 `codec_whitelist` 를 모름(TypeError). 그 둘을 잠깐 가짜로 채워
  보면 (b)(c)의 `pix_fmt` 가 `None` 이 아니고 (e)의 probe 가 `ok` 다 — 이 실패 모양을 커밋 메시지에 적는다. (a)(g)(h)는 지금 코드에서 이미 참인
  회귀 지킴이다(T009 의 돌연변이 확인 셋이 RED 를 대신한다). closes US1-1, US1-2, US1-3, US1-4, US1-5, 경계 사례 1·2, SC-001(pytest), SC-002(앞),
  supports FR-001~003, FR-005
- [X] T007 [P] [US1] (a) `tests/test_server_security_doc.py` 에 더한다: `<!-- stt:audio-decoders -->` 표의 `decoder` 칸 값 집합 ==
  `set(reader.AUDIO_DECODERS.split(","))`, 겹침 없음. (b) `tests/server/test_audio_decode.py` 에 더한다: 가로챈 `av.open` 과 열린 컨테이너로: `open_container(..., codec_whitelist=X)` 가
  `container_options["codec_whitelist"] == X` 를 넘기고 모든 흐름의 코덱 문맥 `options["codec_whitelist"] == X` 를 둔다. `codec_whitelist=None` 이면
  어느 쪽에도 두지 않는다. 영어 표식·칸 이름으로만 읽는다. verify RED: 표식 없음, `AUDIO_DECODERS` 없음, 키워드 없음. closes US1-6, supports FR-004

### Implementation for User Story 1 (turn T006–T007 GREEN)

- [X] T008 [US1] `src/stt/audio_decode.py`: `open_container(source, *, format_whitelist=None, codec_whitelist=None)` — `None` 이 아니면
  `container_options` 에 `codec_whitelist` 를 더하고, 열린 컨테이너의 흐름마다 코덱 문맥이 있으면 그 `options` 에 같은 값을 둔다(contracts/reader.md §2).
  `None` 이면 지금과 똑같다. `metadata_errors`(PyAV 19 미만) 처리와 `format_whitelist` 는 그대로. docstring(Google, 영어)에 두 겹의 이유. PyAV 17·18·19
  모두에서 같은 동작인지는 CI(3.10)·로컬 3.11 이 본다(T031). RED → GREEN: turns T007(b) GREEN. supports FR-001, FR-005
- [X] T009 [US1] `src/stt/server/reader.py`: `AUDIO_DECODERS`(contracts/reader.md §1 의 32개, `AUDIO_FORMATS` 옆, 코드에서 이 한 곳만), 자식의
  `_open_container` 가 `format_whitelist=AUDIO_FORMATS, codec_whitelist=AUDIO_DECODERS` 로 연다. probe 는 길이를 재기 전에 순수 함수
  `first_audio_decoder_allowed(container) -> bool`(음성 흐름 없음·`codec_context is None`·이름이 목록 밖 → 거짓 → 끝 코드 1)을 부른다. 감독·받기·워커는 바꾸지 않는다(받기·워커는 PyAV 를
  불러오지 않는다 — 002 T019(e) 시험이 그대로 지킨다). RED → GREEN: turns T006, T007(a 제외) GREEN. **돌연변이 확인**(T006(a)(g)의 RED 대신):
  `AUDIO_DECODERS` 에서 녹음기 코덱 10개를 빼면 T006(g)가, `aac` 를 빼면 T006(a)가, `stt.audio_decode.decode_samples` 가 `open_container` 에
  `codec_whitelist=AUDIO_DECODERS` 를 넘기게 하면 T006(h)가 실패함을 한 번씩 보고 되돌린다(quickstart B-003-1(1)의 「22개
  일 때 `failed`」를 pytest 로 본다 — 결과를 커밋 메시지에). closes US1-1, US1-2, US1-3, US1-4, US1-5, 경계 사례 1·2, supports FR-001~003, SC-002
- [X] T010 [US1] `docs/server-security.md` §2 에 「해독기 목록」 절(contracts/operator-log.md §5.1): 문장(서버 경로는 이 음성 해독기만 연다, 영상·이미지·
  자막 해독기는 분석에서도 해독에서도 거절, 영상 트랙이 든 녹음은 음성만, 목록 밖 음성 코덱은 받을 때 `unreadable_audio`), `<!-- stt:audio-decoders -->`
  표(칸 `decoder`·`codec`·`formats`, 32줄), 남는 범위 한 단락(분석기·형식은 목록이 막지 않음, 해독 단계의 영상 묶음 버리기는 운영자가 미룸
  2026-11-10, 그런 공지는 지금처럼 판정). §3 「가장 흔한 줄」의 원인 목록에 「목록 밖 음성 코덱」을 더한다. 목록에 이름을 더하거나 빼면 상수·표·
  형식별 시험을 함께 바꾼다는 규칙 한 줄. RED → GREEN: turns T007(a) GREEN. closes US1-6, supports FR-004, FR-016

**Checkpoint**: pytest 에서 US1 이 모두 GREEN. 영상·표지 그림 녹음이 음성만 읽힌다.

---

## Phase 4: User Story 3 - 서버 오류의 원인 종류가 운영자 기록에 보인다 (Priority: P2)

**Goal**: `<request_id> failed engine_error <cause>`(다섯 이름), 녹음 열기를 `RecordingError` 하나로, 녹음이 없거나 일반 파일이 아니거나 FIFO 여도
워커가 멈추거나 막히지 않는다. US2 보다 먼저 한다(plan).

**Independent Test**: 소켓 없음과 녹음 문제(없음·디렉터리·FIFO·권한)를 시험 대역으로 만들어 응답은 같은 `engine_error` 이고 줄 끝 원인이 다르며,
다음 작업이 처리된다.

### Tests for User Story 3 (write first) ⚠️

- [X] T011 [US3] `tests/server/test_hardening_us3.py`(새), 흉내 읽기 소켓·대역·가짜 엔진으로: (a) `test_hardening_us3_1_*` — 소켓 없음·`not-started`
  대역·「시작」 전 끊김: 응답 `engine_error`·「The server could not start the audio reader.」, 줄 `<id> failed engine_error reader_unavailable`.
  (b) `test_hardening_us3_2_*` — 녹음 없음·디렉터리·FIFO·권한 000(root 로 돌면 건너뜀, 이유를 적음): 같은 응답, 줄 `… recording_unusable`, 읽기 연결
  0(인스턴스를 띄우지 않음). FIFO 경우는 워커를 스레드(또는 하위 프로세스)로 돌려 `join(timeout=5)` 으로 기다리고, `finally` 에서 FIFO 의 쓰는 쪽을
  `os.open(fifo, O_WRONLY | O_NONBLOCK)` 으로 열었다 닫아 막힌 `open` 을 풀어 준다 — 지금 코드의 RED 가 매달리지 않고 「5초 안에 끝나지 않음」으로
  곧 실패한다. (c) `test_hardening_us3_3_*` — 일반 파일 아님 작업 뒤 같은 워커 실행에서 다음 작업이 `done`, 워커 끝 코드 0, 트레이스백 없음.
  (b)(c)의 모든 경우에 `assert_no_result(layout, id)` 를 단언한다(헌법 III — 실패 길마다 남은 파일 없음). (d) `test_hardening_us3_4_*`·`test_hardening_edge_6_*` — 읽기 단계에서 잡는 것은 `OSError`(`RecordingError` 포함)·`ValueError` 뿐이고 그 밖의
  예외(가로채 넣은 `RuntimeError`)는 002 처럼 워커 밖으로 올라간다. 모든 `engine_error` 줄이
  `^<uuid> failed engine_error (reader_unavailable|recording_unusable|unexpected|engine_failed|engine_no_verdict)$` 에 맞고 경로·파일 이름·errno·오류
  문구가 없다. `os.pipe2` 를 EMFILE 로 바꿔 넣으면 `unexpected`, 엔진의 `engine_error` 판정 → `engine_failed`, 판정 없음 → `engine_no_verdict`.
  (e) 받기에서 일반 파일이 아닌 녹음은 지금처럼 요청별 예외로 기록된다 — 줄이 `intake error RecordingError`(errno 없음) 또는 errno 이름(contracts/
  reader.md §5). verify RED: 줄 끝에 원인 없음, 녹음 없음에서 워커가 `StopIteration` 으로 멈춤, 디렉터리에서 `ValueError` 로 멈춤, FIFO 에서 시간 넘김.
  closes US3-1, US3-2, US3-3, US3-4, 경계 사례 6, SC-004(pytest), supports FR-008, FR-009
- [X] T012 [P] [US3] 기존 시험을 새 줄·예외 형으로 고친다(research R6, 파일마다 까닭을 커밋 메시지에): `tests/server/test_decoder_us3.py:321`·`:571`
  (줄 전체 비교 → 원인 이름 포함), `:548-571`(`_open_recording` 을 맨 `OSError` 로 바꿔 넣던 시험 → 녹음 쪽은 실제 없는 파일·권한으로, 그 밖은
  `os.pipe2` 의 EMFILE 로), `tests/server/test_reader_client.py:155-197`(열기 오류의 예외 형 `RecordingError`, errno 유지, 일반 파일 아님은 errno 없음).
  고쳐 쓴 시험마다 지금의 `assert_no_result` 단언을 그대로 둔다(헌법 III). `tests/server/test_logging.py:174` 는 부분 문자열 비교라 그대로 둔다. verify RED: 고친 단언이 지금 코드에서 실패한다(원인 없음, `RecordingError` 없음).
  supports US3-1, US3-2, FR-008
- [X] T013 [P] [US3] `tests/test_server_security_doc.py` 에 더한다: `<!-- stt:engine-error-causes -->` 표의 `cause` 칸 집합 ==
  `worker.ENGINE_ERROR_CAUSES`, 그리고 문서에 `failed engine_error reader_unavailable` 이 있다(옛 줄 `failed engine_error` 만 쓴 자리가 없음).
  verify RED: 표식·상수 없음. supports US3-4, FR-016

### Implementation for User Story 3 (turn T011–T013 GREEN)

- [X] T014 [US3] `src/stt/server/reader_client.py`: `class RecordingError(OSError)`(영어 docstring), `_open_recording` 이 `O_RDONLY | O_NOFOLLOW |
  O_CLOEXEC | O_NONBLOCK` 로 열고, 열기 `OSError` → 같은 errno 의 `RecordingError`(`from error`), 일반 파일 아님 → 기술자를 닫고 errno 없는
  `RecordingError("the recording is not a regular file")`. `probe()`·`decode()` 의 `Raises:` 를 contracts/reader.md §5 대로. 다섯 결과는 그대로.
  받기 쪽 기록이 T011(e)대로인지 보고, 다르면 `src/stt/server/intake.py` 의 기록 함수만 고친다. RED → GREEN: turns T012 의 `test_reader_client.py`
  부분, T011(b)의 FIFO·(e) GREEN. supports US3-2, FR-009
- [X] T015 [US3] `src/stt/server/worker.py`: `ENGINE_ERROR_CAUSES`(frozenset 다섯), `_prepare` 의 녹음 경로를 `glob` 대신
  `audio/<audio_id>/audio.<original_name.rsplit(".", 1)[1]>` 로(받기 `_copy_attachment` 와 같은 규칙), 읽기 단계의 예외 나누기(`RecordingError` →
  `recording_unusable`, 그 밖의 `OSError`·`ValueError` → `unexpected`, `unavailable` → `reader_unavailable`), GPU 단계의 두 길(`engine_failed`·
  `engine_no_verdict`), `_settle` 이 `engine_error` 일 때만 줄 끝에 원인 낱말을 붙인다. 응답의 `Failure` 는 바꾸지 않는다. 읽기 단계에서 잡는 것은 `OSError`
  (`RecordingError` 포함)와 `ValueError` 뿐이다 — 이 둘은 워커를 멈추지 않는다. 그 밖의 예외는 잡지 않고 002 처럼 올라간다(`except Exception`
  없음, 헌법 V, data-model §3). RED → GREEN: turns T011(a)~(d), T012 의 `test_decoder_us3.py` 부분 GREEN. closes US3-1, US3-2, US3-3, US3-4, 경계 사례 6, supports FR-008, FR-009
- [X] T016 [US3] `docs/server-security.md` §3 「작업 하나의 실패」에 `engine_error` 줄의 모양과 `<!-- stt:engine-error-causes -->` 표(칸 `cause`·`stage`·
  뜻·볼 것, contracts/operator-log.md §5.2), 「읽기 단위를 쓸 수 없을 때」 항목의 줄을 `… failed engine_error reader_unavailable` 로 고친다.
  `recording_unusable` 의 볼 것은 녹음을 열지 않고 폴더·소유·권한만 본다는 규칙을 적는다. RED → GREEN: turns T013 GREEN. closes US3-4(문서),
  supports FR-016

**Checkpoint**: 워커의 읽기 준비가 어떤 녹음 상태에서도 멈추지 않고, `engine_error` 줄마다 원인 이름이 있다.

---

## Phase 5: User Story 2 - 끝이 잘린 녹음을 운영자가 알아본다 (Priority: P2)

**Goal**: 읽기가 `ok` 이고 `audio.wav` 를 쓴 뒤 `job.duration_s − 해독된 바이트 ÷ 2 ÷ 16 000 ≥ 1.0` 이면 워커 표준 출력에 고정 줄
`decoded audio is shorter than the probed length` 하나. 결과는 그대로.

**Independent Test**: 끝을 자른 사인파와 잘리지 않은 녹음을 맡겨 앞의 것만 그 시각에 줄이 있고 둘 다 `done`.

### Tests for User Story 2 (write first) ⚠️

- [X] T017 [US2] `tests/server/test_hardening_us2.py`(새), 흉내 읽기 소켓 + 진짜 감독 + 가짜 엔진으로: (a) `test_hardening_us2_1_*` — 사인파 10초
  MP3·FLAC·색인이 앞인 M4A 를 반으로 자른 셋: 받기 → 워커 → `done`, 표준 출력에 `<id> running`, 잘림 줄, `<id> done` 이 이 순서로 한 번씩(SC-003 의
  「3개 이상」). (b) `test_hardening_us2_2_*` — 형식 8가지 정상 녹음은 잘림 줄 0개. (c) `test_hardening_us2_3_*` — 비교 순수 함수 `worker.is_truncated(probed_s, decoded_bytes)`(contracts/operator-log.md §1): 차이 정확히 1.0 →
  참, 0.999… → 거짓, 해독이 더 김 → 거짓, 0.046(WMA 끝)·0.0065(Opus)·0.00775(002 의 끝 차이)·−0.056(기준 1시간) → 거짓. (d) `test_hardening_us2_4_*` —
  색인이 앞인 M4A 를 `mdat` 머리 뒤에서 자른 것: `done`(빈 전사록), 줄 하나. (e) `test_hardening_us2_5_*` — 잘린 녹음의 1차와 재전사 둘: 작업마다 줄
  하나. (f) FR-006 — 줄에 요청 ID·경로·파일 이름·숫자가 없다(줄 == `worker.TRUNCATION_LINE`). (g) `test_hardening_edge_5_*`(spec 경계 사례 5) — 묶음 길이 합의
  길(`container.duration is None`, `src/stt/server/reader.py:334-339`): `tests/server/test_reader.py:849` 의 `NoDuration` 감싸개로 온전한 사인파와
  반을 자른 사인파를 `reader.container_duration_s` 에 넘기면 처음 잰 길이가 각각 해독된 길이와 1초 안으로 같아 `worker.is_truncated` 가 둘 다 거짓.
  그리고 길이를 머리에 적지 않는 WAV·Ogg 를 반으로 자른 것을 받기 → 워커로: 처음 잰 길이도 같이 줄어 줄이 없고 `done`. (h) `test_hardening_edge_4_*` — 진짜
  감독과 002 T055 의 「일찍 멈춤」 대역(끝 코드 3, 그 앞까지의 표본)으로, 처음 잰 길이가 해독된 길이보다 1초 이상 길면 `done` 이고 잘림 줄이 남는다.
  (i) 읽기가 `failed`·`stopped`·`timeout` 이면 잘림 줄이 없다. verify RED: `worker.TRUNCATION_LINE`·`worker.is_truncated` 없음(ImportError), 잘린 녹음에서 줄 0개.
  closes US2-1, US2-2, US2-3, US2-4, US2-5, 경계 사례 4·5, SC-003(pytest), supports FR-006, FR-007
- [X] T018 [P] [US2] `tests/test_server_security_doc.py` 에 더한다: `<!-- stt:truncation threshold_s=… -->` 의 값 == `worker.TRUNCATION_THRESHOLD_S`,
  문서에 `worker.TRUNCATION_LINE` 이 한 줄 그대로 있다. verify RED: 표식·상수 없음. supports FR-016

### Implementation for User Story 2 (turn T017–T018 GREEN)

- [X] T019 [US2] `src/stt/server/worker.py`: `TRUNCATION_THRESHOLD_S = 1.0`, `TRUNCATION_LINE`, 비교 순수 함수 `is_truncated(probed_s: float, decoded_bytes: int) -> bool`,
  읽기 단계가 `ok` 이고 `audio.wav` 를 쓴 직후·GPU 단계 전에 참이면 표준 출력에 그 줄 하나(작업 ID 없음). 작업 상태·응답·결과·`spec.json` 은 그대로.
  같은 커밋에서 `tests/server/test_decoder_us3.py:514`(`test_decoder_edge_10_a_recording_damaged_from_its_first_frame_…` — 2초로 잰 녹음이 0바이트로
  해독됨)의 표준 출력 기대를 `[running, TRUNCATION_LINE, done]` 으로 고친다(경계 사례 4, research R6). 이 시험은 T019 를 넣으면 RED 가 되고 기대를
  고쳐 GREEN 이 된다. `:492` 시험은 그대로다.
  RED → GREEN: turns T017 GREEN. closes US2-1, US2-2, US2-3, US2-4, US2-5, 경계 사례 4·5, supports FR-006, FR-007
- [X] T020 [US2] `docs/server-security.md` §3 「끝이 잘린 녹음」(contracts/operator-log.md §5.2): 표식, 줄, 확인 명령, 바로 앞 `<request_id> running`
  이 그 작업이라는 것, 알아보는 형식·알아보지 못하는 형식(WAV·Ogg·ADTS, 색인이 뒤인 M4A 는 받을 때부터 `unreadable_audio`), 잘리지 않았는데 줄이 남는
  경우(경계 사례 3 — 머리 없는 가변 비트율 MP3, ADTS AAC, 영상이 더 긴 녹음; 줄은 「더 짧다」는 사실만 말한다), 일찍 멈춘 해독과 함께 남는 경우
  (경계 사례 4). RED → GREEN: turns T018 GREEN. closes 경계 사례 3(문서 — 줄의 뜻을 운영자가 읽는 자리), supports FR-016

**Checkpoint**: 잘린 녹음이 `done` 이면서 운영자 기록에 한 줄을 남긴다.

---

## Phase 6: User Story 4 - 작은 손질로 설계 문서의 주장을 참으로 만든다 (Priority: P3)

**Goal**: 감독의 SIGINT 무시, systemd 257 단언, `audio.wav` 를 만든 기술자로 쓰기, 손 실행 안내 한 줄, 두 자식에 `-I`.

**Independent Test**: 손질마다 자동 시험 하나(SC-005).

### Tests for User Story 4 (write first) ⚠️

- [ ] T021 [US4] `tests/server/test_hardening_us4.py`(새): (a) `test_hardening_us4_1_*` — 하위 프로세스 감독(`tests/server/reader_harness.py`) + T002
  의 `sigint-parent`: 판정 `ok`, 감독 끝 코드 0, 표준 오류에 트레이스백·`KeyboardInterrupt` 없음. 처분은 하위 프로세스에서만 바뀐다(pytest 자신의
  SIGINT 처분이 시험 전후 같음을 단언). (b) `test_hardening_us4_3_*` — `worker._write_audio` 가 파일을 만든 직후 경로를 지우는 가로채기(예:
  `os.fdopen`·`wave.open` 을 감싸 첫 호출 때 `os.unlink`)에서, 쓰기가 끝난 뒤 그 경로에 파일이 다시 생기지 않는다. 정상이면 0600·WAV 머리(1채널·
  2바이트·16 kHz) 그대로. 단위와 같은 `umask 0o027` 로 돈다. (c) `test_hardening_us4_5_*` — 임시 폴더에 `json.py`(가져오면 끝 코드 99 로 끝남)를
  심고 그 폴더를 현재 폴더로 `reader.DEFAULT_CHILD_COMMAND`·`worker.DEFAULT_ENGINE_CMD` 를 띄우면 심은 모듈이 읽히지 않는다(끝 코드 ≠ 99). 콘솔
  스크립트 `stt` 도 같은 폴더에서 읽지 않는다(이미 참 — 회귀 지킴). verify RED: (a) 판정 없음·`KeyboardInterrupt` 트레이스백, (b) 경로에 0640 파일이
  다시 생김, (c) 끝 코드 99. closes US4-1, US4-3, US4-5, SC-005(pytest 몫), supports FR-010, FR-012, FR-014
- [ ] T022 [P] [US4] `tests/server/test_server_cli.py` 의 `test_the_read_command_ends_with_code_2_and_no_output_when_stdin_is_not_a_connected_socket`
  (`:511-516`)를 `test_hardening_us4_4_*` 의 뜻으로 고친다: 끝 코드 2, 표준 출력 없음, 표준 오류 == `reader.MANUAL_RUN_LINE + "\n"`. 그리고
  `tests/test_server_security_doc.py` 에 `reader.MANUAL_RUN_LINE` 이 문서에 그대로 있다는 단언. verify RED: 표준 오류가 비어 있음, 상수 없음.
  closes US4-4(pytest), supports FR-013
- [ ] T023 [P] [US4] `nix/module-eval.nix`(새)와 `flake.nix` 의 `checks.x86_64-linux.module-eval`(contracts/module.md §2): `self.nixosModules.default`
  + `services.stt.enable = true; boot.isContainer = true;` 를 systemd `256.10`(`overrideAttrs` + `__intentionallyOverridingVersion = true`)·`257`·고정된
  nixpkgs 의 판으로 평가해 실패한 단언 문장을 모으고, 평가 시점 `assert` 셋(256.10 은 `services.stt needs systemd 257 or later` 로 시작하고 `256.10` 을
  담는 문장이 정확히 하나, 나머지 둘은 없음)이 통과하면 빈 결과물. `captest 8G 15m nix build .#checks.x86_64-linux.module-eval -L`. verify RED: 단언이
  없어 첫 `assert` 가 실패한다. **`[VERIFY]` B-003-6 의 로컬 몫**: `captest 10G 30m nix flake check --no-build -L` 의 시간과 찍힌 최대 메모리(평가 메모리)를
  커밋 메시지에 적는다. push 는 운영자 승인 뒤라 CI 몫은 T033 에서 본다. closes US4-2(시험), supports FR-011, SC-005

### Implementation for User Story 4 (turn T021–T023 GREEN)

- [ ] T024 [US4] `src/stt/server/reader.py`: `serve()` 맨 앞 `signal.signal(signal.SIGINT, signal.SIG_IGN)`(`_supervise` 는 바꾸지 않음),
  `MANUAL_RUN_LINE` 상수와 표준 입력이 연결된 `AF_UNIX` `SOCK_SEQPACKET` 소켓이 아닐 때 표준 오류에 그 한 줄을 쓰고 2(표준 출력 없음),
  `DEFAULT_CHILD_COMMAND` 에 `-I`. 감독의 다른 줄은 그대로. RED → GREEN: turns T021(a), T021(c)의 자식 부분, T022 의 cli 부분 GREEN.
  closes US4-1, US4-4, US4-5(자식), supports FR-010, FR-013, FR-014
- [ ] T025 [US4] `src/stt/server/worker.py`: `_write_audio` 가 `os.open(…, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0o600)` 의 기술자를
  `with os.fdopen(fd, "wb")` 로 감싸 `wave.open(<파일 객체>, "wb")` 에 넘긴다(경로로 다시 열지 않음, `wave.open` 은 모듈로 불러 저장 오류 시험
  `test_decoder_us3.py:452` 의 바꿔치기가 그대로 닿음). `DEFAULT_ENGINE_CMD` 에 `-I`. RED → GREEN: turns T021(b), T021(c)의 엔진 부분 GREEN, 기존 저장
  오류 시험 그대로 GREEN. closes US4-3, US4-5(엔진), supports FR-012, FR-014
- [ ] T026 [US4] `nix/module.nix` 의 `config = lib.mkIf cfg.enable { … }` 에 `assertions`(contracts/module.md §1 의 문장 그대로, 영어). 다른 설정은
  건드리지 않는다. RED → GREEN: turns T023 GREEN. closes US4-2, supports FR-011
- [ ] T027 [US4] `docs/server-security.md` §2 감독의 줄 문단에 손 실행 줄(`MANUAL_RUN_LINE` 그대로)과 「해독 자식과 엔진은 `python -I` 로 떠서 현재
  폴더를 모듈 찾는 길에 넣지 않는다」 한 줄, §3 감독 줄 목록에 손 실행 줄(운영 중에는 나오지 않음). RED → GREEN: turns T022 의 문서 단언 GREEN.
  supports US4-4, US4-5, FR-016

**Checkpoint**: US4 의 다섯 손질마다 자동 시험이 있고 통과한다.

---

## Phase 7: Polish & 최종 확인

**Purpose**: VM GREEN, 남은 문서, 전체 회귀, 최종 감사. 감사는 기능 브랜치를 마치기 전 한 번(헌법 「최종 검증」).

- [ ] T028 VM GREEN: `captest 8G 60m nix build .#checks.x86_64-linux.server-vm -L` 가 통과한다 — B-003-1~4 와 **B-003-5**(기존 B-P1·B-P2 부분 시험이
  감시 라이브러리가 실린 채로 모두 통과, B-P2-1 의 설정 단언 그대로). **해결 `[VERIFY]` B-003-2**: 빌드 안에서
  `head -c` 로 자른 넷이 이 세션의 측정처럼 읽혀(처음 잰 길이 10초 안팎, 해독 반쯤) 잘림 줄이 정확히 4 다. 어긋나면 원인을 research R12 에 적고
  파일 만드는 법을 고친다(기준값을 넓히지 않는다). 찍힌 최대 메모리를 커밋 메시지에. RED 해당 없음 — RED 는 T005 에서 봤다(이 과제는 같은 단언의
  GREEN 확인). closes US1-1~5(VM), US2-1·2·4(VM), US3-1·2·3(VM), US4-4(VM), SC-001(VM), SC-002(VM), SC-003(VM), SC-004(VM), `[VERIFY]` B-003-2,
  supports SC-006
- [ ] T029 [P] `docs/server-security.md` §4(contracts/operator-log.md §5.3): `:238` 「후속 후보: 코덱 허용목록 … 넣지 않았다」 줄을 「코덱 허용목록:
  003(배포 커밋·날짜)부터 서버 경로는 §2 『해독기 목록』의 음성 해독기만 연다 …」로 바꾸고(커밋·날짜 자리는 `pending` 으로 두었다가 T033 에서 채운다),
  4.2 정책에 「영상·이미지·자막 해독기만의 공지는 4.3 의 `reason` 에 『서버 경로에서 열리지 않음(§2)』을 적을 수 있다. 분석기·형식·목록 안 음성
  해독기의 공지는 지금처럼」 한 항목. `stt:policy` 표식·4.3 의 기존 다섯 줄은 바꾸지 않는다. WMA Pro·Voice·Lossless·AMR-WB 는 시험 파일 없이 목록에
  있다는 사실과 첫 실제 녹음 때의 확인 방법(응답과 `reader child` 줄)을 §2 해독기 목록 아래 한 줄로 적는다. RED 해당 없음 — 문서(기존
  `stt:policy` 대조 시험이 그대로 GREEN 인지 T031 에서 본다). supports FR-016, US1-6
- [ ] T030 [P] `specs/002-decoder-isolation/contracts/reader.md` 의 §4(상수 `AUDIO_DECODERS`)·§5(SIGINT 무시, 손 실행 줄)·§6(두 목록, 길이 재기의
  이름 확인, `-I`)·§7(`RecordingError`·`O_NONBLOCK`)·§8(끝 코드 2 의 줄)에 이 기능의 `specs/003-reader-hardening/contracts/` 를 가리키는 한 줄씩
  (research R14). 스키마·001 계약 문서는 건드리지 않는다. RED 해당 없음 — 내부 계약 문서. supports FR-015
- [ ] T031 전체 회귀(A 층): `captest 4G 20m uv run --locked python -m pytest -q`(3.12, av 19.0.2)와, push 전이라 CI 대신 로컬에서 uv 가 받은
  CPython 으로 3.10(av 17.1.0)·3.11(av 18.1.0) — `UV_PYTHON_PREFERENCE=managed UV_PYTHON_INSTALL_DIR=<scratch>/pythons
  UV_PROJECT_ENVIRONMENT=<scratch>/venv310 captest 4G 30m uv run --locked --python 3.10 --extra server --extra diarize python -m pytest -q -p no:cacheprovider`,
  3.11 도 같은 꼴(`venv311`), 끝나면 두 환경을 지운다(`<scratch>` 는 세션 scratchpad). AMR-NB 시험이 3.10·3.11 에서 도는지 확인 — 모두 통과, `captest 8G 15m nix build .#checks.x86_64-linux.module-eval -L` GREEN, `git diff --stat master -- src/stt/server/schemas`
  가 비어 있음. 이 기능에서 만든 시험 녹음이 모두 사인파·무음·단색·잘린 것·텍스트 파일뿐인지 `tests/server/conftest.py`·`nix/vm-test.nix` 를 훑어
  확인한다(FR-017). 코드·시험·로그에 한글이 없음을 `grep -nP '[\x{AC00}-\x{D7A3}]' src tests nix` 로 본다(기존 `transcribe.py` 의 한국어 출력 문장은
  예외 — 002 plan Complexity Tracking). 찍힌 최대값을 커밋 메시지에. RED 해당 없음 — 확인. closes SC-005, SC-006(로컬 몫 — 3.10·3.11·3.12), supports FR-015, FR-017
- [ ] T032 T-Verify-Final: `project-audit` 스킬로 최종 감사 — 범위 qa-engineer(7-Layer 모듈·통합·E2E) + adversary(20+ 페르소나 + silent-skip).
  adversary 는 코드와 설정만 읽고, 입력 파일을 만들거나 돌리지 않는다(FR-017, 001 T078 의 교훈). 특히 볼 곳: 두 겹 목록이 모든 열기 길에 걸리는지
  (`find_stream_info`·해독·재전사), 받기·워커가 PyAV 를 불러오지 않는지, 원인 이름 집합이 닫혀 있는지, 새 줄에 작업 ID·경로·숫자가 새지 않는지,
  VM 감시 라이브러리가 운영 모듈에 새지 않는지. BOUNDARY_MISMATCH / INTEGRATION_MISSING / SILENT_SKIP_NEW / PERSONA_ATTACK_SUCCESS 가 나오면 고칠
  때까지 T033 으로 가지 않는다(지적마다 RED → GREEN 과제를 이 파일 끝에 더하고, 고친 뒤 바뀐 곳만 다시 감사). 결과를
  `specs/003-reader-hardening/audit/` 에 남긴다. RED 해당 없음 — 감사. supports SC-001~006

---

## Phase 8: 배포·호스트 확인·회신 (운영자 승인 뒤)

- [ ] T033 배포: T032 가 통과한 뒤 운영자 승인을 받아 `003-reader-hardening` 을 master 에 합치고 push 한다. PR 이나 그 전 push 는 하지 않는다(push 는 운영자 승인 뒤만 — 3.10·3.11 은 T031 이 로컬에서 봤다). push 뒤 CI(Python 3.10·3.12 pytest,
  `nix flake check --no-build`)가 GREEN 인지 보고 **`[VERIFY]` B-003-6 의 CI 몫**(그 평가의 시간·메모리, 실패하면 원인)을 적는다. 운영자가 nixos-config 에서
  `nix flake update stt --option tarball-ttl 0` 과 `nxr` 을 한다(stt 서비스가 잠깐 다시 뜨므로 운영자가 시각을 맞춘다). 스위치가 systemd 판 단언을
  통과한다(호스트 262 — US4-2 의 호스트 몫). 배포한 커밋과 날을 `specs/003-reader-hardening/handoff/reply.md` 초안과 `docs/server-security.md` §4 의
  `pending` 자리(T029)에 채우는 문서 커밋을 만들고, 그 push 도 운영자 승인 뒤에 한다. RED 해당 없음 — 배포. closes `[VERIFY]` B-003-6, US4-2(호스트),
  supports US1~US4
- [ ] T034 호스트 확인(운영자와, quickstart C): **C-003-1** `systemctl show -p MainPID stt-worker.service` 가 스위치 전과 다르거나 0, 읽기 단위 설정이
  002 와 같음(002 contracts/module.md §4 의 명령). **C-003-2** `clip60.m4a`·`hour.m4a` 를 1차·2차로 전사해 `transcript.txt` 와 `result.json` 의
  `paragraphs` 를 2026-11-10 배포 뒤 보관본과 비교(같아야 함 — 다르면 운영자가 받아들인 뒤에만 완료), 그 시각의 `journalctl -u stt-worker -g 'shorter
  than the probed length'` 와 `journalctl -k -g 'type=1326'` 이 빔. **C-003-3** 운영자가 호스트 `ffmpeg` 로 만든 사인파 + 단색 영상 `m4a` 와 사인파
  MP3 + 단색 PNG 표지를 보안 문서 §3 의 「안전한 재확인」 스니펫으로 배포된 읽기 소켓을 거쳐 읽어 `ok … ok`·맞는 길이, 같은 시각 `reader child` 줄과
  `type=1326` 없음((선택) 1차로 맡겨 `done`). **C-003-4**(선택) 사인파 10초 MP3 를 반으로 잘라 1차로 → `done` 과 잘림 줄 하나. **C-003-5** 셸에서
  `stt server read` → 안내 한 줄, `echo $?` 가 2. **운영 확인 `[VERIFY]` R2**(WMA Pro·Voice·Lossless·AMR-WB): 시험 파일 없이, 배포된 해석기로
  `python -I -c` 를 돌려 `wmapro`·`wmavoice`·`wmalossless`·`amrwb` 가 모두 `av.codec.Codec(name, "r")` 로 열리고 `type == "audio"` 임을 본다(해독기
  이름이 배포된 FFmpeg 에 있다). 끝까지 읽는 확인은 그런 녹음이 처음 들어올 때 응답과 `reader child` 줄로 한다 — 이 남은 확인을 회신과 `docs/server-security.md` §4
  매달 확인 기록의 다음 확인 항목에 적고, 그 표를 고친 뒤 `captest 4G 10m uv run --locked python -m pytest -q tests/test_server_security_doc.py` 가
  GREEN 인지 본다. 결과를 `handoff/reply.md` 에. RED 해당 없음 — 호스트 확인. closes SC-001(호스트), SC-002, SC-003(호스트),
  US4-4(호스트), supports US1-1~3, US2-2, `[VERIFY]` R2(이름 몫)
- [ ] T035 회신 `specs/003-reader-hardening/handoff/reply.md`(C-003-6, 운영자 결정 2026-11-10): (1) 계약(스키마·실패 이유·고정 문장·지우기·재전사·
  23:58)은 바뀌지 않았다. (2) **거절되는 코덱 한 단락**: 서버가 여는 해독기는 이제 음성 해독기 목록(보안 문서 §2, 32개)뿐이다. 받는 녹음 형식은
  그대로이고 영상 트랙이 든 녹음은 음성만 읽는다. 첫 음성 트랙이 Speex, GSM, 그 밖의 목록 밖 음성 코덱(예: MPEG Layer I, TrueSpeech, IMA·Microsoft
  밖의 ADPCM — G.726·G.722·Yamaha·QuickTime IMA4)이면 받을 때 바로 — MP3 라고 적힌 상자(예: `.mp3` 안 MPEG Layer I)는 길이 재기를 지나므로 처리 때 —
  `unreadable_audio`(「The recording could not be read as audio.」)로 거절된다.
  002 까지는 이런 녹음도 전사됐다. 배포한 날 이미 받아 둔 그런 녹음의 재전사는 해독에서 `unreadable_audio` 가 된다(그날 23:58 까지). (3) 배포 커밋·
  날짜, C-003-2·C-003-3 의 결과, WMA Pro·Voice·Lossless·AMR-WB 의 남은 운영 확인(T034). 운영자가 bhu-llm 대장으로 옮긴다. RED 해당 없음 — 회신 문서.
  supports FR-003, SC-001, SC-002, 헌법 VI

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1(T001·T002) → Phase 2(T003 → T004 → T005) → Phase 3(US1) → Phase 4(US3) → Phase 5(US2) → Phase 6(US4) → Phase 7 → Phase 8.
- Phase 3~6 의 **시험 과제**는 Phase 1 뒤 언제든 쓸 수 있지만, **구현 과제**는 T005(VM RED 기록) 뒤에만 한다 — 그래야 B-003-1·3·4 의 RED 가 남는다.
- Phase 4(US3)를 Phase 5(US2)보다 먼저: 둘 다 `worker.py` 의 읽기 단계를 고치고, 잘림 줄은 US3 이 정한 예외 구조 뒤에 놓인다(plan).
- Phase 6 의 T023·T026(Nix)은 Python 과제와 독립이라 Phase 3 이후 언제든 할 수 있다.
- T028(VM GREEN)은 T009·T015·T019·T024·T025 가 끝난 뒤. T032 는 T028~T031 뒤. Phase 8 은 T032 통과와 운영자 승인 뒤.

### Within Each Phase

- 시험 과제(RED 확인) → 구현 과제(GREEN) → 다음.
- 같은 파일의 과제는 차례로: `src/stt/server/worker.py`(T015 → T019 → T025), `src/stt/server/reader.py`(T009 → T024),
  `docs/server-security.md`(T010 → T016 → T020 → T027 → T029 → T033 → T034), `tests/test_server_security_doc.py`(T007(a) → T013 → T018 → T022), `tests/server/test_audio_decode.py`(T007(b)),
  `nix/vm-test.nix`(T003 → T004), `tests/server/conftest.py`(T001), `flake.nix`(T023).
- T008(`audio_decode.py`) → T009(`reader.py` 가 새 키워드를 넘김).

### User Story Dependencies

- US1: Phase 2 뒤. 다른 이야기에 기대지 않는다.
- US3: Phase 2 뒤. US1 과 코드가 겹치지 않는다(`reader_client.py`·`worker.py`).
- US2: US3 뒤(같은 `worker.py` 읽기 단계).
- US4: (a)(d)는 `reader.py` 라 US1 뒤, (c)(e)는 `worker.py` 라 US2 뒤, (b)는 독립.

## Parallel Opportunities

- Phase 1: T001·T002 함께.
- Phase 3: T006 과 T007 함께(다른 파일).
- Phase 4: T011·T012·T013 함께. 구현은 T014(`reader_client.py`) 와 T016(문서) 함께, T015 는 T014 뒤.
- Phase 5: T017·T018 함께.
- Phase 6: T021·T022·T023 함께. T026(`module.nix`)은 T024·T025 와 함께.
- Phase 7: T029·T030 함께.

### Parallel Example: Phase 6

```text
# 시험을 함께 쓴다(모두 RED 확인):
T021 tests/server/test_hardening_us4.py
T022 tests/server/test_server_cli.py + tests/test_server_security_doc.py
T023 nix/module-eval.nix + flake.nix

# 서로 다른 파일의 구현을 함께:
T024 src/stt/server/reader.py   T025 src/stt/server/worker.py   T026 nix/module.nix
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 → Phase 2(VM RED 기록) → Phase 3(US1).
2. **멈추고 확인**: pytest 에서 영상·표지 그림 녹음이 음성만 읽히고 비음성 해독기가 열리지 않는다. 배포는 한 번이므로 MVP 단독 배포는 하지 않는다
   (plan 「배포는 한 번이다」).

### Incremental Delivery

1. US1 → US3 → US2 → US4 를 차례로 GREEN 으로 만들고 이야기마다 커밋한다.
2. VM GREEN(T028)·전체 회귀(T031)·최종 감사(T032) 뒤 한 번 배포(T033), 호스트 확인(T034), 회신(T035).

## Notes

- **시나리오 ↔ 과제(coverage check)**:

  | 시나리오·기준 | 과제 |
  |---|---|
  | US1-1 | T006(a)·T009·T028(B-003-1(1), B-P2-8) |
  | US1-2 | T006(b)·T009·T028(B-003-1(2))·T034(C-003-3) |
  | US1-3 | T006(c)·T009·T028(B-003-1(2))·T034(C-003-3) |
  | US1-4 | T006(d)·T009·T028(B-003-1(3)) |
  | US1-5 | T006(e)·T009·T028(B-003-1(3)) |
  | US1-6 | T007(a)·T010 |
  | US2-1 | T017(a)·T019·T028(B-003-2) |
  | US2-2 | T017(b)·T019·T028(B-003-2 `tone10.mp3`)·T034(C-003-2) |
  | US2-3 | T017(c)·T019 |
  | US2-4 | T017(d)·T019·T028(B-003-2 `nopackets-faststart.m4a`) |
  | US2-5 | T017(e)·T019 |
  | US3-1 | T011(a)·T015·T028(B-003-3(1)) |
  | US3-2 | T011(b)·T012·T014·T015·T028(B-003-3(2)) |
  | US3-3 | T011(c)·T015·T028(B-003-3(2) 뒤의 정상 작업) |
  | US3-4 | T011(d)·T013·T015·T016 |
  | US4-1 | T002·T021(a)·T024 |
  | US4-2 | T023·T026·T033(호스트 스위치) |
  | US4-3 | T021(b)·T025 |
  | US4-4 | T022·T024·T027·T028(B-003-4)·T034(C-003-5) |
  | US4-5 | T021(c)·T024·T025 |
  | 경계 사례 1 | T006(b)·T009 |
  | 경계 사례 2 | T006(f)·T009 |
  | 경계 사례 3 | T020(문서) — 잘리지 않았는데 줄이 남을 수 있음은 동작이 아니라 줄의 뜻이라 문서로 닫는다 |
  | 경계 사례 4 | T017(h)·T019(+ 002 시험 `test_decoder_us3.py:514` 갱신)·T020 |
  | 경계 사례 5 | T017(g)(묶음 길이 합의 길 + WAV·Ogg)·T019 |
  | 경계 사례 6 | T011(d)·T015 |
  | SC-001 | T006·T028·T034 |
  | SC-002 | T006(a)·T009·T028·T034(C-003-2) |
  | SC-003 | T017·T019·T028·T034 |
  | SC-004 | T011·T015·T028 |
| 헌법 III (실패 길의 남은 파일) | T011(b)(c)·T012 |
  | SC-005 | T021·T022·T023(+T024~T026)·T031 |
  | SC-006 | T028(B-003-5)·T031(3.10·3.11·3.12 로컬)·T033(CI 3.10·3.12) |

- **FR ↔ 과제**: FR-001~003 → T006·T008·T009 · FR-004 → T007·T010 · FR-005 → T006(h)·T008 · FR-006·007 → T017·T019 · FR-008·009 → T011·T014·T015 ·
  FR-010 → T021(a)·T024 · FR-011 → T023·T026 · FR-012 → T021(b)·T025 · FR-013 → T022·T024 · FR-014 → T021(c)·T024·T025 · FR-015 → T030·T031 ·
  FR-016 → T010·T016·T020·T027·T029 · FR-017 → T001·T003·T031·T032.
- **남은 `[VERIFY]` 의 자리**: B-003-1 → T005(RED 로 확인) · B-003-2 → T028 · B-003-6 → T023(로컬)·T033(CI) · R2 의 네 해독기(WMA Pro·Voice·Lossless·
  AMR-WB) → T034(이름 몫, 운영)·T029·T035(첫 실제 녹음 때의 확인 방법).
- **RED 를 지금 코드로 볼 수 없는 시험**: 형식 8가지·더한 녹음기 코덱·명령줄 그대로(T006(a)(g)(h))는 목록이 없는 지금 코드에서 이미 참이다. RED 는
  T009 의 돌연변이 확인 셋(이름을 빼면 (a)(g), 명령줄에 목록을 넘기면 (h)가 실패)으로 본다. quickstart B-003-1(1)의 「22개일 때 `failed`」도 같은 확인이다.
- spec 이 바뀌면 spec 을 먼저 고치고 과제를 고친다(전역 §3.5).
