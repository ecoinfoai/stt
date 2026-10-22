# Data Model: 교내 서버 전사

- 입력: [spec.md](spec.md) Key Entities·FR, [research.md](research.md) 결정
- 형식의 정본은 [contracts/](contracts/) 의 JSON Schema 다. 이 문서는 뜻·검증 규칙·상태 변화·시각 계산을 적는다.
- 모든 시각은 UTC, RFC 3339 초 단위(`2026-10-22T05:00:00Z`)로 적는다. 「자정」만 `Asia/Seoul` 로 계산한다(R7).
- 식별자·필드 이름은 영어다(헌법 「개발 환경과 흐름」 언어 규칙).

## 1. 엔티티 한눈에

| 엔티티 | 누가 만드나 | 어디 | 공개 여부 |
|---|---|---|---|
| Request (transcribe / delete) | 호출하는 쪽 | `requests/inbox/<request_id>.json` (+ 첨부) | 계약 — `request.schema.json` |
| Response (job status / delete outcome / refusal) | stt | `requests/outbox/<request_id>.json` | 계약 — `response.schema.json` |
| Result | stt | `requests/outbox/<request_id>/transcript.txt`, `result.json` | 계약 — `result.schema.json` |
| Job | stt | `jobs/<request_id>/job.json` | 내부 |
| Audio | stt | 기록 `audio/<audio_id>.json`, 녹음 파일 `audio/<audio_id>/audio.<ext>` — 녹음을 지울 때 파일 폴더만 지운다(R21-4) | 내부 |
| Work | stt | `work/<request_id>/` (엔진 작업 폴더) | 내부 |
| Queue marker | stt | `queue/<seq>-<request_id>` (빈 파일) | 내부 |

배치와 권한은 [contracts/README.md](contracts/README.md) §2.

**보관 기한의 여유**(R21-1): 24시간 상한은 1분마다 도는 청소가 집행한다. 그래서 모든 24시간 기한을 2분 당겨 계산한다 —
`RETENTION_DUE = 24h − 2min`(23시간 58분). 청소 주기와 실행 시간을 더해도 24시간 안에 지워진다. 자정은 당기지 않는다(「직후」 = 1분 안).

## 2. Request — 호출하는 쪽이 쓰는 것

### 2.1 공통

| 필드 | 형식 | 규칙 |
|---|---|---|
| `schema_version` | `1` | 계약 판. 다르면 `invalid_request` |
| `kind` | `transcribe` \| `delete` | |
| `request_id` | 소문자 UUID | 파일 이름 `<request_id>.json` 과 같아야 한다. 요청 하나에 하나, 다시 쓰지 않는다. 응답의 `request_id` 는 언제나 파일 이름이다 |
| `requested_by` | `^[A-Za-z0-9._:@-]{1,128}$` | 호출하는 쪽이 확인한 이용자 표식(불투명 값). stt 는 같은 요청자 확인에만 쓰고 로그에 남기지 않는다(FR-037) |

### 2.2 transcribe

| 필드 | 형식 | 규칙 |
|---|---|---|
| `audio` | `{file}` 또는 `{from_request}` 중 하나 | `file`: `<request_id>.audio.<ext>`, ext ∈ {m4a, mp3, wav, flac, ogg, opus, aac, wma}. 같은 이름의 첨부가 inbox 에 있어야 한다. `from_request`: 앞 transcribe 요청의 `request_id` — 같은 요청자이고 그 녹음이 아직 있어야 한다(FR-003) |
| `language` | `^[a-z]{2,3}$` | 엔진이 아는 언어 코드여야 한다(faster-whisper 의 언어 목록, R5). `auto` 는 받지 않는다 |
| `diarize` | bool | 화자 구분 여부 |
| `hints` | 문자열 1~200개(선택) | 각 1~100자, 앞뒤 공백·제어 문자 없음, 중복 없음. 빈 배열은 `invalid_request`(US5-6). 순서대로 엔진에 넣는다(FR-006) |
| `delete_audio` | `after_job` \| `at_midnight` | 이 녹음의 삭제 시기(§5) |

의미 검사(스키마 밖, 받기 단계): `request_id` = 파일 이름 = `audio.file` 의 앞부분, 첨부 존재, 언어 목록, 녹음 길이 ≤ 3시간(10,800초), 녹음을 읽을 수 있음.

### 2.3 delete

