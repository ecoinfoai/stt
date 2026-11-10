# 읽기 단위의 바뀜 — 해독기 목록, 감독의 신호와 안내, 자식의 시작, 녹음 열기 (003)

002 의 내부 계약 [`specs/002-decoder-isolation/contracts/reader.md`](../../002-decoder-isolation/contracts/reader.md)를 바탕으로 이 기능이 더하고
바꾸는 것만 적는다. 메시지 셋(요청·시작·판정)의 모양, 기술자, 상수의 값, 끝 코드 0·1·2 의 뜻은 그대로다. 근거는 [research.md](../research.md)
R1·R2·R6·R8~R10 이다.

## 1. 상수 (`src/stt/server/reader.py`, 002 §4 에 더함)

| 이름 | 값 | 근거 |
|---|---|---|
| `AUDIO_DECODERS` | `"aac,alac,flac,mp3float,mp2,opus,vorbis,wmav1,wmav2,wmapro,wmavoice,wmalossless,ac3,eac3,amrnb,amrwb,adpcm_ima_wav,adpcm_ms,pcm_alaw,pcm_f32be,pcm_f32le,pcm_f64be,pcm_f64le,pcm_mulaw,pcm_s16be,pcm_s16le,pcm_s24be,pcm_s24le,pcm_s32be,pcm_s32le,pcm_s8,pcm_u8"` (32개) | R2. FFmpeg 해독기 이름(`AVCodec.name`)의 쉼표 목록, `AUDIO_FORMATS` 와 같은 꼴. 영상·이미지·자막 해독기는 없다. 녹음기 코덱 10개는 운영자 결정(2026-11-10) |
| `MANUAL_RUN_LINE` | `"stt server read only serves a connection that systemd passes from stt-reader.socket on standard input"` | R9 |
| `DEFAULT_CHILD_COMMAND` | `(sys.executable, "-I", "-m", "stt.server.reader", "child")` — `-I` 를 더함 | R10 |

- `AUDIO_DECODERS` 는 코드에서 이 한 곳에만 있다. 보안 문서 §2 의 표가 같은 값이고 시험이 둘을 맞춘다([operator-log.md](operator-log.md) §5).
- 목록에 이름을 더하거나 빼는 변경은 이 상수, 문서 표, A 층의 형식별 시험(가능하면 그 코덱의 사인파 녹음 하나)을 함께 바꾼다.

## 2. 공유 해독 모듈 (`src/stt/audio_decode.py`)

```python
def open_container(source: BinaryIO | str, *, format_whitelist: str | None = None,
                   codec_whitelist: str | None = None) -> Any: ...
```

- `codec_whitelist` 가 `None` 이 아니면:
  1. `av.open(..., container_options={"format_whitelist": …, "codec_whitelist": codec_whitelist})` — `avformat_find_stream_info` 가 여는 해독기에 걸린다.
  2. 연 컨테이너의 흐름마다 코덱 문맥이 있으면 그 `options` 에 `"codec_whitelist": codec_whitelist` 를 둔다 — PyAV 가 해독할 때 여는 해독기
     (`avcodec_open2(…, options)`)에 걸린다. 목록 밖이면 FFmpeg 가 `EINVAL` 로 거절하고 PyAV 는 `av.error.ArgumentError` 를 낸다.
- `codec_whitelist` 가 `None`(기본)이면 지금과 똑같다. 명령줄의 `decode_samples` 는 넘기지 않는다(FR-005).
- `metadata_errors` 처리(PyAV 19 미만)와 `format_whitelist` 는 그대로다.

## 3. 해독 자식 (`python -I -m stt.server.reader child probe|decode <녹음 fd> <출력 fd>`, 002 §6 을 바꿈)

