# 운영자 기록의 줄과 보안 문서의 바뀜 (003)

운영자가 보는 서버 단위 로그의 정확한 영어 문장과, `docs/server-security.md` 에 들어갈 표식·표·명령을 정한다. 호출하는 쪽(bhu-llm)의 계약
(`specs/001-server-transcription/contracts/`)과는 관계가 없다 — 응답·결과·실패 이유·고정 문장은 하나도 바뀌지 않는다. 근거는
[research.md](../research.md) R4~R6·R9·R13 이다.

## 1. 워커의 잘림 줄 (US2, FR-006)

| 항목 | 값 |
|---|---|
| 문장 | `decoded audio is shorter than the probed length` (상수 `worker.TRUNCATION_LINE`) |
| 기준 | `worker.TRUNCATION_THRESHOLD_S = 1.0` 초 |
| 조건 | 읽기 단계가 `ok` 이고 `audio.wav` 를 쓴 뒤, `job.duration_s − 해독된 바이트 ÷ 2 ÷ 16 000 ≥ 1.0` |
| 자리 | 워커의 표준 출력(작업 줄과 같은 흐름), 그 작업의 `<request_id> running` 줄 뒤, GPU 단계 전. 작업마다 많아야 한 줄 |
| 없는 것 | 작업 ID·경로·파일 이름·길이 값·녹음 내용·해독기 문구 |
| 결과 | 바뀌지 않는다. 작업은 GPU 단계대로 끝난다(엔진이 성공하면 앞부분 전사로 `done`, FR-007) |

- 재전사(`from_request`)는 앞 작업의 처음 잰 길이를 쓰므로, 잘린 녹음의 작업마다 줄이 하나씩 남는다(US2-5).
- 음성 묶음이 없는 녹음(해독된 길이 0)도 처음 잰 길이가 1초 이상이면 줄이 남는다(US2-4).
- 처음 잰 길이와 해독된 길이의 차이가 1초 미만이면(정상 녹음의 끝 차이, 002 의 7.75 ms 등) 줄이 없다(US2-2·3).
- 비교는 순수 함수 `is_truncated(probed_s: float, decoded_bytes: int) -> bool`(`src/stt/server/worker.py`)로 두어 경계(정확히 1.0초 짧음 → 참)를 프로세스 안에서 시험한다.

## 2. 워커의 서버 오류 줄 — 원인 종류 (US3, FR-008·FR-009)

줄 모양(바뀜): `<request_id> failed engine_error <cause>` — 이유 뒤에 공백 하나와 원인 이름 하나. 다른 실패 이유의 줄(`<request_id> failed <reason>`)은
그대로다. `engine_error` 줄에는 늘 원인 이름이 붙는다.

| `<cause>` | 단계 | 언제 | 응답의 `failure.detail`(그대로) |
|---|---|---|---|
| `reader_unavailable` | 읽기 | 읽기 클라이언트의 결과가 `unavailable`: 소켓 없음·접속 거부·「시작」 전 끊김·`started: false`·「시작」 기다림 넘김 | The server could not start the audio reader. |
| `recording_unusable` | 읽기 | `reader_client.RecordingError`: 녹음 없음, 심볼릭 링크, 일반 파일 아님(디렉터리·FIFO 등), 권한, 기술자 부족. 읽기 단위에 아무것도 보내지 않았다 | The server could not start the audio reader. |
| `unexpected` | 읽기 | 읽기 단계의 그 밖의 `OSError`·`ValueError`(파이프·소켓 만들기, 「시작」 뒤의 입출력 오류, `job_running` 확인의 오류) | The server could not start the audio reader. |
| `engine_failed` | GPU | 엔진이 `failed`·`engine_error` 판정을 씀 | The engine stopped because of an unexpected error. |
| `engine_no_verdict` | GPU | 엔진이 쓸 만한 판정 없이 끝남(판정 파일 없음·깨짐·결과 파일 빠짐·목록 밖 이유) | The engine stopped without a verdict. |

