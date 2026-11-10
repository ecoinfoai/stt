# 읽기 프로토콜 — 받기·워커 ↔ 읽기 단위 (내부 계약)

stt 안의 약속이다. 호출하는 쪽(bhu-llm)의 계약(`specs/001-server-transcription/contracts/`)과는 관계가 없고, 판 번호 없이 stt 와 함께 바뀐다.
근거는 [research.md](../research.md) R4~R10 이다. 단위 설정은 [module.md](module.md) 에 있다.

## 1. 연결

- 소켓: `/run/stt-reader.sock`, `AF_UNIX` `SOCK_SEQPACKET`. 소유 `stt:stt`, 0600 — 받기·워커(`stt`)와 root 만 접속한다.
- `Accept=yes`: 연결 하나마다 systemd 가 `stt-reader@.service` 인스턴스 하나를 띄우고, 그 연결을 표준 입력으로 준다.
- 연결 하나에 요청 하나. 녹음 하나를 한 번 읽는다(FR-006). 판정을 보낸 뒤 양쪽 모두 닫는다.

## 2. 메시지

모두 UTF-8 JSON 객체 한 개이고, 한 메시지는 `MESSAGE_LIMIT`(4096 B) 이하다. 다른 키가 있거나 형이 틀리면 받은 쪽이 그 연결을 실패로 끝낸다.

| # | 방향 | 본문 | SCM_RIGHTS 기술자 |
|---|---|---|---|
| 1 | 호출하는 쪽 → 감독 | `{"op": "probe", "limit_s": <수>}` | `[녹음]` |
| 1 | 호출하는 쪽 → 감독 | `{"op": "decode", "limit_s": <수>}` | `[녹음, 파이프 쓰는 쪽]` |
| 2 | 감독 → 호출하는 쪽 | `{"started": true}` · `{"started": false}` | — |
| 3 | 감독 → 호출하는 쪽 | `{"status": "ok", "duration_s": <수>}`(probe) · `{"status": "ok"}`(decode) · `{"status": "failed"}` | — |

- `limit_s`: 0보다 크고 그 `op` 의 상한(§4) 이하인 유한한 수. 감독은 이를 어기는 요청에 `{"started": false}` 로 답한다(호출하는 쪽의 버그다).
- 녹음 기술자는 일반 파일을 읽기 전용으로 연 것이고, 파이프 기술자는 `os.pipe()` 의 쓰는 쪽이다. 디렉터리 기술자는 넘기지 않는다
  (`systemd.exec(5)` 의 `DynamicUser` 경고).
- 메시지 3 의 `duration_s` 는 유한한 수이고 0 이상이다. 3시간 한도 검사는 받기가 한다(001 과 같다).

## 3. 순서와 시간

```text
호출하는 쪽                                  감독(읽기 인스턴스)
connect ───────────────────────────────▶ (systemd 가 인스턴스를 띄움)
요청 + 기술자 ─────────────────────────▶ 요청·기술자 검사 → 해독 자식 띄움(기술자 넘김, 자기 사본 닫음)
              ◀──────────────────────── {"started": true|false}      ← START_TIMEOUT_S 안에
(decode: 파이프를 읽어 WAV 로 씀)           자식의 끝 · 연결 끊김 · limit_s 가운데 먼저 온 것을 기다림
              ◀──────────────────────── {"status": ...}               ← limit_s + VERDICT_GRACE_S 안에
close                                       자식을 거둔 뒤 끝남
```

- 감독은 요청(메시지 1)을 최대 `START_TIMEOUT_S`(30초, `serve` 의 `request_timeout_s` 기본값) 동안만 기다린다. 그 안에 요청이 없거나 요청 없이 연결이
  닫히면 아무것도 보내지 않고 끝 코드 1 로 끝난다. 호출하는 쪽은 연결하자마자 요청을 보내므로 이 한도와 호출하는 쪽의 「시작」 대기(`start_timeout_s`)는 기본값이 같다.
- 호출하는 쪽이 일찍 닫으면(작업을 빼앗김) 감독은 자식을 SIGKILL 로 죽이고, 판정 없이 끝낸다.
- 감독은 `limit_s` 가 지나면 자식을 SIGKILL 로 죽이고 `{"status": "failed"}` 를 보낸다.
- 감독이 무엇 때문에 죽든 인스턴스가 끝나고, systemd 가 그 cgroup 의 모든 프로세스를 죽인다.

