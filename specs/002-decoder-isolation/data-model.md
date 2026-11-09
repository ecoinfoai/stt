# Data Model: 녹음 해독 격리 (Phase 1)

- 작성: 2026-11-09 · 근거: [spec.md](spec.md) Key Entities, [research.md](research.md) R4~R16.
- 001 의 기록(Job·Audio)·상태·응답 형식은 바뀌지 않는다(`specs/001-server-transcription/data-model.md`). 이 문서는 새로 생기는 것과 바뀌는 내부
  자료만 적는다.

## 1. 읽기 인스턴스 (spec 「읽기 프로세스」)

연결 하나에 systemd 가 띄우는 `stt-reader@<n>-<pid>-<uid>.service` 하나. 안에 감독(주 프로세스) 하나와 해독 자식 하나가 있다(R5).

| 속성 | 값 | 출처 |
|---|---|---|
| 신원 | 템플릿의 일회용 UID·GID(61184–65519) 하나를 동시에 도는 모든 인스턴스가 나눠 쓴다. 인스턴스 사이는 UID 가 아니라 이름공간(`PrivatePIDs`·`PrivateIPC`·`PrivateTmp`)으로 가른다 | `DynamicUser=yes` |
| 읽을 수 있는 것 | 받은 녹음 기술자 하나(읽기 전용, 일반 파일) | 요청의 SCM_RIGHTS |
| 쓸 수 있는 것 | 해독이면 받은 파이프의 쓰는 쪽 하나. 파일은 없다(`LimitFSIZE=0`, 사설 tmpfs 만 쓰기 가능) | 요청의 SCM_RIGHTS |
| 닿을 수 없는 것 | `/var/lib/stt`·`/var/lib/stt-models`·`/run/stt`(가림 + 권한), GPU 장치(사설 `/dev`), 망(이름공간 없음, 소켓 생성 금지) | [contracts/module.md](contracts/module.md) §2 |
| 상한 | 메모리 768M(스왑 0), 감독의 시간 상한(요청의 `limit_s`, 최대 240초), systemd `RuntimeMaxSec=300`, 작업 32 | R9 |
| 끝난 방식 | `ok` · `failed`(오류·판정 없음·메모리·시간 상한·출력 이상) · 띄우지 못함 — 호출하는 쪽이 정한다(§5) | R6 |
| 작업과의 연결 | 호출하는 쪽(받기·워커)만 안다. 읽기 인스턴스는 작업 ID·경로·요청자를 받지 않는다 — 그래서 그 로그에도 없다 | FR-019 |
| 수명 | 연결 하나. 끝나면 cgroup·사설 tmpfs·IPC 가 사라진다(`CollectMode=inactive-or-failed`) | FR-011 |

## 2. 읽기 요청과 판정 (내부 프로토콜)

SOCK_SEQPACKET 메시지 셋. 형식은 UTF-8 JSON 객체이고, 한 메시지는 4096 바이트 이하다. 전체 규칙은 [contracts/reader.md](contracts/reader.md)에 있다.

| 메시지 | 방향 | 내용 | 함께 넘기는 기술자 |
|---|---|---|---|
| 요청 | 호출하는 쪽 → 감독 | `{"op": "probe", "limit_s": 30}` 또는 `{"op": "decode", "limit_s": 240}` | probe: [녹음] · decode: [녹음, 파이프 쓰는 쪽] |
| 시작 | 감독 → 호출하는 쪽 | `{"started": true}` 또는 `{"started": false}` | — |
| 판정 | 감독 → 호출하는 쪽 | probe: `{"status": "ok", "duration_s": <유한한 수 ≥ 0>}` · decode: `{"status": "ok"}` · 둘 다: `{"status": "failed"}` | — |

- 판정 검사(호출하는 쪽, 경계): 키가 정확히 그것뿐이고, 값의 형이 맞고, `duration_s` 는 유한한 수이며 0 이상이어야 한다. 하나라도 어긋나면
  「시작 뒤 실패」다.
