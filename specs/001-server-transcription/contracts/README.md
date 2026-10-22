# stt 서버 전사 계약 (bhu-llm ↔ stt)

- 이 폴더가 두 시스템의 결합 지점 전부다(헌법 VI). 형식의 정본은 JSON Schema 세 개다:
  [`request.schema.json`](request.schema.json) · [`response.schema.json`](response.schema.json) · [`result.schema.json`](result.schema.json).
  stt 패키지는 같은 파일을 `stt/server/schemas/` 에 싣고, 시험이 두 사본의 바이트 일치를 확인한다.
- 계약 판: `schema_version: 1`. 호환되지 않게 바꾸면 판을 올리고 핸드오프 문서로 알린다(헌법 VI). 필드 추가도 판을 올린다 —
  스키마가 `additionalProperties: false` 라서 받는 쪽 검사가 깨지기 때문이다.
- 뜻·시각 계산·상태 변화는 [`../data-model.md`](../data-model.md), 명령은 [`cli.md`](cli.md), 호스트 배치는 [`module.md`](module.md).
- 통로는 파일뿐이다. 열린 포트는 없다(tech-notes §1, geo-scout 요청함과 같은 모양).

## 1. 누가 무엇을 하나

| 호출하는 쪽(bhu-llm) | stt |
|---|---|
| 요청을 쓴다(inbox), 응답·결과를 읽는다(outbox) | 요청을 검사해 받아들이거나 거절하고, 차례로 전사하고, 응답·결과를 쓰고, 정한 때에 지운다 |
| `request_id` 를 만든다(UUID, 다시 쓰지 않음) | 요청자 표식을 믿고 같은 요청자 확인에만 쓴다 |
| 결과를 복사한 직후 「결과 지우기」를 보낸다 | 가져가지 않은 결과도 24시간에 지운다 |
| 실패 코드를 이용자 문구(한국어)로 바꾼다 | 실패 코드와 영어 설명 한 문장을 낸다 |

## 2. 폴더와 권한

```text
/var/lib/stt/                        stt:stt 0751 (StateDirectory, StateDirectoryMode=0751 — 기본값은 0755)
├── requests/                        stt:stt 0751
│   ├── inbox/                       stt:stt-requests 2770 + default ACL user:stt:rwx  — 호출하는 쪽이 쓴다
│   └── outbox/                      stt:stt-requests 2750                             — 호출하는 쪽이 읽는다
│       ├── <request_id>.json        응답(response.schema.json), 원자적 교체, 0640
│       └── <request_id>/            결과 폴더 2750: transcript.txt, result.json (0640)
├── jobs/  audio/  work/  queue/     stt:stt 0700 — 내부(audio/ 아래: 기록 <audio_id>.json, 녹음 파일 폴더 <audio_id>/)
└── state.lock  worker.lock  intake.lock  sweep.lock
/run/stt/gpu.lock                    stt:stt 0644 — GPU 잠금(명령줄도 읽기 전용으로 열어 잡는다)
/var/lib/stt-models/                 stt:stt 0750 — 미리 받아 둔 모델(외부 접속 없이 읽는다)
```

- inbox·outbox 에 쓸 수 있는 계정은 `stt-requests` 그룹뿐이다. 호출하는 쪽 서비스 계정을 그 그룹에 넣는 일과 그 단위의 쓰기 경로는
  호스트 설정(nixos-config)이 맡는다 — stt 모듈은 bhu-llm 단위를 건드리지 않는다(geo-scout 선례).
- 이 폴더들은 암호화된 루트(LUKS) 위에 있어야 하고, 어떤 백업·스냅샷 대상에도 넣지 않는다(FR-035·SC-003).

## 3. 맡기는 순서

1. (녹음을 새로 맡길 때) 첨부를 `inbox/<request_id>.audio.<ext>` 로 다 쓴다.
2. 요청을 `inbox/<request_id>.json.tmp` 로 쓴 뒤 `inbox/<request_id>.json` 으로 이름을 바꾼다. `.json` 으로 끝나는 파일만 처리 대상이다.
   - 두 파일 모두 **일반 파일**로, **그룹이 읽을 수 있게**(0640 또는 0660) 만든다. inbox 의 기본 ACL 아래에서는 만들 때의 모드가 stt 의
     권한을 정한다 — `tempfile.mkstemp` 의 0600 그대로면 stt 가 읽지 못하고 거절한다. 심볼릭 링크·하드 링크는 거절한다.
   - 요청 JSON 은 1 MiB 이하.
