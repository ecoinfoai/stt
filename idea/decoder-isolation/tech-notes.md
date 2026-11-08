# 녹음 해독 격리 — 기술 메모 (idea 단계)

- 작성: 2026-11-08 · 짝 문서: [`idea.md`](idea.md)(무엇·왜) · 이 문서는 `/speckit-plan` 의 입력이다.
- 표기: 🟢 확인한 사실 · 🟡 결정(운영자 승인 2026-11-08) · `[VERIFY]` plan 단계에서 먼저 확인할 미검증 사실
- `path:line` 은 stt master `bb704cd` 기준이다. 다른 저장소는 `bhu-llm:` 을 앞에 붙인다. 「인계」 = `docs/handoff-stt-decoder-security-20261108.md`.

## 0. 결정 요약

| 항목 | 결정 | 절 |
|---|---|---|
| 범위 | 한 feature — P1(시스템 호출 제한) → P2(녹음 읽기 격리) → P3(갱신 정책) 단계 배포 🟡 | idea |
| 격리 대상 | 녹음 내용을 읽는 세 곳 모두, 받기의 길이 재기 포함 🟡 | §2, §4 |
| 실패 이유 | 해독 단계의 실패·비정상 종료·그 단계 상한 초과 → `unreadable_audio` 🟡 | §5 |
| 계약 | 요청·응답·결과 스키마와 inbox·outbox 경로는 그대로. 내부 폴더(`jobs`·`audio`·`work`·`queue`)는 계약 밖 🟢 | §4 |

## 1. 지금 단위 설정 🟢 (2026-11-08, 읽기 전용 `systemctl show`)

- 전체 표는 `bhu-llm:specs/029-stt-retranscribe/handoff/stt-decoder-isolation.md` §1. 이 세션에서 다시 본 값(systemd 262):
  두 단위 모두 `SystemCallFilter=~`(필터 없음), `SystemCallArchitectures=`(빈 값), `OOMPolicy=stop`, `OnFailure=`(빈 값), `NRestarts=0`,
  `ProcSubset=all`, `PrivateIPC=no`, `RestrictRealtime=no`.
- 정의: 공통 묶음 `nix/module.nix:35-66`(hardening·offline·state), 받기 `:137-142`, 워커 `:144-173`, 청소 `:175-180`, 모델 받기 `:185-206`.
- 있는 것: `PrivateNetwork` + `RestrictAddressFamilies=AF_UNIX`, 능력 0, `NoNewPrivileges`, `RestrictSUIDSGID`, `ProtectSystem=strict`, `ProtectHome`,
  `PrivateTmp`, `ProtectKernelTunables`·`Modules`·`Logs`, `ProtectControlGroups`, `RestrictNamespaces`, `LockPersonality`, `PrivatePIDs`,
  `MemorySwapMax=0`, `LimitCORE=0`. 워커는 `DevicePolicy=closed` + NVIDIA 넷(`nvidiactl`·`nvidia0`·`nvidia-uvm`·`nvidia-uvm-tools`), 받기·청소는 `PrivateDevices=true`.
- 없는 것 가운데 인계가 요청한 것: `SystemCallFilter`, `SystemCallArchitectures`, `MemoryDenyWriteExecute`. bhu-llm 표에 비어 있는 나머지
  (`PrivateUsers`·`ProtectProc`·`ProtectClock`·`ProtectHostname`·`RestrictRealtime`·`ProcSubset`·`PrivateIPC`)는 인계 요청 밖이다 — plan 에서 더하려면
  이유를 적고 운영자 확인을 받는다.

## 2. 녹음 내용을 읽는 곳 🟢 (인계 §4-2 「어느 단계가 어느 단위에서」의 답)

| # | 하는 일 | 코드 | 해독기 | 단위·프로세스 | GPU 장치 |
|---|---|---|---|---|---|
| 1 | 길이 재기 — 형식 허용목록(`mov,mp3,wav,flac,ogg,aac,asf`)으로 열어 컨테이너 길이, 없으면 패킷 길이의 합(해독 없음) | `src/stt/server/intake.py:79-113`, 호출 `:245` | PyAV 안의 FFmpeg | stt-intake 본 프로세스 | 닫힘 |
| 2 | 전사용 전체 해독(16 kHz 모노) | `src/stt/transcribe.py:776` 이 경로 문자열을 넘김 → faster-whisper `decode_audio`(`av.open`) | PyAV 안의 FFmpeg | stt-worker 가 띄운 엔진 하위 프로세스(`src/stt/server/worker.py:92`) — CTranslate2 의 CUDA 와 같은 프로세스 | 열림 |
| 3 | 화자 구분용 WAV 변환(16 kHz 모노 16-bit) | `src/stt/diarize.py:688-740`, 그 WAV 를 `read_wav` `:744`(파이썬 `wave`)가 읽음 | nixpkgs `ffmpeg` 명령 | 엔진이 띄운 `ffmpeg` 하위 프로세스(같은 단위) | 열림 |