- 감독은 해독 자식의 끝 코드와(probe 는) 자식이 결과 파이프에 쓴 256 바이트 이하의 JSON 만 보고 판정을 만든다.

## 3. 해독된 음성

| 항목 | 값 |
|---|---|
| 위치 | `work/<request_id>/audio.wav` — 그 작업의 작업 폴더(0700) 안, 0600 |
| 형식 | RIFF WAV, PCM s16le, 1채널, 16 000 Hz. 머리는 워커가 `wave` 로 쓰고, 샘플은 읽기 자식이 낸 그대로 |
| 크기 | 짝수 바이트, `MAX_DECODED_BYTES` = 362 880 000 이하(3시간 × 1.05). 어기면 그 읽기는 실패다 |
| 만드는 쪽 | 워커(읽기 단계). 작업마다 새로 만든다 — 재전사도 같다 |
| 읽는 쪽 | 엔진(GPU 단계)만 — 전사(float32 = int16 / 32768.0)와 화자 구분(`read_wav`) |
| 지우는 때 | 작업 폴더와 함께: `lifecycle.finish`(완료·실패·지우기·매일 정리), 워커의 마무리, 죽은 워커 정리. 001 의 처리 중 음성과 같다(FR-012, R13) |
| 드러나는 곳 | 없음 — 상태 기록·응답·결과·로그 어디에도 나오지 않는다 |

## 4. 엔진 명세 `work/<request_id>/spec.json` (내부, 계약 밖)

| 필드 | 001 | 002 |
|---|---|---|
| `request_id`, `original_name`, `language`, `diarize`, `hints`, `duration_s` | 있음 | 그대로 |
| `audio_path` (녹음 파일 경로) | 있음 | **없앤다** — GPU 단계는 녹음의 위치를 모른다(FR-010) |

엔진은 해독된 음성을 정해진 이름 `work_dir/audio.wav` 로 찾는다. 명령줄 인자는 지금처럼 작업 폴더 하나뿐이고, 환경은 `TMPDIR`·`HF_HOME`·
`HF_HUB_OFFLINE` 뿐이다.

## 5. 실패 지도

작업 상태와 실패 이유 목록(11개)은 001 그대로다. 아래는 단계와 끝난 방식에서 이유를 정하는 표다. 고정 문장은 영어 그대로 응답의
`failure.detail` 에 들어간다.

| 단계 | 끝난 방식 | 받기에서 | 처리에서 |
|---|---|---|---|
| 읽기 | 띄우지 못함: 접속 실패, 「시작」 전 끊김, `started: false`, 30초 안에 「시작」 없음 | 예외 → 001 FR-032a(로그에 ID·이유, inbox 에서 치움, 응답 없음) | `engine_error` · 「The server could not start the audio reader.」 |
| 읽기 | 시작 뒤 실패: `failed`, 판정 없는 끊김, 틀린 판정, `limit_s` + 5초 넘김, 출력이 홀수 바이트이거나 상한을 넘음 | `unreadable_audio` · 「The recording could not be read as audio.」(지금 문장) | `unreadable_audio` · 같은 문장 |
| 읽기 | `ok` 이고 길이 > 3시간 | `audio_too_long`(지금과 같다) | — |
| 읽기 | 작업 상한이 먼저 옴 | — (받기에는 작업 상한이 없다) | `processing_timeout`(지금 문장) |
| 읽기 | 작업을 빼앗김(「다 지우기」·23:58) | — | 그 일을 한 쪽이 정한 대로(`deleted` 또는 `failed(audio_deleted)`), 워커 로그는 `stopped` |
| 읽기·GPU | 워커가 죽음 | — | `failed(interrupted)` — 다음 워커 시작이나 청소에서(지금과 같다) |
| GPU | 엔진의 판정 | — | `gpu_out_of_memory`·`gpu_unavailable`·`models_unavailable`·`engine_error`(지금 분류). 엔진이 `unreadable_audio` 를 내면 판정 없음으로 보아 `engine_error` |
| GPU | 엔진이 판정 없이 끝남 | — | `engine_error` · 「The engine stopped without a verdict.」(지금 문장) |
| GPU | 작업 상한의 남은 시간을 다 씀 | — | `processing_timeout` |