## 4. 상수 (`src/stt/server/reader.py` 에 한 번만 둔다)

| 이름 | 값 | 근거 |
|---|---|---|
| `SOCKET_PATH` | `/run/stt-reader.sock` | R12 |
| `MESSAGE_LIMIT` | 4096 B | — |
| `RESULT_LIMIT` | 256 B — 자식이 감독에게 주는 probe 결과 | — |
| `PROBE_LIMIT_S` | 30초 | R9: 컨테이너 길이 3 ms |
| `DECODE_LIMIT_S` | 240초 | R9: 3시간 최악 추정 18초의 13배, 작업 상한의 하한 300초보다 짧음 |
| `START_TIMEOUT_S` | 30초 | 인스턴스 시작 + 자식 띄우기 < 1초 |
| `VERDICT_GRACE_S` | 5초 | 감독이 자식을 거두고 판정을 보낼 여유 |
| `SAMPLE_RATE` | 16 000 Hz, 1채널, s16le | faster-whisper 와 같음(R7) |
| `MAX_DECODED_BYTES` | 362 880 000 B — 3시간 × 1.05 × 16 000 × 2 | R8 |
| `AUDIO_FORMATS` | `mov,mp3,wav,flac,ogg,aac,asf` — `intake.py:49` 에서 옮김 | 001 FR-004a |
| `EARLY_STOP_EXIT` | 3 — decode 자식의 끝 코드: 출력은 첫 깨진 조각 앞까지 온전하다 | 경계 사례 10, FR-023 |

## 5. 감독의 의무 (`stt server read`)

1. 표준 라이브러리만 불러온다. 녹음 기술자와 파이프에서 읽지도 쓰지도 않는다.
2. 표준 입력의 연결에서 메시지 1 을 최대 `START_TIMEOUT_S` 동안 기다려 받는다(§3). 본문과 기술자의 개수·종류(일반 파일·파이프)를 검사하고, 어긋나면 `{"started": false}` 를 보낸다.
3. 해독 자식을 띄운다: `[sys.executable, "-m", "stt.server.reader", "child", <op>, <녹음 fd>, <출력 fd>]`, `pass_fds` 는 그 둘뿐이다. 출력은
   probe 이면 감독이 만든 결과 파이프, decode 이면 받은 파이프다. 자식의 표준 입력·출력·오류는 `/dev/null` 이고, 나머지 기술자는 모두 닫힌다
   (`close_fds`). 띄운 뒤 감독은 넘긴 기술자의 자기 사본을 닫는다.
4. 띄우기에 성공하면 `{"started": true}`, 실패하면(`OSError`) `{"started": false}` 를 보낸다.
5. 자식의 끝, 연결의 끊김(POLLHUP·EOF), `limit_s` 를 함께 기다린다. 끊김이나 `limit_s` 가 오면 자식을 SIGKILL 로 죽이고 거둔다.
6. 판정 `ok` 의 조건: 자식이 끝 코드 0 으로 끝났고(decode 는 `EARLY_STOP_EXIT` 도 같다 — 아래 7), probe 는 결과 파이프의 `RESULT_LIMIT` 이하 JSON
   `{"duration_s": <유한한 수 ≥ 0>}` 이 맞아야 한다. 그 밖은 모두 `failed` 다. 연결이 끊긴 뒤에는 아무것도 보내지 않는다.
