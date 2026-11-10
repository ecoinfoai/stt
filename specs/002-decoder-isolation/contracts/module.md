# 배치 계약의 바뀜 — `services.stt` (002)

`specs/001-server-transcription/contracts/module.md` 를 바탕으로 이 기능이 더하고 바꾸는 것만 적는다. 선택지(options)와 호스트의 몫은 바뀌지 않는다
— nixos-config 에서 할 일이 없다. 근거는 [research.md](../research.md) R1~R5·R9·R12 다.

## 1. 네 단위에 더하는 것 (P1 — 공통 묶음 `hardening`)

| 설정 | 값 | 단위 | 기대하는 `systemctl show` |
|---|---|---|---|
| `SystemCallArchitectures` | `native` | intake·worker·sweep·models | `SystemCallArchitectures=native` |
| `SystemCallFilter` | `@system-service` (+ R2 관측으로 정한 예외만, `docs/server-security.md` §1) | 같음 | 허용 호출 목록(`~` 로 시작하지 않음) |
| `MemoryDenyWriteExecute` | `true` — 워커는 호스트 판정 결과(R3)에 따라 | 같음 | `yes`(워커가 `no` 이면 §1 에 거부 지점) |

- `SystemCallErrorNumber` 는 두지 않는다 — 어기면 SIGSYS 로 끝난다(R1).
- 이 셋 말고 001 의 설정(`PrivateNetwork`·`PrivateDevices`·장치 정책·상한·`KillSignal`…)은 그대로다.

## 2. 읽기 소켓과 읽기 단위 (P2 — 새것)

### 2.1 `stt-reader.socket`

| 설정 | 값 |
|---|---|
| `ListenSequentialPacket` | `/run/stt-reader.sock` |
| `Accept` | `yes` |
| `SocketUser` · `SocketGroup` · `SocketMode` | `stt` · `stt` · `0600` |
| `MaxConnections` | `4` |
| `wantedBy` | `sockets.target` |

### 2.2 `stt-reader@.service`