- 셋 다 `stt` 사용자이고 `StateDirectory=stt` 로 `/var/lib/stt` 전체를 쓸 수 있다 — `requests/inbox`·`outbox`, `jobs`·`audio`·`work`·`queue` 의 모든 작업 자료.
- 엔진의 다른 입력: `spec.json`(워커가 씀), 힌트(받기가 스키마로 검사함), 모델 폴더(읽기만).
- faster-whisper 는 해독된 배열을 받으면 해독하지 않는다: `if not isinstance(audio, np.ndarray): audio = decode_audio(...)`
  (설치본 `faster_whisper/transcribe.py:386-387`, `:875-876`). P2 에서 GPU 단계가 해독된 음성만 받을 수 있는 근거다.

## 3. P1 — 시스템 호출 제한

- 인계 요청: 두 단위에 `SystemCallFilter=@system-service`, `SystemCallArchitectures=native`. 워커에 CUDA 때문에 예외가 필요하면 그 묶음만 더하고
  이유를 적는다(먼저 `SystemCallLog=` 로 관측). `MemoryDenyWriteExecute=yes` 를 받기에서 켤 수 있는지, 워커에서 CUDA 때문에 불가피한지 답한다.
- `[VERIFY]` 워커 쪽 프로세스(엔진: CTranslate2 4.8.1 의 CUDA 12 휠, torch 2.13.0 의 CUDA 13 휠, NVIDIA 595.112.07 사용자 공간; `ffmpeg` 하위 프로세스)가
  `@system-service` 밖의 호출을 쓰는지 — 호스트에서 관측 설정으로 1차·2차(화자 구분) 전사를 한 번씩 돌린 뒤 정한다. 관측 기록이 journal 에 남는 조건도 함께 확인한다.
- `[VERIFY]` `MemoryDenyWriteExecute=yes` — 받기(PyAV 만)와 워커(CUDA 문맥 생성·CTranslate2·torch·pyannote)에서 각각 켠 채로 시험한다.
  워커에서 안 되면 거부된 지점을 근거로 적는다.
- VM 시험(GPU 없음)은 설정 값과 GPU 없는 경로(받기·청소·`gpu_unavailable`)를 본다. CUDA 경로는 호스트 시험에서만 확인된다.
- 확인(인계 §6): `systemctl show stt-worker.service stt-intake.service -p SystemCallFilter -p SystemCallArchitectures -p MemoryDenyWriteExecute`
  — 필터 항목이 보이고(맨 `~` 가 아님) `SystemCallArchitectures=native`.

## 4. P2 — 녹음 읽기 격리

- 인계가 든 수단: 해독을 GPU 가 닫힌 단위(`PrivateDevices=yes`)에서, 가능하면 작업마다 새로 뜨는 일회용 프로세스(`DynamicUser` 또는 작업별 사용자)로.
  작업별로 디렉터리 권한이나 사용자를 나눈다.
- 일회용 프로세스가 갖출 것(idea 에서): 자기 작업의 녹음만 읽고 자기 출력만 쓴다 · GPU 장치 없음 · 망 없음 · 시스템 호출 제한 · 메모리·시간 상한이
  그 프로세스에만 걸린다 · 끝나면 남는 것이 없다. 상한을 따로 거는 까닭: 지금은 `OOMPolicy=stop` 이라 워커 단위 안의 한 프로세스가 메모리 상한으로 죽으면
  단위 전체가 멈춘다 `[VERIFY]`(§5).