- 상수: `worker.ENGINE_ERROR_CAUSES = frozenset({"reader_unavailable", "recording_unusable", "unexpected", "engine_failed", "engine_no_verdict"})`.
- 읽기 단계의 어떤 예외도 워커를 멈추지 않는다. 그 작업만 위 표대로 끝나고 다음 작업으로 간다(US3-3).
- 줄에는 경로·파일 이름·녹음 내용·오류 문구·errno 가 없다. 분류 이름만 있다(US3-4).
- 「다 지우기」·23:58 정리로 작업을 빼앗긴 경우는 지금처럼 `<request_id> stopped` 다(원인 이름 없음).

## 3. 워커가 쓰는 줄 전체 (003 뒤)

```text
<request_id> running
decoded audio is shorter than the probed length          ← 새 줄(작업 ID 없음)
<request_id> done
<request_id> failed <reason>                             ← reason ≠ engine_error
<request_id> failed engine_error <cause>                 ← 바뀐 줄
<request_id> stopped
<request_id> failed audio_deleted
<request_id> failed interrupted
<request_id> work folder removal failed <errno 이름>
```

## 4. 읽기 단위의 손 실행 줄 (US4-4)

`stt server read only serves a connection that systemd passes from stt-reader.socket on standard input` — 표준 오류, 끝 코드 2([reader.md](reader.md) §4).

## 5. `docs/server-security.md` 의 바뀜 (FR-004·FR-016)

문서는 한국어다. 시험(`tests/test_server_security_doc.py`)은 아래 표식·영어 칸 이름·영어 값만 읽는다.

### 5.1 §2 녹음 읽기 격리 — 「해독기 목록」 절을 더한다

- 문장: 서버 경로(길이 재기·해독)는 아래 음성 해독기만 연다. 영상·이미지·자막 해독기는 녹음 상자에 그 트랙이 있어도 열리지 않는다 — 분석
  (`find_stream_info`)과 해독 모두 FFmpeg 의 `codec_whitelist` 로 거절된다. 영상 트랙이 든 녹음은 음성만 읽는다(운영자 결정 2026-11-10). 목록 밖 음성
  코덱의 녹음은 받을 때 `unreadable_audio` 다.
- 표식과 표:

  ```markdown
  <!-- stt:audio-decoders -->

  | decoder | codec | formats |
  |---|---|---|
  | `aac` | AAC | m4a, aac |
  | … (research R2 의 32개, 한 줄에 해독기 하나) |
  ```

  시험: `decoder` 칸의 값 집합 == `set(reader.AUDIO_DECODERS.split(","))`, 겹침 없음. 어긋나면 실패한다(US1-6).
- 남는 범위 한 단락: 분석기(parser)·형식(demuxer)은 목록이 막지 않는다. 길이 재기의 분석 중 영상 분석기가 영상 묶음 몇 개를 읽을 수 있고, 해독 중에도
  영상 묶음이 형식·분석기를 지난다(research R3 — 해독 단계에서 영상 묶음을 버리는 안은 운영자가 미뤘다, 2026-11-10). 공지가 분석기·형식에 관한 것이면
  지금처럼 판정한다.
- 감독의 줄 문단(「읽기 단위의 자식은 로그를 쓰지 않고 …」)에 §4 의 손 실행 줄을 더한다. 해독 자식과 엔진은 `python -I` 로 떠서 현재 폴더를 모듈
  찾는 길에 넣지 않는다는 한 줄도 더한다.

### 5.2 §3 작업 실패와 단위 비정상 종료 구별 — 줄 셋을 더한다

- 「작업 하나의 실패」: `engine_error` 줄의 모양과 원인 표.

  ```markdown
  <!-- stt:engine-error-causes -->

  | cause | stage | 뜻 | 볼 것 |
  |---|---|---|---|
  | `reader_unavailable` | read | … | `systemctl status stt-reader.socket`, `journalctl -u 'stt-reader@*'` |
  | `recording_unusable` | read | … | 그 작업의 녹음 폴더(`/var/lib/stt/audio/<audio_id>/`)가 있는지, 일반 파일·`stt` 소유·0600 인지. 녹음을 열지는 않는다 |
  | `unexpected` | read | … | 같은 시각의 워커 저널(기술자·메모리), `/var/lib/stt` 의 기록 |
  | `engine_failed` | gpu | … | 같은 시각의 GPU·모델 상태. 같은 녹음을 재전사해 되풀이되는지 |
  | `engine_no_verdict` | gpu | … | `systemctl status stt-worker`, `journalctl -k`(엔진 프로세스가 죽었는지) |
  ```

  시험: `cause` 칸의 값 집합 == `worker.ENGINE_ERROR_CAUSES`. 「읽기 단위를 쓸 수 없을 때」 항목의 `<request_id> failed engine_error` 를
  `<request_id> failed engine_error reader_unavailable` 로 고친다.