- 작업 상한 = `deadlines.processing_limit(duration_s)` = max(녹음 길이, 5분)이고, 읽기 단계를 시작할 때부터 잰다. 읽기의 기다림 =
  min(작업 상한까지 남은 시간, `limit_s` + 5초)다. 먼저 끝나는 쪽이 이유를 정한다(FR-018).
- 해독 상한 240초 < 작업 상한의 하한 300초이므로, 실제로는 멈춘 해독이 늘 `unreadable_audio` 로 끝난다(R9).

## 6. 처리 흐름 — 상태는 그대로, 안쪽 단계가 둘

```text
queued ──(GPU 잠금, start)──▶ running ─┬─ 읽기 단계: 읽기 인스턴스 → work/<id>/audio.wav
                                        │     ├─ 실패 → failed(unreadable_audio | engine_error | processing_timeout)
                                        │     └─ 빼앗김 → (deleted | failed audio_deleted), 로그 stopped
                                        └─ GPU 단계: 엔진(남은 작업 상한) → done | failed(GPU 분류 | processing_timeout)
```

- 응답의 `state` 는 두 단계 모두 `running` 이다. 안쪽 단계는 응답에 나오지 않는다(계약 그대로).
- 받기: 첨부 복사 → 읽기 인스턴스로 길이 재기 → 받아들임(`queued`) 또는 거절(`unreadable_audio`·`audio_too_long`). 재전사(`from_request`)는
  지금처럼 길이를 다시 재지 않고 앞 작업의 길이를 쓴다.

## 7. 제한 예외 기록 (spec 「제한 예외 기록」)

`docs/server-security.md` §1 의 표. 서버 단위마다 한 줄이다.

| 칸 | 뜻 |
|---|---|
| 단위 (`unit`) | `stt-intake`·`stt-worker`·`stt-sweep`·`stt-models`·`stt-reader@` |
| 호출 필터 (`filter`) | 실제 설정(`@system-service`, 읽기 단위는 더 좁은 목록) |
| 더한 예외 (`added`) | 일반 서비스 범위를 넘어 허용한 호출·묶음. 없으면 `-` |
| 좁힘 (`narrowed`) | `~` 로 뺀 호출·묶음(읽기 단위). 없으면 `-` |
| 이유·관측 근거 (`reason`·`observed`) | 예외마다: 무엇이 그 호출을 쓰는가, 관측한 날, 커널 기록의 호출 번호(R2) |
| 쓰기·실행 메모리 금지 (`mdwe`·`mdwe_evidence`) | `on` 또는 `off` + 거부된 지점(R3). 호스트 판정 전에는 `pending` |

- 규칙: `nix/module.nix` 의 예외 줄마다 이 표의 같은 줄을 가리키는 주석이 있다. 표에 없는 예외는 두지 않는다(FR-002).
  `tests/test_server_security_doc.py` 가 `nix/module.nix` 에서 `SystemCallFilter` 를 가진 모든 단위를 찾아 이 표(표식
  `<!-- stt:syscall-table -->`)와 비교한다. 문서는 한국어지만 시험이 읽는 표식·칸 이름·상태값은 영어다(헌법 언어 규칙).

## 8. 구성 요소 판 기록 (spec 「구성 요소 판 기록」)

`docs/server-security.md` §4. 표식은 `<!-- stt:versions -->`, 칸 이름은 괄호 안의 영어다.

