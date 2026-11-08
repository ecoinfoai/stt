# stt 인계 — 해독 단위 보안 강화 요청 (2026-11-08, bhu-llm → stt)

받는 곳: stt 저장소(`~/localgit/stt`)를 맡은 세션. bhu-llm 쪽은 stt 저장소를 읽기만 하므로, 고칠 것은 이 문서로 넘긴다.
보낸 곳: bhu-llm 029(녹음 전사 보완, v0.2.1) 의 최종 보안 점검(T029 기록, T039 감사, T040 최종 리뷰).
bhu-llm 쪽 상태는 대장 `docs/ops/open-items.md` A18 이 원천이다.

## 1. 왜 지금인가

- bhu-llm 의 업로드 화면은 녹음 파일의 **이름·크기·확장자만** 검사하고, 내용은 해독하지 않는다. 해독은 stt 의 몫이다.
  그래서 녹음 확장자를 붙인 조작 파일은 **그대로 stt 의 해독기(ffmpeg 류)까지 간다.** 이 경로는 028 부터 같다.
- 029 부터 바뀐 것 두 가지가 노출을 키운다.
  - 녹음을 결과를 넘긴 뒤에도 「지워줘」 또는 그날 23:58 까지 둔다(재전사를 위해). 그날 올라온 다른 이용자의 녹음이 더 오래 같은 곳에 있다.
  - 같은 녹음을 다시 전사할 수 있어(최대 3번, 자동 2차 포함 차수 1~5), 한 업로드가 **최대 5번** 해독된다.
- 운영자 지시(2026-11-07): 「별도로 열리는 파일 업로드 페이지의 보안성 점검이 시급하다 — AI 를 쓴 해킹 사례가 많다」.

## 2. 측정한 사실 (읽기 전용 `systemctl show` 만, 2026-11-08)

전체 표: bhu-llm `specs/029-stt-retranscribe/handoff/stt-decoder-isolation.md`. 요점:

| 항목 | stt-worker | stt-intake |
|---|---|---|
| 사용자 | `stt`(고정, `DynamicUser=no`) | `stt`(같은 사용자) |
| 망 | `PrivateNetwork=yes`, `AF_UNIX` 만 | 같음 |
| 능력·권한 상승 | 능력 0, `NoNewPrivileges`, `RestrictSUIDSGID` | 같음 |
| 장치 | `PrivateDevices=no`, `DevicePolicy=closed`, NVIDIA 넷 `rw` | `PrivateDevices=yes` |
| **시스템 호출 필터** | **없음**(`SystemCallFilter=~` 맨 꼴 — 필터 없는 단위의 모양) | **없음** |
| `SystemCallArchitectures` | 비어 있음 | 비어 있음 |
| `MemoryDenyWriteExecute` | `no` | `no` |
| 상태 디렉터리 | `StateDirectory=stt`(모든 작업 공유) | 같음 |
| 메모리 | 12 GiB, 스왑 0 | 1 GiB, 스왑 0 |

이미 잘 되어 있는 것: 망이 없어서 해독기가 뚫려도 **녹음을 밖으로 빼낼 길이 없다.** 능력 0·`ProtectSystem=strict`·`ProtectHome`·이름공간 금지도 있다.

## 3. 남는 위험

1. 해독기 취약점을 노린 조작 파일이 해독 프로세스를 장악하면, 그 프로세스는 `stt` 권한으로 같은 상태 디렉터리의 **그날 다른 이용자의 녹음·전사록**을 읽거나 바꿀 수 있다.
2. 시스템 호출 필터가 없어 커널 취약점으로 격리 밖으로 나갈 길이 넓다(`ProtectKernelModules`·`ProtectKernelLogs`·`RestrictNamespaces`·`LockPersonality` 가 막는 몇 가지만 빼고).
3. 해독이 GPU 장치가 열린 단위(stt-worker)에서 돈다면, 드라이버 취약점으로도 이어질 수 있다. (어느 단계가 어느 단위에서 도는지는 bhu-llm 이 확인하지 못했다.)

## 4. 요청 (우선순위 순)

