# Data Model: 녹음 읽기 추가 좁히기와 알아차리기 (Phase 1)

- 작성: 2026-11-10 · 근거: [spec.md](spec.md) Key Entities, [research.md](research.md) R1~R11.
- 001·002 의 기록(Job·Audio)·상태·응답·결과·읽기 프로토콜 메시지는 바뀌지 않는다(`specs/001-server-transcription/data-model.md`,
  `specs/002-decoder-isolation/data-model.md`). 새 저장 자료는 없다. 이 문서는 새 상수·판정 규칙과, 실패 지도에서 바뀌는 칸만 적는다.

## 1. 해독기 목록 (spec 「해독기 목록」)

| 속성 | 값 |
|---|---|
| 이름 | `AUDIO_DECODERS` (`src/stt/server/reader.py`, `AUDIO_FORMATS` 옆) |
| 모양 | FFmpeg 해독기 이름(`AVCodec.name`)의 쉼표 목록, 문자열 하나 — FFmpeg 의 `codec_whitelist` 옵션 값 그대로 |
| 값 | 32개: `aac`, `alac`, `flac`, `mp3float`, `mp2`, `opus`, `vorbis`, `wmav1`, `wmav2`, `wmapro`, `wmavoice`, `wmalossless`, `ac3`, `eac3`, `amrnb`, `amrwb`, `adpcm_ima_wav`, `adpcm_ms`, `pcm_alaw`, `pcm_f32be`, `pcm_f32le`, `pcm_f64be`, `pcm_f64le`, `pcm_mulaw`, `pcm_s16be`, `pcm_s16le`, `pcm_s24be`, `pcm_s24le`, `pcm_s32be`, `pcm_s32le`, `pcm_s8`, `pcm_u8` (research R2. 녹음기 코덱 10개 — `mp2`·`wmapro`·`wmavoice`·`wmalossless`·`ac3`·`eac3`·`amrnb`·`amrwb`·`adpcm_ima_wav`·`adpcm_ms` — 는 운영자 결정 2026-11-10) |
| 쓰는 곳 | 읽기 자식만: (1) 형식 단계의 `codec_whitelist`(`avformat_find_stream_info` 의 해독기), (2) 흐름마다 코덱 문맥 옵션의 `codec_whitelist`(해독의 해독기), (3) 길이 재기의 첫 음성 흐름 해독기 이름 확인 |
| 쓰지 않는 곳 | 받기·워커(녹음을 해석하지 않는다), 명령줄(`decode_samples`, FR-005) |
| 같은 값의 사본 | `docs/server-security.md` §2 의 `<!-- stt:audio-decoders -->` 표. `tests/test_server_security_doc.py` 가 집합으로 맞춘다 |
| 규칙 | 음성 해독기만. 영상·이미지·자막 해독기 이름은 넣지 않는다(FR-001). 이름 하나를 더하거나 빼는 변경은 상수·문서 표·형식별 시험을 함께 바꾼다 |

녹음에 따라 일어나는 일([contracts/reader.md](contracts/reader.md) §3):

| 녹음의 트랙 | 길이 재기 | 해독 | 작업 |
|---|---|---|---|
| 목록 안 음성 하나 이상(첫 음성 트랙이 목록 안) + 영상·그림 트랙 있거나 없거나 | `ok`, 처음 잰 길이 = 컨테이너 길이(없으면 첫 음성 트랙의 묶음 길이 합) | `ok`, 첫 음성 트랙만 | 지금처럼 진행 |
| 첫 음성 트랙의 코덱이 목록 밖(Speex, GSM, MPEG Layer I, IMA·Microsoft 밖의 ADPCM 등) | `failed`(MP3 라고 적힌 상자 안 MPEG Layer I 는 이름이 `mp3float` 로 보여 `ok`) | `failed`(목록 밖 해독기를 열 때 `ArgumentError` — 위 MP1 과 배포 전에 받아 둔 녹음의 재전사) | 받기 `unreadable_audio`, MP3 라고 적힌 상자의 MP1 은 처리 `unreadable_audio` |
| 음성 트랙 없음(영상만) | `failed` | — | 받기 `unreadable_audio` (지금과 같다) |

## 2. 잘림 판정 (spec 「잘림 기준」)