| 칸 | 뜻 | 저장소에서 맞추는 곳 |
|---|---|---|
| 구성 요소 (`component`) | 예: PyAV 휠(안의 FFmpeg libavcodec·libavformat), nixpkgs `ffmpeg` 명령, faster-whisper, CTranslate2, torch, pyannote.audio | — |
| 서버 경로에서 하는 일 (`role`) | 녹음 바이트 해석 `decodes-recordings` / 명령줄만 `cli-only` / 해독된 음성만 다룸 `decoded-audio-only` | — |
| 지금 판 (`version`) | 예: av 18.0.0(Python 3.12)·17.1.0(Python 3.10) | `uv.lock`(시험이 대조) |
| 고정한 곳 (`pinned_in`) | `uv.lock` / `flake.lock` 의 nixpkgs 커밋 | `flake.lock`(시험이 대조) |
| 들어온 날 (`since`) | 그 판을 넣은 커밋과 날짜 | `git log` |
| 마지막 갱신일 (`last_updated`) | 판을 바꾼 마지막 날 | `git log` |
| 기준 녹음 비교 (`comparison`) | `decodes-recordings` 줄에서, 이 판으로 기준 녹음을 비교한 날 | 시험이 `last_updated` 와 같거나 뒤인지 본다(US5-3) |

- 정책 절: 누가(운영자), 주기(매달), 볼 곳(FFmpeg 보안 공지·PyAV 새 판·nixpkgs 의 ffmpeg 갱신), 보안 공지 뒤 7일 안에 올리거나 이유를 적음,
  올릴 때의 순서(잠금 파일 갱신 → 빌드·시험 → 배포 → 기준 녹음 비교 → 이 표 고치기). 시험이 읽는 표식
  `<!-- stt:policy owner=operator cadence=monthly advisory_days=7 sources=ffmpeg-security,pyav-releases,nixpkgs-ffmpeg -->` 을 함께 둔다.
- 매달 확인 기록표(`stt:monthly-checks`): 확인한 날(`date`) · 본 곳(`sources`) · 결과(`result`: 새 판 없음 / 올린 커밋). 첫 달과 마지막 달
  사이에 빠진 달이 없어야 한다(US5-4).
- 보안 공지 기록표(`stt:advisories`): 공지(`advisory`) · 구성 요소(`component`) · 나온 날(`published`) · 올린 날(`updated`, 없으면 `-`) · 올리지
  않는 이유(`reason`). 줄마다 `updated` 가 `published` + 7일 이하이거나 `reason` 이 차 있어야 한다(US5-5).

## 9. 회신 인계 문서 (spec 「회신 인계 문서」)

`specs/002-decoder-isolation/handoff/p1-reply.md`, `p2-reply.md`.

| 절 | 내용 | P1 | P2 |
|---|---|---|---|
| 확인 결과 | 인계 §6 의 `systemctl show` 출력 그대로, 시험 결과(정상 녹음·텍스트 파일·재전사 3번) | 네 단위 | 다섯 단위 + 권한 점검 |
| 요청 1 | 필터·아키텍처·예외와 이유·쓰기·실행 메모리 금지의 단위별 답 | ✔ | 읽기 단위 덧붙임 |
| 요청 2·3 | 어느 단계가 어느 단위에서 도는가(지금 → P2 뒤), 격리 방식, 작업별 분리 | 지금 상태 + 계획 | 배포된 것 |
| 요청 4 | 구성 요소의 판·날짜·정책 | 판·날짜 | 정책 + 첫 매달 확인 |
| §5 질문 | 계약 불변, 텍스트 파일 → `unreadable_audio`, 단위 비정상 종료를 아는 곳(R15) | ✔ | ✔ |
| 질문 6 | 보관 기간(R17) | ✔ | — |
| 남는 위험 | 그 단계에서 아직 열린 것(예: P1 만 배포된 동안의 같은 계정·같은 저장 위치) | ✔ | ✔ |