1. **시스템 호출 필터** — 가장 효과가 크고 바로 할 수 있다. 두 단위에 다음을 더한다.
   ```
   SystemCallFilter=@system-service
   SystemCallArchitectures=native
   ```
   CUDA 때문에 stt-worker 에 예외가 필요하면 그 묶음만 더하고(`SystemCallLog=` 로 먼저 관측 가능) 이유를 적어 달라.
   `MemoryDenyWriteExecute=yes` 는 stt-intake 에서는 켤 수 있는지, stt-worker 에서 CUDA 때문에 불가피한지 알려 달라.
2. **해독 단계를 GPU 단위에서 떼기** — ffmpeg 로 오디오를 PCM/wav 로 바꾸는 단계를 GPU 가 닫힌 단위(`PrivateDevices=yes`)에서 하고,
   GPU 단위는 이미 해독된 데이터만 받게 한다. 가능하면 해독은 작업마다 새로 뜨는 일회용 프로세스(`DynamicUser` 또는 작업별 사용자)로.
   이미 이렇게 나뉘어 있다면 어느 단계가 어느 단위에서 도는지 알려 달라.
3. **작업별 저장 분리** — 장악된 프로세스가 다른 작업의 녹음·전사록을 읽지 못하게, 작업(요청)마다 디렉터리 권한이나 사용자를 나눈다.
4. **해독기 갱신 정책** — ffmpeg·faster-whisper·Python 의존성을 누가, 얼마 주기로, 무엇(보안 공지·nixpkgs 갱신)을 보고 올리는지, 마지막 갱신 날짜.

시범 단계(시험 계정만)인 지금은 1번을 먼저, 전체 이용자에게 열기 전(bhu-llm FR-659)에는 2·3번이 갖춰지기를 바란다.

## 5. 지켜 줄 것 — stt 계약

- bhu-llm 은 stt 계약 `ce5c325` 에 맞춰져 있다(§3 요청 쓰기, §5 지우기 — 한 번의 지우기가 그 녹음을 쓴 모든 요청을 지움, §6 `audio.from_request` 재전사, 23:58 정리).
  위 강화로 **요청·결과 파일의 모양, 경로, 지우기 의미, `from_request` 동작**이 바뀌면 bhu-llm 이 깨진다. 바뀌어야 한다면 먼저 알려 달라.
- 조작 파일은 지금처럼 작업 실패(`unreadable_audio`)로 끝나고, 다른 작업·단위는 영향을 받지 않아야 한다.
- 단위가 비정상 종료하거나 OOM 으로 죽을 때 운영자가 알 수 있는 경로가 있는지 알려 달라(작업 실패와 구별).

## 6. 확인하는 법 (stt 쪽에서 바꾼 뒤)

- `systemctl show stt-worker.service stt-intake.service -p SystemCallFilter -p SystemCallArchitectures -p MemoryDenyWriteExecute -p PrivateDevices -p User -p DynamicUser`
  — 필터 항목이 보이고(맨 `~` 가 아님) `SystemCallArchitectures=native`.
- 정상 녹음 하나, 녹음 확장자를 붙인 조작 파일 하나(예: 텍스트 파일을 `.m4a` 로)를 맡겨, 정상은 전사되고 조작 파일은 `unreadable_audio` 로 끝나며 단위가 죽지 않는다.
- 재전사(`audio.from_request`)가 3번까지 그대로 된다.

## 7. 회신

답은 운영자를 거쳐 bhu-llm 대장 A18 에 적는다. 답이 오기 전까지 bhu-llm 은 위 §3 을 「남는 위험」으로 대장에 두고,
운영자가 시범 단계에서 받아들일지 정한다(bhu-llm SC-733 은 그 수락 뒤에만 충족으로 센다).

bhu-llm 쪽에서 할 수 있는 보조책(아직 하지 않음): 업로드 직후 파일 머리(매직 바이트)로 실제 형식을 확인해 확장자와 다르면 바로 거절. 해독 없이 걸러내는 일부 방어일 뿐이라 위 요청을 대신하지 못한다.