- plan 에서 고를 것:
  - 일회용 프로세스를 누가 띄우는가. 지금 단위들은 능력 0·`NoNewPrivileges` 라 스스로 다른 사용자가 되거나 새 단위를 띄울 수 없다 — systemd 가 작업마다
    띄우게 하는 방식(예: `Accept=yes` 소켓의 템플릿 단위 + `DynamicUser`)과 그 권한 경로를 정한다 `[VERIFY]`.
  - 해독된 음성을 녹음마다 한 번 만들어 재전사에 다시 쓸지, 작업마다 새로 만들지. 앞의 것은 보관 자료가 늘고(3시간 녹음 16 kHz 모노 16-bit 약 346 MB, 23:58 까지),
    뒤의 것은 한 업로드가 최대 5번 해독된다.
  - 전사(지금 PyAV 의 float32 리샘플)와 화자 구분(지금 `ffmpeg` 의 16-bit WAV)이 서로 다른 해독기·형식을 쓴다. 한 번의 해독으로 합치면 결과가
    달라질 수 있다 → idea SC-7(기준 녹음 비교).
  - 받기의 길이 재기는 접수 확인을 수 초 안에 내야 한다(001 SC-011) — 일회용 프로세스의 시작 비용 `[VERIFY]`.
- 계약 경계 🟢: `specs/001-server-transcription/contracts/README.md` §2 에서 호출하는 쪽이 쓰는 것은 `requests/inbox`·`outbox` 이고,
  `jobs`·`audio`·`work`·`queue` 는 「내부」다. 내부 배치는 계약을 깨지 않고 바꿀 수 있다(README §2 그림은 고친다). 단위를 더하면 `contracts/module.md` §3 을 고친다.
  호스트 설정(nixos-config)도 바꿔야 하는지 `[VERIFY]`.

## 5. 실패 이유와 운영자 관측

- 지금 🟢(코드 읽기):
  - 받기: 길이 재기 실패 → `unreadable_audio`(받기에서 거절), 3시간 초과 → `audio_too_long`.
  - 엔진: PyAV 오류(`av.error.FFmpegError`) → `unreadable_audio`(`src/stt/server/engine.py:54-76`). `ffmpeg` 변환 실패는 `RuntimeError`
    (`src/stt/diarize.py:736-740`) → `engine_error`. 엔진이 판정 없이 끝나면 `engine_error`(`src/stt/server/worker.py:37`, `_verdict` `:125`).
  - 엔진이 워커 단위의 메모리 상한(12 GiB)에 걸리면 `OOMPolicy=stop` 으로 워커 단위가 멈추고, 그 작업은 다음 워커 시작이나 청소에서 `interrupted` 가 된다
    `[VERIFY]`(systemd 문서와 `nix/module.nix:161-164` 주석에 근거, 시험 안 함).
- 결정 🟡: 해독 단계(길이 재기·해독·변환)의 실패·비정상 종료·그 단계에 거는 상한 초과는 모두 `unreadable_audio`. GPU 단계의 분류(`gpu_out_of_memory`·
  `engine_error` 등)는 그대로다. 작업 전체의 처리 시간 상한(`processing_timeout`)과 해독 단계 상한의 관계는 spec 에서 정한다.
- 응답 스키마의 실패 이유 목록(`failure.reason`, 11개)은 그대로다 — `unreadable_audio` 는 이미 있다.
- 운영자 관측 🟢: `OnFailure=` 가 비어 있어 단위 비정상 종료를 알리는 길이 없다. 지금 볼 수 있는 곳은 `systemctl --failed` 와 journal 의
  `<request_id> <state>` 줄뿐이다. 새 길을 만들지는 idea Open Questions.

## 6. P3 — 녹음을 여는 구성 요소와 갱신 🟢

| 구성 요소 | 지금 판 | 고정하는 곳 | 그 판이 들어온 날 |
|---|---|---|---|
| PyAV 휠 + 안에 든 FFmpeg 라이브러리(libavcodec 62.28.102, libavformat 62.12.102) — §2 의 1·2번 | av 18.0.0(Python 3.12, 서버) · 17.1.0(Python 3.10) | `uv.lock` | 2026-07-31 `ad3d695` |
| `ffmpeg` 명령 — §2 의 3번 | ffmpeg 8.1.2 | `flake.lock` 의 nixpkgs `148bab9c1c`(2026-08-01 판) | 2026-08-02 `13e2b00` |
| faster-whisper · CTranslate2 | 1.2.1 · 4.8.1 | `uv.lock` | 2026-07-31 `ad3d695` |
| torch · pyannote.audio | 2.13.0 · 4.0.7 | `uv.lock` | 2026-08-03 `47d6d56` |