| 설정 | 값 | 까닭 |
|---|---|---|
| `ExecStart` | `${stt} server read` | [reader.md](reader.md) §5 |
| `StandardInput` · `StandardOutput` · `StandardError` | `socket` · `null` · `journal` | 연결은 표준 입력으로만. 표준 출력이 연결에 섞이지 않게 |
| `DynamicUser` | `yes` | 읽기마다 일회용 UID(`stt` 가 아닌 동적 범위). 템플릿의 모든 인스턴스가 같은 이름·같은 UID 를 받는다(아래 「UID 에 대하여」). 함께 걸리는 것: `PrivateTmp`, `RemoveIPC`, `NoNewPrivileges`, `RestrictSUIDSGID`, `ProtectSystem=strict`, `ProtectHome=read-only`. 이 systemd(261.1)는 `PrivateTmp` 를 `disconnected` 가 아니라 `yes` 로 보여 준다(VM 에서 확인) |
| `CollectMode` | `inactive-or-failed` | 실패한 인스턴스가 쌓이지 않게(`systemd.socket(5)` 권고). **`[Unit]` 설정이다** — `[Service]` 에 두면 무시된다(`unitConfig`) |
| `PrivateDevices` | `yes` | GPU 장치 없음(FR-008) |
| `PrivateNetwork` · `RestrictAddressFamilies` | `yes` · `none` | 망 없음, 소켓 생성 금지 — 받은 연결은 그대로 쓴다(VM 시험 B-P2-1 이 확인 — 닫힘) |
| `InaccessiblePaths` | `-/var/lib/stt` `-/var/lib/stt-models` `-/run/stt` `-/nix/var` | 권한 위에 한 겹 더. `gpu.lock`(0644) 붙잡기 막기(R12). 모든 항목에 `-` 를 붙인다: `/run`·`/var` 를 빈 tmpfs 로 덮으면 그 아래 경로가 없어도 단위가 실패하지 않아야 한다. `/nix/var` 는 Nix 데이터베이스(0644 파일)를 가린다(감사 H1). `/nix/store` 는 그대로 읽힌다 |
| `ProtectHome` | `yes` | |
| `TemporaryFileSystem` | `/dev/shm:ro` `/run:ro` `/var:ro` | `PrivateDevices=yes` 에도 단위의 `/dev/shm` 은 호스트의 것과 같다(같은 장치·아이노드, 1777). 읽기 단위가 호스트 `/dev/shm` 의 파일을 읽거나 쓰지 못하게 비어 있는 읽기 전용 tmpfs 로 덮는다(VM 시험 B-P2-1 이 확인 — 닫힘). 같은 까닭으로 `/run`·`/var` 도 덮는다: 호스트의 누구나 읽을 수 있는 잠금 파일(`/run/nixos/switch-to-configuration.lock`, `/nix/var/nix/db/…`)에 `flock` 을 걸어 배포를 막는 길을 없앤다(감사 H1, R12). 읽기는 `/run` 아래에서 아무것도 필요로 하지 않는다 — 연결과 로그는 기술자로 받는다. `DynamicUser` 의 사설 `/tmp`·`/var/tmp` 와 함께 되는 것을 VM 에서 확인했다(R12) |
| `PrivateIPC` · `PrivatePIDs` · `ProtectProc` · `ProcSubset` | `yes` · `yes` · `invisible` · `pid` | 다른 프로세스·IPC 가 보이지 않게(VM 시험 B-P2-1 이 확인 — 닫힘) |
| `ProtectKernelTunables` · `ProtectKernelModules` · `ProtectKernelLogs` · `ProtectControlGroups` · `ProtectClock` · `ProtectHostname` | 모두 `yes` | |
| `RestrictNamespaces` · `RestrictRealtime` · `LockPersonality` | 모두 `yes` | |
| `CapabilityBoundingSet` | `""` | |
| `SystemCallArchitectures` | `native` | |
| `SystemCallFilter` | `@system-service`, `~@privileged`, `~@keyring`, `~process_vm_readv process_vm_writev io_uring_setup io_uring_enter io_uring_register userfaultfd` | FR-008 의 「더 좁은 제한」(R5, 🟢 사용자 관리자로 해독 확인). `~@keyring`(`add_key`·`request_key`·`keyctl`): 같은 동적 UID 를 나눠 쓰는 인스턴스끼리 커널 사용자 키링을 통해 자료를 주고받지 못하게 한다(VM 시험 B-P2-1 이 확인 — 닫힘) |
| `MemoryDenyWriteExecute` | `yes` | 🟢 PyAV 해독 확인(R3) |
| `MemoryMax` · `MemorySwapMax` | `768M` · `0` | R9(측정 최대 104 MB 의 약 7배), FR-035 |
| `LimitCORE` · `LimitFSIZE` | `0` · `0` | 덤프 없음, 파일에 자료를 쓰지 못함 |
| `TasksMax` | `32` | |
| `RuntimeMaxSec` | `300` | 감독 상한(최대 240초) 위의 받침 |
| `restartIfChanged` | `false` | 배포(`switch-to-configuration`)가 도는 읽기를 건드리지 않게. 템플릿이 바뀌었을 때 인스턴스가 켜져 있으면 스위치는 템플릿 이름에서 `@` 를 떼어 소켓을 멈췄다 켜는데(`stt-reader.socket`), 이 설정(`X-RestartIfChanged=false`)이 있으면 그 인스턴스는 건너뛴다. 읽기 하나는 `RuntimeMaxSec` 으로 끝나므로 다음 읽기부터 새 템플릿을 쓴다 |
| `KillSignal` | `SIGKILL` | 감독은 `PrivatePIDs` 의 PID 1 이라 처리기 없는 SIGTERM 을 무시한다(001 워커와 같은 까닭). 그대로 두면 상한·OOM 정지가 `TimeoutStopSec`(90초)만큼 늦어진다. 정리할 상태가 없다 |

**UID 에 대하여** (2026-11-09 실측, systemd 261.1, 소스 `src/core/socket.c`·`dynamic-user.c`·`user-util.c`).
- 템플릿에 `DynamicUser=yes` 만 두면 동시에 도는 모든 인스턴스가 **같은 동적 UID 하나**를 쓴다. 사용자 이름이 인스턴스 이름이 아니라 템플릿 접두사
  (`stt-reader`)에서 오기 때문이다. 이름을 해시하는 것은 접두사가 사용자 이름이 될 수 없을 때뿐이다(research R4 의 옛 서술은 틀렸다).