| 속성 | 값 |
|---|---|
| 기준 | `TRUNCATION_THRESHOLD_S = 1.0` 초 (`src/stt/server/worker.py`) |
| 처음 잰 길이 | `job.duration_s` — 받기의 길이 재기 값(`job.json`, 필수 필드). 재전사는 앞 작업의 값을 그대로 가진다 |
| 해독된 길이 | 읽기 결과의 바이트 수 ÷ 2 ÷ 16 000 (= `audio.wav` 의 프레임 수 ÷ 16 000) |
| 판정 | `처음 잰 길이 − 해독된 길이 ≥ 1.0` 이면 참. 해독된 길이가 더 길거나 차이가 1초 미만이면 거짓 |
| 판정하는 때 | 읽기 단계가 `ok` 이고 `audio.wav` 를 쓴 직후, GPU 단계 전. 읽기가 실패·빼앗김·상한이면 판정하지 않는다 |
| 참일 때 | 워커 표준 출력에 고정된 한 줄([contracts/operator-log.md](contracts/operator-log.md) §1). 작업 상태·응답·결과·`spec.json` 은 그대로 |
| 드러나는 곳 | 워커 단위 로그뿐 — 응답·결과·작업 기록에는 없다(FR-007, 새 필드 없음) |

잰 사실(research R4): 정상 녹음에서 처음 잰 길이가 더 긴 차이는 최대 0.046초(WMA), 기준 녹음 둘은 차이가 0 이하다. 끝을 자른 녹음은 머리가 길이를 적는
형식(색인이 앞인 M4A, Xing·Info 머리의 MP3, FLAC, 대부분의 WMA)에서만 줄이 생긴다.

## 3. 원인 종류 (spec 「원인 종류」)

`ENGINE_ERROR_CAUSES` (`src/stt/server/worker.py`) — 작업이 `engine_error` 로 끝날 때 워커 줄 `<request_id> failed engine_error <cause>` 의
마지막 낱말. 응답의 `failure` 는 지금과 같다.

| `<cause>` | 정하는 곳(워커) | 입력 |
|---|---|---|
| `reader_unavailable` | 읽기 단계 | `reader_client.decode(...).outcome == "unavailable"` |
| `recording_unusable` | 읽기 단계 | `reader_client.RecordingError` (녹음 열기 실패·일반 파일 아님 — 아무것도 보내지 않음) |
| `unexpected` | 읽기 단계 | 그 밖의 `OSError`·`ValueError` |
| `engine_failed` | GPU 단계 | `outcome.json` 이 `{"status": "failed", "reason": "engine_error", …}` |
| `engine_no_verdict` | GPU 단계 | 판정이 쓸 만하지 않음(`_NO_VERDICT`) |

- 집합은 닫혀 있다. 새 길이 생기면 이 표·상수·문서 표(`<!-- stt:engine-error-causes -->`)를 함께 고친다. 읽기 단계에서 잡는 것은 `OSError`(`RecordingError` 포함)와 `ValueError` 뿐이고, 둘 가운데 따로 이름이 없는 것을 `unexpected` 로 묶는다(경계 사례 6).
  그 밖의 예외는 잡지 않고 002 처럼 올라간다(모두 잡는 `except Exception` 은 두지 않는다 — 헌법 V).
- 원인 이름은 기록에만 있다. `Failure`(응답에 들어가는 이유·문장)에는 더하지 않는다.

## 4. 녹음 열기 오류 `RecordingError` (내부)

| 속성 | 값 |
|---|---|
| 정의 | `class RecordingError(OSError)` (`src/stt/server/reader_client.py`) |
| 내는 곳 | `_open_recording`: 열기의 `OSError` → 같은 errno 로, 일반 파일이 아님 → errno 없이 |
| 열기 플래그 | `O_RDONLY`, `O_NOFOLLOW`, `O_CLOEXEC`, `O_NONBLOCK` — FIFO 에서도 바로 돌아온다 |
| 잡는 곳 | 워커: `recording_unusable`. 받기: 지금처럼 요청별 예외(`intake error <errno 이름 또는 RecordingError>`, 001 FR-032a) |
| 약속 | 이 예외가 나면 읽기 단위에 연결하지 않았다(읽기 인스턴스가 뜨지 않는다) |