3. stt 받기 단계가 바로 처리하고, 수 초 안에 `outbox/<request_id>.json` 이 생긴다 — 받아들였으면 `state: queued` 와 `queue_position`,
   거절했으면 §4 의 두 꼴 가운데 하나(`kind: invalid` 의 refusal, 또는 `state: failed` 와 `failure`). delete 요청에는 `kind: delete` 와 `outcome`.
   inbox 의 요청과 첨부는 처리 즉시 사라진다(첨부는 stt 가 복사해 가져간다).
4. 호출하는 쪽은 응답을 다시 읽어 진행을 본다: `queued`(순서) → `running` → `done` | `failed`, 그리고 「지금 지우기」 뒤에는 `deleted`.
5. `done` 이면 `result.transcript`·`result.data` 경로의 파일을 읽고, 복사가 끝나면 「결과 지우기」를 보낸다(§5).

## 4. 받기 단계의 검사 — 스키마 밖의 것

차례대로 본다. 응답의 꼴은 두 가지다 — 요청을 쓸 수 없으면 `refusal`(`kind: invalid`), 스키마는 맞는 transcribe 가 뒤 검사에서 실패하면
`failed` job 상태(`accepted_at` 없음). 응답의 `request_id` 는 언제나 파일 이름이다.

| 검사 | 어기면 |
|---|---|
| 파일 이름이 `<UUID>.json` | 답 없이 지운다(답을 쓸 이름이 없다). 같은 앞부분의 첨부도 지운다 |
| 같은 `request_id` 의 응답이 이미 있음 | 새 요청과 첨부를 지우고, 있던 응답에 `duplicate_refused_at` 만 적는다 |
| 요청 파일이 일반 파일(심볼릭 링크 아님, 링크 수 1), 1 MiB 이하, stt 가 읽을 수 있음, UTF-8 JSON | `refusal` — 내용을 읽거나 옮기지 않는다 |
| 본문이 `request.schema.json` 에 맞음 | `refusal`(detail 에 틀린 칸의 경로와 규칙. 요청 값은 싣지 않는다) |
| 본문의 `request_id` = 파일 이름 | transcribe → `failed(invalid_request)`, delete → `refusal` |
| `audio.file` 의 앞부분 = `request_id`, 첨부가 inbox 에 있고 일반 파일(심볼릭 링크 아님, 링크 수 1)이며 stt 가 읽을 수 있음 | `failed(invalid_request)` |
| `language` 가 엔진의 언어 목록에 있음 | `failed(invalid_request)` |
| 녹음을 열 수 있고 음성 흐름이 있음 | `failed(unreadable_audio)` |
| 녹음 길이 ≤ 3시간 | `failed(audio_too_long)` |
| `from_request`: 같은 요청자의 transcribe 이고 녹음이 남아 있음(삭제 시각이 지났으면 청소 전이라도 없는 것으로 본다) | `failed(source_unavailable)`(없음·남의 것·지워짐을 가르지 않는다) |
| delete 의 `target`: 같은 요청자의 transcribe | 응답 `outcome: refused`(없음·남의 것을 가르지 않는다) |

받기는 본 `*.json` 을 하나도 inbox 에 남기지 않는다. 거절된 요청의 첨부는 그 자리에서 지운다 — 지우는 것은 언제나 **파일 이름 앞부분**의
`<request_id>.audio.*` 뿐이고, 본문의 `audio.file` 이 다른 이름을 가리켜도 그 파일은 건드리지 않는다.

## 5. 지우기

| 요청 | 지우는 것 | 상태에 남는 것 |
|---|---|---|
| `scope: result` (결과 지우기) | 그 job 의 `outbox/<id>/` | `result.deleted_at`. 녹음은 그 job 의 삭제 시기를 따른다. 답: 지웠으면 `deleted`, 이미 지워졌으면(「지금 지우기」로 지운 job 포함) `already_deleted`, 결과가 생긴 적이 없으면(대기·처리 중·실패, 결과 없이 지운 job) `no_result` |
| `scope: everything` (지금 지우기) | 그 job 의 녹음(같은 녹음을 쓰는 다른 job 이 있어도), 처리 중 음성, 힌트 사본, 결과. 처리 중이면 멈춘다 | `state: deleted`, `deleted.at`, `audio.deleted_at`. 답: `deleted`, 이미 지웠으면 `already_deleted`. 같은 녹음을 기다리던 다른 job 은 `failed(audio_deleted)`, `cause: deleted_on_request` |