- 인스턴스마다 `User=<접두사>%i` 로 UID 를 가르는 길은 쓸 수 없다. `Accept=yes` AF_UNIX 인스턴스 이름은
  `<연결 번호>-<소켓 쿠키>-<상대 PID>_<pidfd 번호>-<상대 UID>`(예: `19-14-3512_3513-997`)이고, 동적 사용자 이름은 엄격 검사(최대 31자)를 받는다.
  최악은 71자(번호 10 + 쿠키 20 + PID 7 + pidfd 20 + UID 10 + 구분자 4)이고, 접두사를 `r-` 한 겹으로 줄여도 PID·UID·구분자만 14자라 나머지 세 숫자에
  자리당 5자리가 남는다. 부팅 직후 짧은 이름은 통과하지만(31자 통과), 32자부터는 단위는 읽히되 시작이 `Failed to spawn 'start' task: Invalid argument`
  로 거부된다. 공유 UID 로 물러서지는 않는다(안전하게 닫힘). 그러나 오래 도는 호스트에서는 모든 읽기가 멎는다.
- 그래서 겹쳐 도는 두 읽기를 가르는 것은 UID 가 아니라 인스턴스마다 따로 만들어지는 이름공간이다: `PrivatePIDs`(다른 프로세스가 `/proc` 에 보이지
  않음), `ProtectProc=invisible`·`ProcSubset=pid`, `PrivateIPC`, `PrivateTmp`, `PrivateDevices`, `PrivateNetwork`, `RestrictAddressFamilies=none`,
  그리고 필터 밖인 `ptrace`(`@system-service` 에 들지 않음)와 명시적으로 뺀 `process_vm_readv`·`process_vm_writev`, `~@privileged`. 서로의 프로세스를 보지도 신호를 보내지도 못한다. 시험(B-P2-4)이 보는 것은 UID 가
  `stt` 가 아니라는 것과, 각 읽기가 `/proc` 에서 자기 인스턴스의 프로세스만 본다는 것이다.
- 운영자 결정(2026-11-09): 공유 UID 와 이름공간 격리를 그대로 쓴다. 길이 재기용·해독용 템플릿을 둘로 나눠 UID 를 가르는 안은 쓰지 않는다.
- 같은 동적 UID 를 쓰는 인스턴스끼리는 커널 사용자 키링을 나눈다(`@keyring` 호출은 `@system-service` 안에 있다). 그래서 필터에 `~@keyring` 을
  더했다(최종 검토, 2026-11-09): 키링 호출은 `SIGSYS` 로 막힌다. `/dev/shm` 도 같은 까닭으로 `TemporaryFileSystem=/dev/shm:ro` 로 덮는다.

## 3. 받기·워커가 바뀌는 것 (P2)

| 단위 | 바뀜 |
|---|---|
| `stt-intake.service` | `wants`·`after` `stt-reader.socket`. `ExecStart = ${stt} server intake --reader-socket /run/stt-reader.sock` |
| `stt-worker.service` | `wants`·`after` `stt-reader.socket`. `ExecStart = ${stt} server work --gpu-lock /run/stt/gpu.lock --reader-socket /run/stt-reader.sock` |

- `requires` 가 아니라 `wants` 인 까닭(통제자 판정 16, 2026-11-09): `Requires=` 는 소켓이 멈추면 받기·워커도 멈춘다. 그러면 소켓 단위를
  손볼 때 도는 전사가 `interrupted` 로 끝난다. `Wants=` 는 소켓을 함께 띄우되 멈춤을 전하지 않는다. 소켓이 없으면 읽기는 접속 실패로 끝나고
  받기·워커는 대체 길 없이 그 요청·작업만 실패로 끝낸다(헌법 V, FR-016). 순서는 `After=` 가 지킨다.