7. 로그는 아래 다섯 문장뿐이다(표준 오류 → 단위 로그, `StandardError=journal`). 호출하는 쪽이 끊은 경우(정상)와 정상 판정(끝 코드 0 에 결과가 맞음)에는 아무것도
   쓰지 않는다. `<mode>` 는 `probe` 또는 `decode`, `<n>` 은 정수다. 작업 ID·경로·파일 이름·해독기 문구·녹음의
   메타데이터는 감독이 알지 못하고 쓰지도 않는다.

   | 자식의 끝 | 한 줄 | 판정 |
   |---|---|---|
   | 끝 코드 `<n>` ≠ 0 (decode 의 `EARLY_STOP_EXIT` 제외) | `reader child <mode> exited with status <n>` | `failed` |
   | 신호 `<n>` 으로 죽음(예: 시스템 호출 필터의 SIGSYS 31, 메모리 상한의 SIGKILL 9) | `reader child <mode> killed by signal <n>` | `failed` |
   | decode 가 끝 코드 `EARLY_STOP_EXIT` | `reader child decode stopped early at invalid data` | `ok` |
   | 감독이 `limit_s` 에 자식을 죽임 | `reader child <mode> timed out` | `failed` |
   | probe 가 끝 코드 0 인데 결과가 §5.6 의 검사를 못 넘음 | `reader child probe returned an invalid result` | `failed` |

   `EARLY_STOP_EXIT` 는 「해독이 첫 깨진 조각(`InvalidDataError`)에서 멈췄고 출력은 그 앞까지 온전하다」는 알림이다. 판정은 `ok` 로 그대로이고(경계 사례 10),
   호출하는 쪽에는 아무것도 달라지지 않는다. 장악된 자식은 이 알림도 정상 종료도 속일 수 있으므로 이 줄은 운영자 신호일 뿐 보안 통제가 아니다.
   probe 가 `EARLY_STOP_EXIT` 로 끝나면 일반 끝 코드로 본다.

## 6. 해독 자식의 의무 (`python -m stt.server.reader child probe|decode`)

- 공통: 인자로 받은 녹음 기술자를 파일 객체로 열어 공유 해독 모듈 `stt.audio_decode.open_container(recording, format_whitelist=AUDIO_FORMATS)`
  로 연다(받기·워커는 이 모듈을 불러오지 않는다 — 자식 함수 안에서만 가져온다). 이 함수는 `av.open(..., container_options={"format_whitelist": …})`
  를 부르고, `metadata_errors="ignore"` 는 설치된 PyAV 가 19 미만일 때만 덧붙인다(PyAV 19 는 이 키워드를 없앴고 잘못된 UTF-8 메타데이터를 스스로
  대체 문자로 읽는다). 경로는 쓰지 않는다. 예외는 모두 끝 코드 1 이다(문장은 어디에도 쓰지 않는다).
- probe: 001 의 `probe_duration` 과 같은 뜻이다. 오디오 흐름이 없으면 실패. 컨테이너 길이가 있으면 그것, 없으면 첫 오디오 흐름의 패킷 길이 합(해독
  없음). 출력 기술자에 `{"duration_s": …}` 를 쓰고 끝 코드 0. 길이를 고르는
  부분은 열린 컨테이너를 받는 순수 함수로 두어, 시험이 프로세스 안에서 가짜 컨테이너로 볼 수 있게 한다.
- decode: `stt.audio_decode.write_s16` 이 faster-whisper 1.2.1 `decode_audio` 와 같은 순서로 한다 — `container.decode(audio=0)`, 첫 `InvalidDataError` 에서 멈춤
  (`decode` 는 생성기라 한 번 예외를 내면 끝난다. faster-whisper 의 「건너뛰기」도 실제로는 같은 멈춤이다), `AudioFifo` 로
  50만 샘플씩 묶기(`pts=None`), `AudioResampler(format="s16", layout="mono", rate=16000)`, 끝에 `None` 으로 비우기. 나온 프레임의 바이트를
  차례로 출력 기술자에 쓴다(머리 없음). `write_s16` 은 멈췄는지를 돌려주고, 끝 코드는 온전히 끝났으면 0, 깨진 조각에서 멈췄으면 `EARLY_STOP_EXIT`
  다(그 앞까지의 출력은 모두 쓴 뒤다).
- 같은 해독을 명령줄도 쓴다: `stt.audio_decode.decode_samples(path)` 는 `write_s16` 의 결과를 `int16 / 32768.0` 의 float32 로 돌려주며, 형식 허용
  목록(`format_whitelist`)은 읽기 단위만 쓴다(명령줄은 `stt fetch` 가 받은 mp4·webm 등도 연다). faster-whisper 에 경로를 넘기지 않는다.
  해독이 깨진 조각에서 멈췄으면 `decode_samples` 는 `on_early_stop(<그때까지의 초>)` 를 한 번 부르고, 명령줄(`transcribe_file`)은 그것으로 표준 오류에
  영어 경고 한 줄을 낸다. 깨끗한 녹음의 출력은 바이트까지 그대로다.
