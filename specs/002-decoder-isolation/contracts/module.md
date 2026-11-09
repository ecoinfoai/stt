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
| `DynamicUser` | `yes` | 인스턴스마다 일회용 UID. 함께 걸리는 것: `PrivateTmp=disconnected`, `RemoveIPC`, `NoNewPrivileges`, `RestrictSUIDSGID`, `ProtectSystem=strict`, `ProtectHome=read-only` |
| `CollectMode` | `inactive-or-failed` | 실패한 인스턴스가 쌓이지 않게(`systemd.socket(5)` 권고) |
| `PrivateDevices` | `yes` | GPU 장치 없음(FR-008) |
| `PrivateNetwork` · `RestrictAddressFamilies` | `yes` · `none` | 망 없음, 소켓 생성 금지 — 받은 연결은 그대로 쓴다 `[VERIFY]` B-P2-1 |
| `InaccessiblePaths` | `/var/lib/stt` `/var/lib/stt-models` `/run/stt` | 권한 위에 한 겹 더. `gpu.lock`(0644) 붙잡기 막기(R12) |
| `ProtectHome` | `yes` | |
| `PrivateIPC` · `PrivatePIDs` · `ProtectProc` · `ProcSubset` | `yes` · `yes` · `invisible` · `pid` | 다른 프로세스·IPC 가 보이지 않게 `[VERIFY]` B-P2-1 |
| `ProtectKernelTunables` · `ProtectKernelModules` · `ProtectKernelLogs` · `ProtectControlGroups` · `ProtectClock` · `ProtectHostname` | 모두 `yes` | |
| `RestrictNamespaces` · `RestrictRealtime` · `LockPersonality` | 모두 `yes` | |
| `CapabilityBoundingSet` | `""` | |
| `SystemCallArchitectures` | `native` | |
| `SystemCallFilter` | `@system-service`, `~@privileged`, `~process_vm_readv process_vm_writev io_uring_setup io_uring_enter io_uring_register userfaultfd` | FR-008 의 「더 좁은 제한」(R5, 🟢 사용자 관리자로 해독 확인) |
| `MemoryDenyWriteExecute` | `yes` | 🟢 PyAV 해독 확인(R3) |
| `MemoryMax` · `MemorySwapMax` | `768M` · `0` | R9(측정 최대 104 MB 의 약 7배), FR-035 |
| `LimitCORE` · `LimitFSIZE` | `0` · `0` | 덤프 없음, 파일에 자료를 쓰지 못함 |
| `TasksMax` | `32` | |
| `RuntimeMaxSec` | `300` | 감독 상한(최대 240초) 위의 받침 |
| `KillSignal` | `SIGKILL` | 감독은 `PrivatePIDs` 의 PID 1 이라 처리기 없는 SIGTERM 을 무시한다(001 워커와 같은 까닭). 그대로 두면 상한·OOM 정지가 `TimeoutStopSec`(90초)만큼 늦어진다. 정리할 상태가 없다 |

## 3. 받기·워커가 바뀌는 것 (P2)

| 단위 | 바뀜 |
|---|---|
| `stt-intake.service` | `requires`·`after` `stt-reader.socket`. `ExecStart = ${stt} server intake --reader-socket /run/stt-reader.sock` |
| `stt-worker.service` | `requires`·`after` `stt-reader.socket`. `ExecStart = ${stt} server work --gpu-lock /run/stt/gpu.lock --reader-socket /run/stt-reader.sock` |

- P2 뒤 받기는 녹음을 해석하지 않지만 `PrivateDevices`·`MemoryMax=1G` 등 001 설정은 그대로 둔다.
- 워커의 GPU 장치 허용·`MemoryMax=12G` 는 GPU 단계 때문에 그대로다. 워커가 녹음을 해석하지 않게 되는 것은 코드의 바뀜(reader.md §7)이다.
- 배포: `switch-to-configuration` 이 소켓 단위를 띄운다. 워커는 `restartIfChanged=false` 라 도는 전사를 끊지 않고, 다음 path 시작이 새
  `ExecStart` 를 쓴다. 받기는 다음 요청에서 새 설정으로 돈다.

## 4. 확인 명령 (인계 §6 과 회신)

```sh
systemctl show stt-worker.service stt-intake.service stt-sweep.service stt-models.service \
  -p SystemCallFilter -p SystemCallArchitectures -p MemoryDenyWriteExecute -p PrivateDevices -p User -p DynamicUser
# 템플릿은 인스턴스 이름으로 조회한다(띄우지 않는다).
systemctl show stt-reader@check.service \
  -p DynamicUser -p PrivateDevices -p PrivateNetwork -p RestrictAddressFamilies -p InaccessiblePaths \
  -p SystemCallFilter -p SystemCallArchitectures -p MemoryDenyWriteExecute -p MemoryMax -p MemorySwapMax -p LimitFSIZE -p LimitCORE \
  -p RuntimeMaxUSec -p KillSignal -p CollectMode -p PrivateTmp
systemctl show stt-reader.socket -p Listen -p Accept -p SocketUser -p SocketMode -p MaxConnections
```

- 🟢 이 호스트에서 확인한 형식: `SystemCallArchitectures=native` 는 그대로 `native` 로, 필터는 풀린 호출 목록으로 나온다. 필터가 없는 지금의
  워커는 `SystemCallFilter=~`·`SystemCallArchitectures=`·`MemoryDenyWriteExecute=no` 로 나온다. `systemctl show 'x@.service'` 는 오류이고,
  인스턴스 이름은 띄우지 않고 조회된다(2026-11-09).
- 읽기 단위의 `MemorySwapMax`·`LimitCORE`·`KillSignal`·`CollectMode`·`PrivateTmp` 는 FR-012(스왑·덤프 없음)와 §2.2 를 보는 값이다. 출력 꼴은
  VM 에서 처음 본다 `[VERIFY]` B-P2-1.

## 5. 001 의 계약 문서에 고칠 것 (구현 때)

- `specs/001-server-transcription/contracts/module.md` §3: 단위 표에 `stt-reader.socket` → `stt-reader@.service` 줄. 「모든 단위 공통」에 §1 의
  세 설정. 세부는 이 문서를 가리킨다.
- `specs/001-server-transcription/contracts/README.md` §2: 그림에 `/run/stt-reader.sock  root 가 만든 소켓, stt:stt 0600 — 내부`.
- `specs/001-server-transcription/contracts/cli.md` §1: [reader.md](reader.md) §8.