- 공통: 녹음 기술자를 `open_container(recording, format_whitelist=AUDIO_FORMATS, codec_whitelist=AUDIO_DECODERS)` 로 연다.
- probe: 길이를 재기 전에 **첫 음성 흐름의 해독기**를 확인한다. 음성 흐름이 없거나(지금과 같다), 그 흐름에 해독기가 없거나(`codec_context is None`),
  해독기 이름(`codec_context.codec.name`)이 `AUDIO_DECODERS` 에 없으면 끝 코드 1 이다. 그다음은 002 와 같다(컨테이너 길이, 없으면 묶음 길이 합).
  이 확인은 열린 컨테이너를 받는 순수 함수 `first_audio_decoder_allowed(container) -> bool`(`src/stt/server/reader.py`)로 두어 시험이 가짜
  컨테이너로 프로세스 안에서 본다. 거짓이면 probe 는 끝 코드 1 이다.
- decode: 002 와 같다(`write_s16`). 첫 음성 흐름의 해독기가 목록 밖이면 PyAV 가 그 해독기를 열 때 `ArgumentError` 가 나고 끝 코드 1 이다.
- 감독 쪽에서 보면 두 경우 모두 `reader child <probe|decode> exited with status 1` 한 줄과 판정 `failed` 다 — 텍스트 파일과 같은 줄이다. 받기는
  `unreadable_audio`, 처리는 `unreadable_audio` 로 끝낸다(002 실패 지도 그대로).

| 녹음 | probe | decode | 열리는 비음성 해독기 |
|---|---|---|---|
| 형식 8가지·ALAC·PCM 변형·더한 녹음기 코덱(목록 안 코덱) | `ok`(값 그대로) | `ok`(바이트 그대로) | 없음 |
| 목록 안 음성 + 영상 트랙 / 표지 그림 | `ok`(값 그대로) | `ok`(음성만, 바이트 그대로) | 없음(`find_stream_info` 에서 거절) |
| 영상 트랙만 | `failed`(음성 흐름 없음) | — | 없음 |
| 목록 밖 음성 코덱만(Speex, GSM, MPEG Layer I, IMA·Microsoft 밖의 ADPCM …) | `failed`(이름 확인). 단 MP3 라고 적힌 상자(`.mp3` 안 MPEG Layer I)는 이름이 `mp3float` 로 보여 `ok` — 받기를 지나 처리에서 거절 | `failed`(`ArgumentError`) | 없음 |
| 녹음 확장자를 붙인 텍스트 | `failed`(`InvalidDataError`, 지금과 같다) | — | 없음 |

## 4. 감독 (`stt server read`, 002 §5·§8 에 더함)

- `serve()` 는 맨 처음 SIGINT 를 무시한다(`signal.signal(signal.SIGINT, signal.SIG_IGN)`). 해독 자식이 감독에게 SIGINT 를 보내도 감독은 자식의
  끝을 기다려 평소의 판정과 줄을 낸다(US4-1). 처분은 `serve()` 에서만 바꾼다 — 프로세스 안의 시험이 부르는 `_supervise` 는 바꾸지 않는다. 무시 처분은 exec 를 지나 상속되므로 해독 자식도 SIGINT 를 무시한 채 뜬다. 해롭지 않다 — 자식은 SIGINT 에 기대지 않고, 감독이 자식을 끝낼 때는 SIGKILL 을 쓴다.
- 표준 입력이 연결된 `AF_UNIX` `SOCK_SEQPACKET` 소켓이 아니면 표준 오류에 `MANUAL_RUN_LINE` 한 줄(끝에 줄바꿈)을 쓰고 끝 코드 2 로 끝난다(US4-4).
  표준 출력에는 아무것도 쓰지 않는다. 끝 코드 0·1 의 경우는 지금처럼 아무것도 쓰지 않는다.
- 감독이 단위 로그에 쓰는 줄은 002 의 다섯 줄과 이 한 줄이다. 이 줄에도 작업 ID·경로·파일 이름이 없다.

## 5. 호출하는 쪽 (`src/stt/server/reader_client.py`, 002 §7 을 바꿈)

```python
class RecordingError(OSError):
    """The recording could not be opened as a regular file; nothing was sent to the reader."""
```