| 필드 | 형식 | 규칙 |
|---|---|---|
| `target` | 소문자 UUID | 지울 transcribe 요청의 `request_id`. 요청자가 다르거나 없으면 같은 답(`refused`)을 낸다 — 있는지 없는지 드러내지 않는다 |
| `scope` | `result` \| `everything` | `result` = 「결과 지우기」, `everything` = 「지금 지우기」(FR-005) |

답(`outcome`): 남의 것·없는 것 → `refused`. `result` — 결과가 있으면 `deleted`, 있었으나 이미 지워졌으면(`result_deleted_at` 이 있음 —
`deleted` job 포함) `already_deleted`, 생긴 적이 없으면(대기·처리 중·실패, 결과 없이 지운 job) `no_result`. `everything` — 이미 `deleted` 면
`already_deleted`, 아니면 `deleted`(R20-7, R21-12). request_id 가 파일 이름과 다른 delete 요청은 `refusal` 이다(§2.4).

### 2.4 받기 검사와 응답의 꼴 (R20-2…5)

| 경우 | 응답 |
|---|---|
| 이름이 `<UUID>.json` 이 아님 | 없음. 파일(과 같은 앞부분의 첨부)을 지운다 |
| 요청 파일이 일반 파일이 아님·심볼릭 링크·링크 수 > 1·1 MiB 초과·읽을 수 없음·UTF-8 JSON 아님 | `refusal`(`invalid_request`) — 내용을 쓰지 않는다 |
| 스키마 위반 | `refusal`(`invalid_request`) |
| delete 요청이 뒤 검사(이름 일치)에서 실패 | `refusal`(`invalid_request`) |
| transcribe 가 스키마는 맞고 뒤 검사에서 실패(이름 일치·첨부 없음·첨부가 일반 파일 아님·모르는 언어 → `invalid_request`, 해독 실패 → `unreadable_audio`, 길이 → `audio_too_long`, 원본 → `source_unavailable`) | `failed` job 상태, `accepted_at` 없음 |

- 받기는 본 `*.json` 을 하나도 남기지 않는다(남으면 path 단위가 되풀이해 깨운다). 첨부도 `O_NOFOLLOW` 로 열어 일반 파일·링크 수 1 을 확인한다.
- 거절할 때 지우는 첨부는 언제나 **파일 이름 앞부분**의 `<파일 id>.audio.*` 뿐이다. 본문의 `audio.file` 이 다른 앞부분을 가리켜도 건드리지 않는다(R21-9).
- 이름이 UUID 가 아닌 파일을 버릴 때는 파일 이름 없이 고정 문구 한 줄을 로그에 남긴다(R21-16).
- 중복: job 기록이 있으면 기록의 `duplicate_refused_at` 을, 기록이 없는 응답(거절·delete 답)이면 응답 파일을 읽어 그 칸을 더해 원자적으로 다시
  쓴다(R21-11).
- `detail` 은 `<JSON 경로>: <규칙마다 정한 고정 문구>` 꼴(예: `hints: items must be unique`), 500자 이하, 요청 값을 담지 않는다. 검사는 `kind` 로
  고른 갈래(`$defs/transcribe`·`$defs/delete`)에 하고, `kind` 가 없거나 모르는 값이면 `kind` 를 가리킨다.

## 3. Job — 받아들인 transcribe 요청

```text
Job
  request_id     str (UUID)          job 의 이름. 응답·결과 파일 이름과 같다
  requested_by   str                 같은 요청자 확인용
  seq            int                 받은 순서(1부터, 줄 순서의 기준)
  accepted_at    time
  audio_id       str (UUID)          쓰는 녹음(§4). from_request 면 원래 녹음의 id
  language       str
  diarize        bool
  hints          [str] | null        끝나면(done/failed/deleted) 지운다 — §6
  delete_audio   after_job | at_midnight
  duration_s     float               받기 단계에서 잰 녹음 길이
  state          queued | running | done | failed | deleted
  started_at, finished_at            time | null
  failure        {reason, detail, cause?} | null   cause 는 reason = audio_deleted 일 때만(§5)
  result_deleted_at                  time | null
  deleted_at                         time | null   (지금 지우기)
  duplicate_refused_at               time | null   (같은 request_id 가 다시 왔을 때)
  expires_at     time = accepted_at + RETENTION_DUE(23h58m)   이때(1분 안에) job 의 모든 것(응답·결과·기록)이 사라진다
```

### 3.1 상태 변화