- 처리 시간은 수 초다(받기 단계가 바로 처리). 처리 중인 엔진은 2초 안에 멈춘다(SC-005: 1분 안).
- 예정된 삭제(끝나면 바로·자정·24시간)는 요청 없이 일어난다. 응답의 `audio.delete_at` 은 그 녹음을 지울 시각이고, 녹음은 그로부터 1분 안에
  사라진다. 24시간 상한의 `delete_at` 은 처음 받은 때 + 23시간 58분이다 — 1분마다 도는 청소가 늦어도 24시간 안에 끝내도록 2분 당겼다
  (data-model 「보관 기한의 여유」). 그 전에 끝나지 않은 job 은 `failed(audio_deleted)` 이고 `cause` 가 원인을 가른다: `midnight`·
  `retention_limit`(보관 상한)·`deleted_on_request`.
- 응답과 결과는 `expires_at`(받은 때 + 23시간 58분)에서 1분 안에 함께 사라진다. 그 뒤에는 그 `request_id` 에 대해 아무것도 남지 않는다.

## 6. 예 — 두 번의 전사

1차(힌트 없음, 화자 구분 없음, 2차를 위해 자정까지 보관):

```json
{"schema_version": 1, "kind": "transcribe",
 "request_id": "0b9a8c2e-5f1d-4c3a-9e7b-2d6f4a1c8e30", "requested_by": "owui-user-42",
 "audio": {"file": "0b9a8c2e-5f1d-4c3a-9e7b-2d6f4a1c8e30.audio.m4a"},
 "language": "ko", "diarize": false, "delete_audio": "at_midnight"}
```

받아들인 직후 응답:

```json
{"schema_version": 1, "kind": "transcribe",
 "request_id": "0b9a8c2e-5f1d-4c3a-9e7b-2d6f4a1c8e30", "state": "queued", "queue_position": 1,
 "accepted_at": "2026-10-22T05:00:02Z", "updated_at": "2026-10-22T05:00:02Z", "expires_at": "2026-10-23T04:58:02Z",
 "audio": {"delete_policy": "at_midnight", "delete_at": "2026-10-22T15:00:00Z"}}
```

2차(1차의 녹음, 언어 모델이 고른 힌트, 화자 구분, 끝나면 바로 삭제):

```json
{"schema_version": 1, "kind": "transcribe",
 "request_id": "6f2e1d0c-9b8a-4e7d-8c6b-5a4f3e2d1c0b", "requested_by": "owui-user-42",
 "audio": {"from_request": "0b9a8c2e-5f1d-4c3a-9e7b-2d6f4a1c8e30"},
 "language": "ko", "diarize": true, "hints": ["Yersinia enterocolitica", "hepatitis B"],
 "delete_audio": "after_job"}
```

결과를 복사한 뒤:

```json
{"schema_version": 1, "kind": "delete",
 "request_id": "c3d2e1f0-a9b8-4c7d-8e6f-5a4b3c2d1e0f", "requested_by": "owui-user-42",
 "target": "6f2e1d0c-9b8a-4e7d-8c6b-5a4f3e2d1c0b", "scope": "result"}
```

## 7. 호출하는 쪽의 의무

- `request_id` 는 요청마다 새 UUID 다. 다시 쓰면 그 요청은 버려지고 `duplicate_refused_at` 만 남는다.
- 요청 파일은 반드시 `.tmp` 로 쓰고 이름을 바꾼다. 첨부는 요청보다 먼저 다 쓴다. 두 파일 모두 그룹이 읽을 수 있는 일반 파일이어야 한다(§3).
- 요청을 맡긴 뒤 5분 안에 `outbox/<request_id>.json` 이 생기지 않으면 실패로 보고 운영자에게 알린다(받기 단위가 멈춘 것이다). 1시간 넘게
  처리되지 못한 inbox 파일은 청소가 지운다.
- 응답 파일이 사라졌으면 그 job 은 만료된 것이다(`expires_at`). 녹음을 가져온 job 은 녹음의 보관 상한과 `expires_at` 이 같아,
  `retention_limit` 실패가 응답과 함께 사라질 수 있다 — 응답이 사라진 것을 만료로 본다.
- 응답·결과를 검사할 때는 이 폴더의 스키마를 쓴다. 모르는 `reason`·`cause` 가 오면 판이 어긋난 것이다. 이용자 문구는 `reason`(과 `cause`)로
  고르고, `detail` 은 운영자 기록용이다.
- 결과를 복사한 뒤에는 「결과 지우기」를 보낸다. 이용자가 「지금 지워줘」 하면 「지금 지우기」를 보낸다.
- 실명은 stt 에 보내지 않는다. 힌트 단어장에 사람 이름이 들어갈 수는 있다 — stt 는 job 이 끝나면 그 사본을 지우고 로그에 남기지 않는다.

## 8. 이 계약에 없는 것

진행률(%) · 완료 알림 · HTTP 통로 · 실명 붙이기(목소리 등록) · 참조 단어장 (1) · 영상 · 회의록·통계. 앞의 둘과 통계는 호출하는 쪽의 일이다.