- 「끝이 잘린 녹음」: 줄, 기준, 볼 것, 경계.

  ````markdown
  <!-- stt:truncation threshold_s=1.0 -->

  ```text
  decoded audio is shorter than the probed length
  ```

  ```sh
  journalctl -u stt-worker -g ' running$|shorter than the probed length'
  ```
  ````

  - 이 줄 바로 앞의 `<request_id> running` 줄이 그 작업이다(워커는 한 번에 한 작업만 한다). 작업은 앞부분 전사로 `done` 이고 응답·결과는 같다.
  - 알아보는 형식: 머리가 전체 길이를 적는 녹음 — 색인이 앞인 M4A, Xing·Info 머리가 있는 MP3, FLAC, 대부분의 WMA. 알아보지 못하는 형식: WAV·Ogg·ADTS
    AAC(길이를 파일 자체에서 잰다). 색인이 뒤인 M4A(휴대폰 녹음 대부분)는 끝이 잘리면 받을 때부터 `unreadable_audio` 다.
  - 잘리지 않았는데 줄이 남는 경우(경계 사례 3): 처음 잰 길이가 추정값인 녹음(머리 없는 가변 비트율 MP3, ADTS AAC — 앞부분이 조용하면 길게 추정된다),
    컨테이너의 다른 트랙(영상)이 음성보다 긴 녹음. 줄은 「해독된 길이가 처음 잰 길이보다 짧다」는 사실만 말한다.
  - 깨진 조각에서 일찍 멈춘 해독은 읽기 단위의 `reader child decode stopped early at invalid data` 와 이 줄이 함께 남을 수 있다(경계 사례 4).
  - 시험: 표식의 `threshold_s` == `worker.TRUNCATION_THRESHOLD_S`, 문서에 `worker.TRUNCATION_LINE` 이 한 줄 그대로 있음.
- 「읽기 인스턴스의 비정상 종료와 일찍 멈춘 해독」: 감독의 줄 목록에 손 실행 줄을 더하고(시험: `reader.MANUAL_RUN_LINE` 이 문서에 그대로 있음), 「가장 흔한
  줄」의 원인 목록(텍스트 파일, 깨진 머리, 목록 밖 형식)에 「목록 밖 음성 코덱」을 더한다.

### 5.3 §4 판 기록과 갱신 정책

- `docs/server-security.md:238` 「후속 후보: 코덱 허용목록(쓸 수 있는 해독기 이름의 제한)은 넣지 않았다. 읽기 격리(P2)가 범위를 막는다(research R7).」를
  다음 뜻으로 바꾼다: 「코덱 허용목록: 003(배포 커밋·날짜)부터 서버 경로는 §2 『해독기 목록』의 음성 해독기만 연다. 영상·이미지·자막 해독기는 분석에서도
  해독에서도 열리지 않는다. 분석기·형식은 남는다(§2).」 배포 커밋과 날짜는 배포 때 채운다.
- 4.2 정책에 한 항목: 「영상·이미지·자막 해독기만의 공지는 서버 경로에서 열리지 않으므로(§2 해독기 목록) 4.3 의 `reason` 에 그 이유를 적을 수 있다. 분석기·
  형식·목록 안 음성 해독기의 공지는 지금처럼 7일 안에 올리거나 이유를 적는다.」 표식(`stt:policy`)과 기존 표의 칸은 바꾸지 않는다.
- 4.3 의 다섯 줄(2026-11-09 판정)은 그때의 기록이라 고치지 않는다.