```text
            받기 단계 거절 — 스키마는 맞은 transcribe 만(§2.4. 그 밖은 refusal 응답)
 (request) ───────────────────────────────────────────────▶ failed   (accepted_at 없음)
     │
     │ 받아들임
     ▼
  queued ──(워커가 꺼냄, GPU 잠금 얻음)──▶ running ──(엔진 결과 게시)──▶ done
     │                                      │
     │                                      ├──(엔진 실패·시간 초과·중단)──▶ failed
     │                                      │
     ├──(녹음이 예정대로 지워짐)──▶ failed(audio_deleted) ◀──┤
     │                                      │
     └──(지금 지우기)──────────▶ deleted ◀──┘   ※ done·failed 에서도 「지금 지우기」면 deleted
```

- 끝 상태는 `done`·`failed`·`deleted` 다. 끝 상태에서 바뀌는 것은 `result_deleted_at`(결과 지우기), `deleted`(지금 지우기),
  `duplicate_refused_at`, `updated_at`, 응답의 녹음 칸(`delete_policy`·`delete_after`·`delete_at`·`deleted_at` — 뒤에 온 2차가 정책을 바꾸거나
  녹음이 지워질 때)뿐이다.
- `done`·`failed` job 에 「지금 지우기」가 오면 상태는 `deleted` 가 되고, 결과가 있었으면 `result_deleted_at` 도 적는다. `deleted` 응답은 `result`
  (`deleted_at` 포함)·`started_at`·`finished_at` 을 남기고 `failure` 는 뺀다(응답 스키마는 `failed` 밖에서 `failure` 를 허용하지 않는다, R21-12).
- 끝 상태로 가는 모든 길(워커 판정·시간 초과·지우기 요청·원본 삭제·죽은 워커)은 `lifecycle.finish` 하나를 거친다 — 힌트 삭제, 대기 표시 삭제,
  정책이 `{after_job: 이 job}` 이면 녹음 삭제, 응답 갱신(R20-9).
- `running` 은 워커가 GPU 잠금을 얻고 엔진을 띄운 뒤에만 된다. 잠금을 기다리는 동안은 `queued` 이고 대기 순서 1 이다.
- 워커가 죽어 `running` 으로 남은 job 은 다음 워커 시작 때나 청소 단계가 워커 생존 잠금이 비어 있음을 볼 때 `failed(interrupted)` 가 된다(R10).

### 3.2 대기 순서

- 대기 표시 `queue/<seq>-<request_id>` 는 워커를 깨우는 신호일 뿐이다. job 이 `queued` 를 떠나는 즉시(시작·실패·지움) 지운다 — 남겨 두면
  워커 path 단위가 워커를 되풀이해 깨운다(R20-1). 순서의 정본은 job 기록의 `seq` 다.
- 줄은 `seq` 오름차순 하나다(FIFO). 대기 순서 = `queued` job 가운데 자기보다 `seq` 가 작은 것의 수 + 1. 처리 중인 job 은 세지 않는다.
- 받아들임·시작·끝남·지움·만료 때마다 모든 `queued` job 의 응답을 다시 쓴다(상태 잠금 아래, §8).

## 4. Audio — 받은 녹음 하나

```text
Audio
  audio_id         str (UUID)   처음 가져온 transcribe 요청의 request_id
  file             audio.<ext>  첨부를 복사한 것(stt 소유). 원래 첨부 이름은 original_name 에
  original_name    str          `<request_id>.audio.<ext>` — 전사록 머리글의 「원본」 칸
  owner            str          requested_by
  received_at      time         처음 받아들인 시각
  hard_deadline    time = received_at + RETENTION_DUE(23h58m)
  policy           {after_job: <request_id>} | {at: time}   가장 나중에 받은 요청이 정한다(§5)
  users            [request_id]  이 녹음을 쓰는 job 들
  deleted_at       time | null
  deleted_by       schedule | after_job | request | null
```

- 녹음을 지우면 파일 폴더 `audio/<audio_id>/` 만 지우고 기록은 남긴다(다른 job 의 응답이 `deleted_at` 을 쓴다). 기록은 그 녹음을 쓰는 마지막
  job 의 `expires_at` 에 지운다(R21-4).

## 5. 녹음 삭제 시각 계산 (FR-030·FR-031·FR-034)

- `next_midnight(t)` = `t` 다음의 `Asia/Seoul` 00:00 (t 가 정확히 00:00 이면 그다음 날 00:00).
- 요청 R 을 받아들이면 그 녹음의 `policy` 를 R 의 것으로 바꾼다:
  - `delete_audio = after_job` → `{after_job: R}`
  - `delete_audio = at_midnight` → `{at: next_midnight(R.accepted_at)}`