- 배포된 패키지에서 확인했다: `stt-server-env` 의 `av.libs` → `av-18.0.0`, `stt` 래퍼의 PATH → `ffmpeg-8.1.2-bin`.
- `uv.lock`·`flake.lock` 파일은 그 뒤(2026-10-22)에도 바뀌었지만 위 판은 그대로였다.
- 두 해독기는 갱신 경로가 다르다: PyAV 안의 FFmpeg 는 `uv.lock` 의 av 를 올려야, `ffmpeg` 명령은 nixpkgs 를 올려야 바뀐다. 한쪽만 올리면 다른 쪽은 그대로다.
- 저장소에 갱신 정책 문서는 없다(README·INSTALL·specs 검색).
- 갱신할 때 걸리는 것: 001 research R1 의 GPU 휠 조합(torch CUDA 13 + CTranslate2 CUDA 12), Python 3.10 의 onnxruntime 상한(`eea5374`), 고정 모델 목록(R9),
  배포 뒤 기준 녹음 비교(idea SC-7).
- 정책 🟡(2026-11-08, spec FR-025): 운영자가 매달 확인하고 새 판이 있을 때만 올린다. 확인할 곳은 FFmpeg 보안 공지, PyAV 릴리스, nixpkgs 의 ffmpeg 갱신이다.
  보안 공지가 나오면 7일 안에 올린다.
- plan 에서 볼 것: nixpkgs 를 올리면 ffmpeg 만이 아니라 서버 환경 전체가 바뀐다. 매달의 갱신은 해독기 둘(`uv.lock` 의 av, ffmpeg 하나)로 한정하고,
  nixpkgs 전체는 보안 공지가 있을 때 올리는 방식이 되는지(ffmpeg 하나만 다른 nixpkgs 에서 가져오기 등) `[VERIFY]`.

## 7. 확인 방법

| 무엇 | 어디서 | idea |
|---|---|---|
| 설정 값(인계 §6 의 `systemctl show`) | 호스트, VM 시험 | SC-1 |
| 권한 점검 — 해독 단계의 신원으로 다른 작업의 경로와 GPU 장치에 접근이 거부되는지 | VM 시험(파일), 호스트(GPU 장치) | SC-2·SC-3 |
| 정상 녹음 하나 + 녹음 확장자를 붙인 텍스트 파일 하나(인계 §6) | 호스트 | SC-4 |
| 해독 단계의 실패·비정상 종료·상한 초과 — 시험용 대역(가짜 해독기) | pytest, VM 시험 | SC-5 |
| 한 녹음에 작업 5개, 「다 지우기」 | pytest, 호스트 | SC-6 |
| 기존 시험 전부, 기준 녹음 비교, 1시간 처리 시간 | CI, 호스트 | SC-7 |
| 새 중간 자료의 남은 파일 0(정상·실패·강제 종료·「다 지우기」·23:58) | pytest, VM 시험 | SC-8 |

- 취약점을 노리는 실제 파일은 만들거나 보관하지 않는다. project-audit 의 adversary 도 읽기만 한다
  (001 T078 — `specs/001-server-transcription/handoff/2026-10-31.md` 「최종 감사」).

## 8. 인계 대응

| 인계 | 이 문서 |
|---|---|
| §4-1 시스템 호출 제한, `MemoryDenyWriteExecute` 판단 | idea P1, §3 |
| §4-2 해독을 GPU 단위에서 떼기 · 「어느 단계가 어느 단위에서」 | idea P2, §2(답), §4 |
| §4-3 작업별 저장 분리 | idea P2, §4 |
| §4-4 갱신 정책 · 마지막 갱신 날짜 | idea P3, §6(날짜는 이미 답) |
| §5 계약 불변 | idea Constraints, §4 계약 경계 |
| §5 조작 파일 → `unreadable_audio`, 다른 작업·단위 무사 | §5, idea SC-4·SC-5 |
| §5 비정상 종료·OOM 을 운영자가 아는 길 | §5(지금은 없음), idea Open Questions |
| §6 확인 | §7 |
| §7 회신 | idea SC-10 |
| bhu-llm 측정 문서 §4-6 보관 기간 확대의 영향 | idea Discussion Notes(001 R22 부터 가정한 보관) |
