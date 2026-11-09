# 서버 보안 기록 — 단위별 제한, 녹음 읽기 격리, 판 기록

이 문서는 `002-decoder-isolation` 기능이 요구하는 운영 기록이다. `tests/test_server_security_doc.py` 가 표식 주석(`<!-- stt:… -->`),
영어 칸 이름, 영어 상태값, ISO 날짜만 읽고 저장소의 값과 대조한다. 이유 같은 자유 문장은 한국어로 쓴다.

## 1. 단위별 제한과 예외

<!-- stt:syscall-table -->

| unit | filter | added | narrowed | reason | observed | mdwe | mdwe_evidence |
|---|---|---|---|---|---|---|---|
| `stt-intake` | `@system-service` | - | - | - | - | on | - |
| `stt-worker` | `@system-service` | - | - | - | - | pending | - |
| `stt-sweep` | `@system-service` | - | - | - | - | on | - |
| `stt-models` | `@system-service` | - | - | - | - | on | - |
| `stt-reader@` | `@system-service` | - | @privileged, @keyring, process_vm_readv, process_vm_writev, io_uring_setup, io_uring_enter, io_uring_register, userfaultfd | 녹음 해독기를 노리는 입력이 읽기 프로세스를 장악해도 다른 프로세스의 메모리를 읽거나 쓰지 못하고, 권한 호출과 커널 비동기 입출력 길을 쓰지 못하게 한다. 해독은 이 호출 없이 된다(사용자 관리자에서 확인, 2026-11-09) | - | on | - |

- 네 단위는 `nix/module.nix` 의 공통 묶음 `hardening` 에서 같은 값을 받는다: `SystemCallArchitectures=native`,
  `SystemCallFilter=@system-service`, `MemoryDenyWriteExecute=true`. `SystemCallErrorNumber` 는 두지 않아, 목록 밖의 호출은 SIGSYS(31)로
  그 프로세스를 끝내고 커널 기록에 호출 번호가 남는다.
- 읽기 단위 `stt-reader@` 는 공통 묶음이 아니라 자기 설정을 가진다. 허용은 같은 `@system-service` 이고, FR-008 의 「더 좁은 제한」으로
  `narrowed` 칸의 호출 묶음·호출을 뺀다. 해독이 이 제한 아래에서 되는 것은 VM 시험이 2초 사인파로 확인한다(`MemoryDenyWriteExecute` 도 켠 채로).
- 더한 예외는 아직 없다. 호스트에서 필터를 걸지 않은 채 실제 전사를 관측하는 일(R2)은 이 표를 채운 시점에 하지 않았다. 관측으로 묶음 밖 호출이
  나오면 그 호출만 해당 단위의 `added` 에 적고, `reason`(무엇이 쓰는가)과 `observed`(관측한 날, 호출 번호)를 채운 뒤에 `nix/module.nix` 에
  이 표의 같은 줄을 가리키는 주석과 함께 더한다. 표에 없는 예외는 두지 않는다.
- 워커의 쓰기·실행 메모리 금지(`mdwe`)는 `pending` 이다. 호스트에서 켠 채로 1차·2차 전사를 돌려 보고 `on` 또는 `off`(거부된 지점을
  `mdwe_evidence` 에 적음)로 확정한다. 나머지 세 단위는 VM 시험이 켠 채로 통과한 것을 근거로 `on` 이다.

## 2. 녹음 읽기 격리

**누가 무엇을 읽는가.** 받기(`stt-intake`)와 워커(`stt-worker`)는 녹음을 해석하지 않는다. 길이 재기(받기)와 해독(워커)은 연결마다 새로 뜨는
`stt-reader@` 인스턴스가 맡는다. 호출하는 쪽은 `/run/stt-reader.sock`(`stt:stt` 0600)에 연결해 녹음 하나를 읽기 전용 기술자로 넘기고(해독이면 결과
파이프의 쓰는 쪽도), 읽기 단위는 경로를 쓰지 않고 받은 기술자만 읽는다. 읽기 한 번이 인스턴스 하나이고, 끝나면 systemd 가 그 cgroup 을 지운다.