- 녹음이 지워지는 때 = 다음 가운데 먼저 오는 것:
  1. `policy.after_job = X` 이고 X 가 끝 상태가 됐을 때(그 직후),
  2. `policy.at` 시각,
  3. `hard_deadline`,
  4. 이 녹음을 쓰는 어느 job 에든 「지금 지우기」가 왔을 때.
- 녹음이 지워질 때 그 녹음을 쓰는 `queued`·`running` job 은 `failed(audio_deleted)` 가 된다(running 이면 엔진을 멈춘다). `failure.cause` 가
  원인을 가른다: `midnight`(정책 시각) · `retention_limit`(`hard_deadline`) · `deleted_on_request`(다른 job 의 「지금 지우기」)(R20-8).
- **삭제 시각이 지났으면 이미 지운 것으로 본다**(R21-7): 받기·워커는 지금 시각이 그 녹음의 삭제 시각(`policy.at`·`hard_deadline` 가운데 먼저 오는
  것)을 지났으면 청소를 기다리지 않고 먼저 그 원인으로 녹음을 지운다. 그 뒤 그 녹음을 가리키는 2차는 `source_unavailable`, 기다리던 job 은 시작하지
  않고 `failed(audio_deleted)` 다(spec 경계 사례 8).
- 응답에는 `audio.delete_policy`, `after_job` 이면 그 정책을 정한 요청 `audio.delete_after`(스키마가 반드시 요구하고 `at_midnight` 이면 금지), 그
  job 기준의 예정 시각 `audio.delete_at`(= min(policy.at, hard_deadline), after_job 이면 hard_deadline)을 싣는다.
  지워지면 `audio.deleted_at`. 녹음의 정책이 바뀌거나 녹음이 지워지면 그 녹음을 쓰는 모든 job 의 응답을 다시 쓴다(`users`).

예: 1차(23:10, `at_midnight`) → 녹음 정책 `{at: 00:00}`. 1차 완료 23:16. 2차(23:30, `from_request`=1차, `after_job`) → 정책 `{after_job: 2차}`.
2차 완료 23:48 → 바로 삭제. 2차가 00:00 전에 오지 않으면 00:00 에 삭제되고, 그 뒤 온 2차는 `source_unavailable` 로 거절된다.

## 6. 그 밖의 삭제 (FR-030·FR-033·FR-036)

| 대상 | 지우는 때 |
|---|---|
| job 의 `hints` | job 이 끝 상태가 되는 즉시(FR-030 의 「늦어도」를 앞당긴 것 — 엔진만 쓴다) |
| 결과(`outbox/<id>/`) | 「결과 지우기」·「지금 지우기」 즉시, 아니면 `expires_at` |
| 대기 표시(`queue/<seq>-<id>`) | job 이 `queued` 를 떠나는 즉시 |
| 응답·job 기록 | `expires_at`(= 받은 때 + 23h58m). 받기 단계에서 거절된 요청의 응답은 처리 시각 + 23h58m |
| 녹음 기록(`audio/<audio_id>.json`) | 그 녹음을 쓰는 마지막 job 의 `expires_at` |
| 게시 중간 폴더(`outbox/.<id>.tmp/`)·원자적 쓰기의 임시 파일(`.*.tmp`)·기록 없는 녹음 폴더 | ctime 으로 1시간이 지나면 청소. 죽은 워커를 정리할 때는 게시 중간 폴더를 바로(R21-2) |
| `interrupted` 가 된 job 의 `outbox/<id>/`(게시 rename 뒤 마무리 전에 워커가 죽은 경우) | 그 job 을 `interrupted` 로 만들 때 |
| 엔진 작업 폴더(`work/<id>/`, 임시 WAV 포함) | 엔진이 끝나는 즉시. 강제 종료로 남은 것은 다음 워커 시작 때와 청소 단계(워커가 없을 때) |
| inbox 의 남은 파일 | 받기 단계가 처리하면 즉시. 짝 없는 첨부·처리되지 못한 파일은 ctime 으로 1시간 뒤 청소(로그 한 줄, 파일 이름 없음) |
| delete 요청의 응답 | 처리 시각 + 23h58m |

## 7. Result — 완료된 job 이 내는 것

- `transcript.txt`: 명령줄과 같은 형식(머리글 + `[MM:SS]` 문단, 화자 구분 시 `[MM:SS] 화자N:`, 불확실 문단은 `화자N(?)`).
  말소리가 없으면 머리글만 있다(US5-7). 머리글의 「원본」 칸은 `original_name`.