- `_open_recording` 은 `os.open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)` 로 연다. `O_NONBLOCK` 은 FIFO 에서 열기가 막히지 않게 한다
  (일반 파일의 읽기에는 효과가 없다).
- 열기가 `OSError` 로 실패하면 같은 errno 의 `RecordingError` 를 낸다(`raise RecordingError(error.errno, error.strerror) from error`). 일반 파일이
  아니면 기술자를 닫고 errno 없는 `RecordingError("the recording is not a regular file")` 를 낸다.
- `probe()`·`decode()` 의 `Raises:` 는 「`RecordingError`: 녹음을 일반 파일로 열지 못함, 아무것도 보내지 않았다」와 「`ValueError`: `job_limit_s` 가
  유한한 양수가 아님」, 그리고 그 밖의 `OSError`(파이프·소켓 만들기, 「시작」 뒤의 입출력 오류)다. 다섯 결과는 그대로다.
- 받기는 지금처럼 예외를 올려 요청을 기록하고 치운다(001 FR-032a). 기록 줄은 `intake error <errno 이름>`(errno 가 있을 때) 또는
  `intake error RecordingError`(일반 파일 아님)다.

## 6. 워커의 읽기 준비 (`src/stt/server/worker.py`)

- `DEFAULT_ENGINE_CMD = (sys.executable, "-I", "-m", "stt.server.engine")` — `-I` 를 더함(R10).
- `_prepare` 는 녹음 경로를 `audio/<audio_id>/audio.<ext>` 로 정한다. `<ext>` 는 녹음 기록의 `original_name.rsplit(".", 1)[1]` 이다(받기의
  `_copy_attachment` 와 같은 규칙). `glob` 하지 않으므로 녹음이 없어도 `StopIteration` 이 나지 않고, 열기에서 `RecordingError`(ENOENT)가 된다.
- 읽기 단계의 예외는 [operator-log.md](operator-log.md) §2 의 원인 종류로 나뉜다. 녹음 열기 오류와 일시적 `OSError`·`ValueError` 는 워커를 멈추지 않는다(FR-009). 그 밖의 예외와, 다시 확인·마무리에서도 나는 같은 오류(작업 기록 읽기 불가, 기술자 부족 지속)는 워커를 멈춘다. 기술자 부족이면 작업은 다음 워커 시작이나 청소에서 `interrupted` 가 되고, 작업 기록이 계속 읽히지 않으면 `interrupted` 에 이르지 못한 채 시작마다 `clear_dead_worker` → `load_jobs` 가 실패해 대기열이 멈추고 새 요청은 `intake error` 로 응답 없이 버려진다(`docs/server-security.md` §3, 002 의 동작, 운영자가 그 기록을 고치거나 옮겨야 풀림. operator-log.md §2).
- `_write_audio` 는 만든 기술자(`O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW`, `0o600`)를 `os.fdopen` 으로 감싸 `wave.open(<파일 객체>, "wb")`
  에 넘긴다. 경로로 다시 열지 않는다(R11). `wave.open` 은 모듈로 부른다(저장 오류 시험의 바꿔치기 자리).

## 7. 시험용 대역의 자리 (002 §9 에 더함)

| 자리 | 무엇을 바꾸나 | 쓰는 곳 |
|---|---|---|
| 해독 자식 | `tests/server/fake_decode_child.py` 의 새 동작 `sigint-parent`: 부모(감독)에게 SIGINT 를 보내고 정상 probe 결과를 쓴 뒤 끝 코드 0 | pytest(US4-1) |
| 읽기 단위의 해독 자식 | VM 노드 설정의 `LD_PRELOAD` 감시 라이브러리: 진짜 `avcodec_open2` 가 성공했는데 문맥이 음성이 아니면 `abort()`. 운영 모듈에는 없다([module.md](module.md) §3) | VM(US1) |

대역과 감시 라이브러리는 녹음 내용을 해석하지 않는다. 해독기를 노리는 파일은 어느 자리에도 없다(FR-017).