**무엇에 닿지 못하는가.**
- 파일: `/var/lib/stt`(다른 작업의 녹음·기록·해독된 음성·응답·`state.lock`)와 `/var/lib/stt-models` 는 권한(일회용 UID 는 `stt` 가 아니다)과
  `InaccessiblePaths` 의 두 겹으로 막힌다. `/run/stt`(0755)와 그 안의 `gpu.lock`(0644)은 권한으로는 읽을 수 있으므로 `InaccessiblePaths` 한 겹만이
  막는다(research R12).
- 쓰기: 이 단위에서 쓰기가 되는 자리(`/tmp`·`/var/tmp`·현재 폴더의 일회용 사설 공간)에는 파일을 만들 수는 있어도 자료는 쓰지 못한다
  (`LimitFSIZE=0`). 그 밖은 `ProtectSystem=strict` 로 읽기 전용이다. `/dev/shm` 은 `PrivateDevices=yes` 에도 호스트의 것과 같아(같은 장치·아이노드, 1777,
  만든 파일이 호스트에 남음) 읽기 전용 빈 tmpfs 로 덮는다(`TemporaryFileSystem=/dev/shm:ro`).
- 호스트의 다른 파일: 위에서 닫은 경로 밖에서 누구나 읽을 수 있는 파일은 읽기 단위도 읽을 수 있다(`/var/lib/stt*`·`/run/stt` 는 닫혔지만 홈 등 그 밖의
  경로는 `ProtectHome` 외에는 열려 있다). 이 호스트에서는 다른 서비스의 상태가 `/var/lib/private`(0700)에 있어 일회용 UID 로는 들어가지 못한다.
  이 잔여 노출을 허용할지는 열린 운영자 질문이다(통제자 결정 20). 모듈은 바꾸지 않았다.
- 장치·망: `PrivateDevices`(GPU 장치 노드 없음), `PrivateNetwork`, `RestrictAddressFamilies=none`(소켓을 만들 수 없음. 받은 연결은 그대로 쓴다).
- 다른 프로세스: `PrivatePIDs`·`ProtectProc=invisible`·`ProcSubset=pid`·`PrivateIPC`·`PrivateTmp`. 겹쳐 도는 인스턴스끼리도 서로를 보지 못한다.
  템플릿의 모든 인스턴스는 같은 동적 UID 를 받으므로(인스턴스별 `User=` 는 이름 길이 한도로 쓸 수 없다. contracts/module.md §2.2) UID 가 아니라 이
  이름공간이 둘을 가른다. 같은 동적 UID 를 쓰는 인스턴스끼리는 커널 사용자 키링을 나누므로 §1 의 필터에서 `@keyring`(`add_key`·`request_key`·`keyctl`)을
  뺐다.
- 권한: `CapabilityBoundingSet` 비움, `NoNewPrivileges`, 위 §1 의 좁은 호출 필터와 `MemoryDenyWriteExecute`.

**상한.** 메모리 `MemoryMax=768M`(스왑 0), 프로세스 `TasksMax=32`, 시간은 감독이 길이 재기 30초·해독 240초(+판정 여유 5초)에서 자식을 죽이고, 단위의
`RuntimeMaxSec=300` 이 그 위의 받침이다. 코어 덤프와 파일 쓰기는 없다(`LimitCORE=0`, `LimitFSIZE=0`). 한 인스턴스가 상한에 걸려도 받기·워커의
cgroup 은 영향이 없고 그 작업 하나만 실패한다.

**끊김에 바로 멈춤.** 호출하는 쪽이 연결을 끊으면(다 지우기, 매일 23:58 정리, 워커 정지) 감독이 해독 자식을 SIGKILL 로 죽이고 인스턴스가 끝난다.
`KillSignal=SIGKILL` 이라 단위 정지도 기다리지 않는다. 읽기 단위의 감독과 자식은 로그를 쓰지 않아 저널에 작업 ID·경로가 남지 않는다.

## 3. 작업 실패와 단위 비정상 종료 구별

새 알림 길은 없다. 운영자는 다음 두 곳을 구별해서 본다.