- 같음의 시험: PyAV 18 이하에서는 사인파 파일 여러 형식에서 decode 출력이 `faster_whisper.audio.decode_audio(path)` 의 `int16` 값과 바이트까지
  같다. PyAV 19 이상에서는 그 함수가 `metadata_errors` 때문에 돌지 않으므로, 16 kHz 모노 s16 WAV(`make_wav`)가 자기 표본과 정확히 같게 나오는지를 본다
  (손실 형식은 길이·크기만 본다). 두 갈래 모두 단언한다.

## 7. 호출하는 쪽의 의무 (`src/stt/server/reader_client.py`)

- 녹음은 내부 경로(`audio/<audio_id>/audio.<ext>`)를 `O_RDONLY | O_NOFOLLOW | O_CLOEXEC` 로 연다. 파이프는 `O_CLOEXEC` 로 만들고, 보낸 뒤 자기
  쓰는 쪽을 닫는다.
- 받는 메시지는 `MESSAGE_LIMIT` 까지만 읽고 §2 대로 엄격히 검사한다. 대답이 장악된 읽기에서 올 수 있다고 보고 다룬다.
- 결과를 다음 다섯 가지로 나눈다. 이유로 바꾸는 표는 [data-model.md](../data-model.md) §5 다.
  - `unavailable` — 「시작」 전의 모든 실패.
  - `failed` — 「시작」 뒤의 모든 실패. decode 의 출력이 홀수 바이트이거나 `MAX_DECODED_BYTES` 를 넘는 것도 여기 든다.
  - `ok`
  - `stopped` — 작업을 빼앗김.
  - `timeout` — 작업 상한.
- decode 를 기다리는 동안 `poll_s`(2초)마다 job 이 아직 `running` 인지 본다. 아니면 연결과 파이프를 닫고 `stopped` 를 낸다.
- 읽기 단위를 쓸 수 없을 때 받기나 워커 안에서 직접 해독하는 대체 길은 없다(헌법 V) — 그것은 격리를 말없이 끄는 일이다.

## 8. 명령줄 (001 `contracts/cli.md` §1 에 더함)

| 명령 | 바뀜 |
|---|---|
| `stt server read` | 새 명령. 읽기 단위의 `ExecStart`. 상태 폴더도 단계 잠금도 쓰지 않고, 상태 폴더 주인 검사(001 FR-038)도 하지 않는다 — 일회용 사용자로 돈다. 표준 입력이 연결 소켓이 아니면 끝 코드 2 |
| ↳ 끝 코드 | 0 — 요청에 답했다(거절 `{"started": false}` 포함). 또는 호출하는 쪽이 끊었다. 1 — 요청이 오지 않았다(`START_TIMEOUT_S` 안에 없거나 요청 없이 닫힘). 감독 내부 예외도 1(트레이스백). 2 — 표준 입력이 연결된 `AF_UNIX` `SOCK_SEQPACKET` 소켓이 아니다 |
| `stt server intake`, `stt server work` | `--reader-socket PATH`(기본 `/run/stt-reader.sock`) |

## 9. 시험용 대역의 자리 (FR-028)

| 자리 | 무엇을 바꾸나 | 쓰는 곳 |
|---|---|---|
| 해독 자식 | 감독의 자식 명령(인자로 주입) — 오류로 끝남, 멈춤, SIGKILL·SIGSYS 로 끝남, 일찍 멈춤 알림(`EARLY_STOP_EXIT`), 엉터리 결과, 상한을 넘는 출력 | pytest |
| 읽기 인스턴스 전체 | 흉내 소켓이 띄우는 명령(`tests/server/fake_reader.py`) — 「시작」 없이 끊기, `started: false`, 판정 없이 끊기 | pytest |
| 읽기 인스턴스 전체 | 대본을 따르는 흉내(`tests/server/scripted_reader.py`) — 엉터리 메시지, 침묵, 닫힘 관찰(호출하는 쪽이 연결·파이프를 닫았는지 기록) | pytest |
| 읽기 인스턴스 전체 | VM specialisation 의 `ExecStart` — 권한 점검(결과를 판정 대신 돌려줌), 실패 방식들, 메모리 넘기기 | VM |

대역은 녹음 내용을 해석하지 않는다. 해독기를 노리는 파일은 어느 자리에도 없다.