- P2 뒤 받기는 녹음을 해석하지 않지만 `PrivateDevices`·`MemoryMax=1G` 등 001 설정은 그대로 둔다.
- 워커의 GPU 장치 허용·`MemoryMax=12G` 는 GPU 단계 때문에 그대로다. 워커가 녹음을 해석하지 않게 되는 것은 코드의 바뀜(reader.md §7)이다.
- 배포(`switch-to-configuration`, nixpkgs 의 `switch-to-configuration-ng` 소스를 읽어 확인, 2026-11-09):
  - 새 `stt-reader.socket` 은 처음 배포 때 띄워진다. 이미 도는 소켓 단위의 설정이 바뀌면 스위치는 그것을 다시 띄우지 않는다(소스의 빈 `FIXME`,
    nixpkgs#74899). 소켓 설정(경로·모드·`MaxConnections` 등)을 바꾼 배포는 운영자가 `systemctl restart stt-reader.socket` 을 따로 한다. 그동안
    들어온 요청은 실패지도대로 끝난다(워커 `engine_error`, 받기는 001 FR-032a 대로 응답 없이 치움).
  - `stt-reader@.service` 템플릿이나 패키지가 바뀌면 도는 읽기는 끝까지 마치고(`restartIfChanged=false`), 받기·워커는 멈추지 않는다. 다음 읽기부터
    새 템플릿이다. 워커는 `restartIfChanged=false` 라 도는 전사를 끊지 않고, 다음 path 시작이 새 `ExecStart` 를 쓴다. 받기는 다음 요청에서 새 설정으로 돈다.
  - VM 시험이 읽기를 붙든 채 템플릿이 다른 구성으로 바꿔 이 동작(같은 워커 PID, 소켓 그대로, 작업이 `interrupted` 가 아님)을 확인한다.

## 4. 확인 명령 (인계 §6 과 회신)

```sh
systemctl show stt-worker.service stt-intake.service stt-sweep.service stt-models.service \
  -p SystemCallFilter -p SystemCallArchitectures -p MemoryDenyWriteExecute -p PrivateDevices -p User -p DynamicUser
# 템플릿은 인스턴스 이름으로 조회한다(띄우지 않는다).
systemctl show stt-reader@check.service \
  -p DynamicUser -p PrivateDevices -p PrivateNetwork -p RestrictAddressFamilies -p InaccessiblePaths \
  -p SystemCallFilter -p SystemCallArchitectures -p MemoryDenyWriteExecute -p MemoryMax -p MemorySwapMax -p LimitFSIZE -p LimitCORE \
  -p RuntimeMaxUSec -p KillSignal -p CollectMode -p PrivateTmp -p TemporaryFileSystem
systemctl show stt-reader.socket -p Listen -p Accept -p SocketUser -p SocketMode -p MaxConnections
```

- 🟢 이 호스트에서 확인한 형식: `SystemCallArchitectures=native` 는 그대로 `native` 로, 필터는 풀린 호출 목록으로 나온다. 필터가 없는 지금의
  워커는 `SystemCallFilter=~`·`SystemCallArchitectures=`·`MemoryDenyWriteExecute=no` 로 나온다. `systemctl show 'x@.service'` 는 오류이고,
  인스턴스 이름은 띄우지 않고 조회된다(2026-11-09).
- 읽기 단위의 `MemorySwapMax`·`LimitCORE`·`KillSignal`·`CollectMode`·`PrivateTmp` 는 FR-012(스왑·덤프 없음)와 §2.2 를 보는 값이다. VM 에서 본 꼴:
  `RestrictAddressFamilies=` 는 빈 값, `PrivateTmp=yes`, `CollectMode` 는 `[Unit]` 에서 `inactive-or-failed`. B-P2-1 은 VM 시험이 닫았다.

## 5. 001 의 계약 문서에 고칠 것 (구현 때)

- `specs/001-server-transcription/contracts/module.md` §3: 단위 표에 `stt-reader.socket` → `stt-reader@.service` 줄. 「모든 단위 공통」에 §1 의
  세 설정. 세부는 이 문서를 가리킨다.
- `specs/001-server-transcription/contracts/README.md` §2: 그림에 `/run/stt-reader.sock  root 가 만든 소켓, stt:stt 0600 — 내부`.
- `specs/001-server-transcription/contracts/cli.md` §1: [reader.md](reader.md) §8.