- **작업 하나의 실패**: 응답의 `failure.reason`, 그리고 단위 로그의 `<request_id> failed <reason>` 줄.

  ```sh
  journalctl -u stt-worker -u stt-intake
  ```

- **서버 단위의 비정상 종료**: 실패한 단위 목록과 종료 원인. 메모리 상한(OOM)이면 `Result=oom-kill`, 호출 필터에 걸렸으면 SIGSYS(31)이다.
  막힌 호출 번호는 커널 기록에 있다.

  ```sh
  systemctl --failed
  systemctl show -p Result -p ExecMainCode -p ExecMainStatus <unit>
  journalctl -k -g 'type=1326'
  ```

- **읽기 단위를 쓸 수 없을 때**: 읽기 소켓이 없거나 인스턴스가 뜨지 못하면 받기·워커는 녹음을 직접 해독하는 대체 길 없이 그 요청·작업만 실패로
  끝낸다. 보이는 것은 다음과 같다.
  - 워커 로그의 `<request_id> failed engine_error`(응답의 `failure.reason` 도 `engine_error`).
  - 받기 로그의 그 요청 ID 와 이유. 받기는 그 요청을 응답 없이 inbox 에서 치운다(001 FR-032a). 이때 `stt-intake.service` 는 실패로 끝난다.
  - 소켓 단위의 상태.

    ```sh
    systemctl status stt-reader.socket
    journalctl -u 'stt-reader@*'
    ```

  녹음이 읽히지 않은 경우(`unreadable_audio`)와 달리, 읽기 단위를 못 띄운 것은 녹음 탓이 아니다.
- **배포 메모**(`switch-to-configuration` 의 동작을 소스로 읽고 VM 시험으로 확인):
  - 워커는 `restartIfChanged=false` 라서, 스위치 때 도는 워커는 큐를 새 코드가 아닌 옛 코드로 계속 비운다. 2차 격리가 적용됐다고 부르기 전에
    `systemctl show -p MainPID stt-worker.service` 의 값이 스위치 전과 달라졌는지 확인한다(같으면 옛 워커가 돈다).
  - `systemctl restart stt-reader.socket` 을 하기 전에 `stt-intake.path` 와 `stt-worker.path` 를 멈추고 받기·워커가 놀 때까지(`ActiveState=inactive`)
    기다린 뒤, 소켓을 다시 띄우고 두 path 단위를 다시 시작한다. 그래야 그 사이 들어온 요청이 유실되지 않는다.
  - 읽기 단위 템플릿(`stt-reader@.service`)이나 패키지가 바뀌어도 도는 읽기는 끝까지 마치고, 받기·워커와 소켓은 멈추지 않는다(`restartIfChanged=false`,
    받기·워커는 소켓을 `Requires` 가 아니라 `Wants` 로 둔다). 다음 읽기부터 새 템플릿이다. 워커는 도는 전사를 끊지 않고 다음 시작부터 새 설정을 쓴다.
  - 소켓 설정(경로·모드·`MaxConnections` 등)을 바꾼 배포는 스위치가 소켓 단위를 다시 띄우지 않는다. 적용하려면 운영자가
    `systemctl restart stt-reader.socket` 을 따로 한다. 그동안 들어온 요청과 작업은 위와 같이 끝난다(워커 `engine_error`, 받기는 001 FR-032a 대로
    응답 없음. 운영자 결정 2026-11-08).

호출 번호는 `scmp_sys_resolver <번호>` 로 이름을 찾는다. 서버 전체의 `OnFailure` 알림은 nixos-config 의 별도 과제다.

## 4. 판 기록과 갱신 정책

### 4.1 구성 요소 판 기록

<!-- stt:versions -->

| component | role | version | pinned_in | since | last_updated | comparison |
|---|---|---|---|---|---|---|

### 4.2 갱신 정책

### 4.3 보안 공지 기록

<!-- stt:advisories -->

| advisory | component | published | updated | reason |
|---|---|---|---|---|

### 4.4 매달 확인 기록

<!-- stt:monthly-checks -->

| date | sources | result |
|---|---|---|