## 5. 실패 지도에서 바뀌는 칸 (002 data-model §5 에 더함)

| 단계 | 끝난 방식 | 002(지금) | 003 |
|---|---|---|---|
| 읽기 준비 | 녹음 파일이 없음 | 워커가 `StopIteration` 으로 멈춤 → 다음 시작·청소에서 `failed(interrupted)` | `engine_error` · 「The server could not start the audio reader.」 · 줄 `… recording_unusable` |
| 읽기 준비 | 녹음이 일반 파일이 아님(디렉터리 등) | 워커가 `ValueError` 로 멈춤 → `interrupted` | 같음(위) |
| 읽기 준비 | 녹음이 FIFO | 열기에서 영원히 막힘 | 같음(위) |
| 읽기 준비 | 녹음 열기 권한·기술자 부족 | `engine_error`, 줄 원인 없음 | `engine_error` · 같은 문장 · `recording_unusable` (다시 확인에서도 기술자가 계속 모자라면 워커가 멈추고 줄이 없다 — operator-log.md §2) |
| 읽기 | 띄우지 못함(`unavailable`) | `engine_error`, 줄 원인 없음 | 같은 응답 · `reader_unavailable` |
| 읽기 | 그 밖의 `OSError`·`ValueError` | `OSError` 는 `engine_error`, `ValueError` 는 워커 멈춤 | `engine_error` · 같은 문장 · `unexpected` |
| 읽기 | 길이 재기에서 목록 밖 음성 코덱(Speex·GSM 등) | (받아들여 전사) | 받기 `unreadable_audio` · 「The recording could not be read as audio.」 |
| 읽기 | 해독에서 목록 밖 음성 코덱(배포 전에 받은 녹음의 재전사) | (전사) | `unreadable_audio` · 같은 문장 |
| 읽기 → GPU | `ok`, 해독된 길이가 1초 이상 짧음 | (아무 기록 없음) | 결과는 그대로 · 잘림 줄 |
| GPU | 엔진의 `engine_error` 판정 | `engine_error`, 줄 원인 없음 | 같은 응답 · `engine_failed` |
| GPU | 엔진이 판정 없이 끝남 | `engine_error`, 줄 원인 없음 | 같은 응답 · `engine_no_verdict` |

나머지 칸(시작 뒤 실패의 `unreadable_audio`, 상한의 `processing_timeout`, 빼앗김의 `stopped`, 워커가 죽음의 `interrupted`, GPU 분류)은 그대로다.

## 6. 프로세스 시작과 신호

| 프로세스 | 띄우는 명령 | `sys.path[0]` | SIGINT |
|---|---|---|---|
| 받기·워커·감독(세 단위의 주 프로세스) | 단위의 `ExecStart` → 감싸는 셸 → 콘솔 스크립트 `bin/stt` | 스크립트 폴더(Nix 저장소, 읽기 전용). 바꾸지 않는다 | 감독만 `SIG_IGN`(`serve()` 맨 앞). 받기·워커는 그대로 |
| 해독 자식 | `(sys.executable, "-I", "-m", "stt.server.reader", "child", op, fd, fd)` | 현재 폴더가 들어가지 않음(`-I`) | 감독의 `SIG_IGN` 을 물려받음 |
| 엔진 | `(sys.executable, "-I", "-m", "stt.server.engine", work_dir)` | 현재 폴더가 들어가지 않음(`-I`) | 그대로 |

## 7. 해독된 임시 음성 (002 data-model §3 의 「만드는 쪽」을 고침)

| 항목 | 002 | 003 |
|---|---|---|
| 만들기 | `O_EXCL` 로 0600 파일을 만들고 닫은 뒤 경로로 다시 열어 `wave` 가 씀 | 같은 기술자(`O_EXCL`·`O_NOFOLLOW`·`O_CLOEXEC`, 0600)를 파일 객체로 감싸 `wave` 가 씀. 경로로 다시 열지 않음 |
| 「다 지우기」와 겹침 | 지워진 뒤 다시 열면 `0640` 으로 새로 생김(최대 363 MB, 몇 초) | 지워진 아이노드에 쓰고 경로에는 다시 생기지 않음 — 어느 순간에도 0600 이거나 없음(FR-012) |