- `result.json`:

```text
Result
  schema_version  1
  request_id
  transcript      "transcript.txt"
  paragraphs      [Paragraph]
  summary         RunSummary

Paragraph
  start, end      float 초(end ≥ start)
  speaker         str | null     화자 구분 시 전사록과 같은 라벨(예: `화자1`), 아니면 null
  text            str
  uncertain       bool           화자 판정이 불확실(짧은 문단). 화자 구분이 없으면 false

RunSummary
  model           "large-v3"
  device          "cuda"
  language        str
  audio_duration_s, processing_s   float
  diarized        bool           요청의 diarize 그대로(말소리가 없어 화자 구분을 건너뛰어도)
  hints           null | {applied: int, total: int, dropped: [str]}   (FR-020, SC-008)
```

- 문단 경계 규칙은 명령줄과 같다(쉼 2초, 800자). 화자 구분이 없을 때도 끝 시각을 낸다 — 새 `build_timed_paragraphs` 가 끝 시각을 함께 남긴다
  (R8-1, `build_paragraphs` 와 명령줄 출력은 그대로).
- 문단 수는 따로 내지 않는다 — `len(paragraphs)` 로 셀 수 있다(헌법 I, R21-3).

## 8. 잠금

| 잠금 | 파일 | 누가 | 무엇을 막나 |
|---|---|---|---|
| GPU | `/run/stt/gpu.lock` | 워커(엔진이 도는 동안), 명령줄 `transcribe`·`batch`·`enroll`(GPU 를 쓰는 동안) | GPU 를 둘이 함께 쓰기(FR-011). 읽기 전용으로 열어 `flock` (R3) |
| 상태 | `state.lock` | 받기·워커·청소 | job·audio 기록과 응답을 동시에 고치기. 짧게만 쥔다 — 전사하는 동안 쥐지 않는다 |
| 워커 생존 | `worker.lock` | 워커(실행 내내) | 워커 둘이 함께 돌기, 그리고 청소 단계의 「워커가 살아 있나」 판단 |
| 받기 | `intake.lock` | 받기 단계 | 받기 둘이 같은 요청을 함께 처리하기 |
| 청소 | `sweep.lock` | 청소 단계 | 청소 둘이 함께 돌기(타이머와 손으로 돌린 청소가 겹칠 때) |

## 9. 실패 이유 (`failure.reason`)

| 코드 | 언제 | 단계 |
|---|---|---|
| `invalid_request` | 스키마·의미 검사 실패(빠진 칸, 이름 불일치, 첨부 없음, 모르는 언어, 빈 힌트) | 받기 |
| `audio_too_long` | 녹음 > 3시간 | 받기 |
| `unreadable_audio` | 녹음을 열 수 없거나 음성 흐름이 없음, 또는 엔진이 해독하지 못함 | 받기·엔진 |
| `source_unavailable` | `from_request` 가 없음·다른 요청자·녹음이 이미 지워짐(구분해 알리지 않음) | 받기 |
| `audio_deleted` | 처리가 끝나기 전에 녹음이 지워짐. `cause` 가 반드시 붙는다(§5) | 받기·워커·청소 |
| `processing_timeout` | 처리 시간 > max(녹음 길이, 5분) | 워커 |
| `gpu_out_of_memory` | GPU 메모리 부족(작은 모델로 대신하지 않음) | 엔진 |
| `gpu_unavailable` | 엔진이 GPU 를 찾지 못함(전사 엔진, 화자 구분 시 화자 엔진까지) | 엔진 |
| `models_unavailable` | 미리 받아 둔 모델이 없음 | 엔진 |
| `interrupted` | 워커가 처리 중에 끝남(재시작·전원) | 워커·청소 |
| `engine_error` | 그 밖의 엔진 오류. detail 은 일반 문구(내부 원문을 싣지 않음) | 엔진 |

- `detail` 은 사람이 읽을 영어 한 문장이다(코드에 한글 없음). 이용자에게 보일 한국어 문구는 호출하는 쪽이 이유(와 원인)별로 정한다.
- `invalid_request` 의 detail 은 §2.4 의 꼴이다. 다른 이유의 detail 은 이유마다 정한 고정 문장이다 — 예외 메시지·PyAV 오류 원문·내부 경로·
  요청 값을 싣지 않는다(US5-8, R20-4).
- 같은 `request_id` 가 다시 오면 상태는 그대로 두고 `duplicate_refused_at` 만 적는다(경계 사례 「중복」).
