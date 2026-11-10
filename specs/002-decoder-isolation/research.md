# Research: 녹음 해독 격리 (Phase 0)

- 작성: 2026-11-09 · 입력: [spec.md](spec.md), `idea/decoder-isolation/tech-notes.md`(이하 「기술 메모」), 인계
  `docs/handoff-stt-decoder-security-20261108.md`(이하 「인계」), 이 저장소 `fd930cf`, 이 호스트(miniecoinfo) 실측.
- 실측은 버리는 코드로 했고 저장소 밖(scratchpad)에 두었다. 시험 녹음은 모두 사인파로 만든 정상 파일과 녹음 확장자를 붙인 텍스트 파일이다 —
  해독기를 노리는 파일은 만들지 않았다(FR-028).
- 형식: 결정 · 근거 · 대안. 🟢 = 이 세션에서 확인한 사실, `[VERIFY]` = 구현·배포 때 확인할 것(괄호 안은 [quickstart.md](quickstart.md)의 단계).
- `path:line` 은 `fd930cf` 기준이다.

## R1. P1 시스템 호출 제한 — 네 단위 공통 `@system-service`, 어기면 그 프로세스가 죽는다

- **결정**: 공통 묶음(`nix/module.nix:35-58` 의 `hardening`)에 `SystemCallArchitectures=native`, `SystemCallFilter=@system-service`,
  `MemoryDenyWriteExecute=yes`(R3)를 더한다. 받기·워커·청소·모델 받기 네 단위가 모두 받는다(FR-001·FR-004). 모델 받기 단위는 이미
  `hardening //` 로 이 묶음을 쓰므로 따로 손대지 않는다. `SystemCallErrorNumber` 는 두지 않아, 목록 밖의 호출은 SIGSYS 로 그 프로세스를
  끝낸다. 예외는 R2 의 관측에서 나온 것만 그 단위에 더한다(FR-002).
- **근거** 🟢 (`systemd-analyze syscall-filter`, systemd 262, 2026-11-09):
  - 들어 있는 것: CUDA 가 쓰는 `ioctl`, `mmap`·`mprotect`(`@default`), `mbind`·`sched_setaffinity`(`@resources`), IPC(`@ipc`).
  - 빠져 있는 것: `mount`·`umount2`·`pivot_root`·`chroot`, `bpf`, 모듈 적재(`init_module`), `kexec_load`·`reboot`, `swapon`, 시계
    설정(`settimeofday`), `ptrace`·`perf_event_open`·`pidfd_getfd`(`@debug`).
  - 들어 있지만 권한을 늘리지 못하는 것: `setuid` 계열(`@setuid`)·`chown` 계열(`@chown`)·`capset` 은 `@privileged` 에도 속하지만
    `@system-service` 안에 있다. 단위들은 능력 0·`NoNewPrivileges` 라 이것들로 권한을 얻지 못한다. `setns`·`unshare`(`@process`)는
    `RestrictNamespaces` 가 막는다. 읽기 단위에서는 `~@privileged` 로 이 묶음까지 막는다(R5).
  - 일반 서비스 범위에 들지만 읽기에 필요 없는 것: `process_vm_readv`·`process_vm_writev`·`memfd_create`(`@ipc`), io_uring(`@aio`),
    `userfaultfd`. 읽기 단위에서는 이 가운데 `process_vm_*`·io_uring·`userfaultfd` 를 더 막는다(R5).
  - 죽이는 쪽을 고른 까닭: 막힌 호출이 말없는 대체로 이어지지 않고(헌법 V), 커널 기록에 호출 번호가 남는다(R2). EPERM 으로 돌려주면 라이브러리가
    조용히 다른 길로 갈 수 있고, 필터가 기록을 요청하지 않으면 errno 동작은 기록되지 않는다.
  - P1 동안 받기는 아직 녹음을 직접 연다(PyAV). 묶음 밖 호출로 받기가 죽으면 path 단위가 같은 요청으로 거듭 깨울 수 있다. 그래서 VM 시험이
    받기·청소 경로 전부를, 호스트 시험이 1차·2차 전사를 필터를 건 채로 거친 뒤에 배포한다(B-P1, C-P1). 그래도 되풀이가 나면 청소가 1시간
    지난 inbox 항목을 치우고(001 FR-032a), 호출하는 쪽은 5분 무응답으로 알게 된다(001 계약 §7).
- **대안**: EPERM 으로 돌려주기 — 위 이유로 기각. 네 단위마다 더 좁은 목록을 손으로 짜기 — 인계가 요청한 것은 일반 서비스 범위이고, 좁힐수록
  드문 경로(화자 구분, 모델 확인)에서 깨질 위험이 커진다. 더 좁은 제한은 녹음을 직접 해석하는 읽기 단위에만 둔다(R5).

## R2. P1 관측 — CUDA 쪽 예외는 관측한 것만 더한다

- **결정**: P1 을 배포하기 전에, 지금 배포된 단위(필터 없음)에 실행 시점 drop-in(`/run/systemd/system/<unit>.d/observe.conf`,
  `SystemCallLog=~@system-service`)을 붙인다. 그 상태로 1차·2차(화자 구분·힌트) 전사와 받기·청소·모델 확인을 한 번씩 돌리고, 커널 기록의
  `type=1326` 줄로 묶음 밖 호출을 모은다. 나온 것이 없으면 예외 없이 간다. 있으면 그 호출(또는 그것이 든 가장 작은 묶음)만 해당 단위에 더하고,
  이유와 관측 근거를 `docs/server-security.md` §1 과 `nix/module.nix` 의 그 줄 옆에 적는다. 관측이 끝나면 drop-in 을 지운다(C-P1-1).
- **근거** 🟢:
  - 이 호스트의 커널 명령줄에는 `audit=0` 이 없고, `kernel.seccomp.actions_logged` 에 `log`·`kill_process` 가 있다.
  - 사용자 관리자로 `SystemCallLog=acct` 를 걸고 `acct()` 를 부르자 `journalctl -k` 에
    `audit: type=1326 … syscall=163 … code=0x7ffc0000` 이 남았다. 막힌 호출로 죽은 경우도
    `sig=31 … syscall=237 … code=0x80000000` 으로 남았다(R5 의 `mbind` 관측). 줄에는 UID·PID·실행 파일·호출 번호만 있고, 녹음 내용·이름은
    없다(FR-019 범위 안).
  - 실행 시점 drop-in 은 저장소를 바꾸지 않고 `daemon-reload` 로 붙고 떨어진다. 워커는 `restartIfChanged=false` 라 도는 전사를 끊지 않는다.
- `[VERIFY]` (C-P1-1): 워커의 CUDA 경로(CTranslate2 4.8.1 CUDA 12, torch 2.13.0 CUDA 13, pyannote 4.0.7, NVIDIA 595.112.07)가
  `@system-service` 밖을 부르는지. 호출 번호는 `scmp_sys_resolver`(libseccomp)로 이름을 찾는다.
- **대안**: 먼저 필터를 걸고 죽는 곳을 고치기 — 운영 중인 전사를 실패시키며 찾는 셈이라 기각. strace 로 관측하기 — 단위 밖에서 돌아
  단위의 조건(이름공간·장치 정책)과 달라 기각.

## R3. 쓰기·실행 메모리 금지 — 켜는 것이 기본, 워커만 호스트에서 판정

- **결정**: `MemoryDenyWriteExecute=yes` 를 공통 묶음에 둔다. 받기·청소·읽기 단위는 VM 시험에서 확인하고, 모델 받기는 VM(망 실패까지)과
  호스트(부팅 확인)에서 확인한다. 워커는 호스트에서 켠 채로 1차·2차 전사를 돌려 본다. CUDA 문맥 생성이나 엔진이 거부되면 워커에만 `false` 를 두고,
  거부된 지점(오류 문장·호출)을 근거로 적는다(FR-003, C-P1-2).
- **근거** 🟢: 읽기 단위와 같은 제한(R5)을 사용자 관리자로 걸고, 배포된 서버 해석기(`stt-server-env`, python 3.12, av 18.0.0)로 AAC 해독과
  하위 프로세스 띄우기를 했다. 둘 다 정상으로 끝났다(2026-11-09). P1 동안 받기가 쓰는 길이 재기도 같은 PyAV 다. FFmpeg 은 실행 중에 코드를
  만들지 않는다.
- `[VERIFY]` (C-P1-2): CUDA 드라이버·CTranslate2·torch·pyannote 가 쓰기+실행 메모리를 요구하는지. 요구하면 워커만 끈다. P2 뒤의 워커는 녹음을
  해석하지 않으므로(R11) 남는 위험이 작다고 회신에 적는다.
- **대안**: 워커는 처음부터 끄기 — 시험하지 않고 끄면 FR-003 의 「막을 수 있는 단위에서 막는다」가 거짓이 될 수 있어 기각.

## R4. P2 격리 수단 — 연결마다 새로 뜨는 소켓 활성화 단위 + 일회용 사용자 + 기술자 넘기기

- **결정**: 녹음 읽기는 새 단위 `stt-reader@.service` 가 맡는다. 동작은 다음과 같다.
  - `stt-reader.socket` 이 `/run/stt-reader.sock`(SOCK_SEQPACKET, 소유 `stt`, 0600)에서 `Accept=yes` 로 듣는다. 연결 하나마다 systemd 가
    인스턴스 하나를 새로 띄운다.
  - 인스턴스는 `DynamicUser=yes`(일회용 UID)로 돌고, 사설 `/dev`, 망 이름공간 없음, 소켓 생성 금지, 상태 폴더 가림, 좁은 호출 묶음, 자기
    메모리·시간 상한을 받는다(R12, [contracts/module.md](contracts/module.md)).
  - 받기·워커는 녹음 파일을 읽기 전용으로 연 기술자(해독이면 출력 파이프의 쓰는 쪽도)를 그 연결로 넘긴다(SCM_RIGHTS). 읽기 단위는 경로를 쓰지 않고
    받은 기술자만 읽는다.
- **근거**:
  - 요구 대조
    - FR-006(작업마다 새 프로세스): 연결 하나 = 인스턴스 하나 = 읽기 한 번이다.
    - FR-007(자기 녹음만): 일회용 UID 는 `stt` 소유의 0700·2770·2750 폴더를 읽을 수 없다. 마운트 이름공간에서도 `/var/lib/stt`·
      `/var/lib/stt-models`·`/run/stt` 가 가려진다. 쥐는 것은 받은 녹음 하나(읽기 전용)와 출력 파이프뿐이다.
    - FR-008: GPU 장치 노드가 없는 사설 `/dev`, 망 이름공간 없음, 소켓을 만들 수 없음.
    - FR-009: 인스턴스마다 cgroup 이 따로라, 메모리 상한에 걸려도 그 인스턴스만 멈춘다. 받기·워커는 다른 cgroup 이다. 지금처럼 워커 단위 안의
      프로세스라면 `OOMPolicy=stop` 으로 워커 전체가 멈출 수 있다(기술 메모 §5).
    - FR-011: 인스턴스가 끝나면 systemd 가 cgroup 의 모든 프로세스를 죽이고, 사설 tmpfs `/tmp` 와 IPC 를 지운다. `DynamicUser` 가
      `PrivateTmp`(문서상 `disconnected`, 이 systemd 의 `systemctl show` 는 `yes`)·`RemoveIPC` 를 함께 건다(`systemd.exec(5)`).
  - 🟢 `systemd.socket(5)`: `Accept=yes` 는 연결마다 인스턴스를 따로 띄우므로 「병렬 연결끼리 샌드박스로 떼어 놓을 수 있다」고 적는다.
  - 🟢 `systemd.exec(5)`: `DynamicUser` 의 사용자 이름은 형식 접미사를 뺀 단위 이름, 곧 템플릿 접두사(`stt-reader`)에서 온다. 인스턴스 이름이 아니다.
    해시는 그 접두사가 사용자 이름이 될 수 없을 때만 쓴다. **그래서 동시에 도는 인스턴스는 모두 같은 UID 를 받는다**(VM 실측, systemd 261.1 — 처음에
    적은 「다른 UID 를 받는다」는 틀렸다). 인스턴스마다 `User=…%i` 로 가르는 길은 `Accept=yes` 인스턴스 이름(`<번호>-<쿠키>-<PID>_<pidfd>-<UID>`)이
    동적 사용자 이름의 31자 한도를 곧 넘어 시작이 거부되므로 쓸 수 없다([contracts/module.md](contracts/module.md) §2.2 「UID 에 대하여」).
    겹쳐 도는 두 읽기를 가르는 것은 UID 가 아니라 인스턴스마다 따로 만들어지는 `PrivatePIDs`·`PrivateIPC`·`PrivateTmp`·`ProtectProc=invisible` 이름공간이고,
    `[VERIFY]`(B-P2-4)는 두 읽기가 `/proc` 에서 자기 인스턴스의 프로세스만 보는지를 확인한다. 같은 문서는 디렉터리 기술자를 넘기지 말라고 경고한다. 넘기는 것은 일반 파일과 파이프뿐이다.
  - 🟢 PyAV 는 넘겨받은 기술자로 만든 파일 객체를 형식 허용목록과 함께 연다(2026-11-09).
    - M4A·MP3·ADTS AAC·Ogg 의 길이가 경로로 연 것과 같았다.
    - 녹음 확장자를 붙인 텍스트는 두 방식 모두 `InvalidDataError` 였다.
    - `socket.send_fds`·`recv_fds` 가 SOCK_SEQPACKET 에서 기술자를 넘긴다.
  - 인계가 든 수단(「GPU 가 닫힌 단위(PrivateDevices=yes)」, 「작업마다 새로 뜨는 일회용 프로세스(DynamicUser 또는 작업별 사용자)」)과 같아서,
    인계 §6 처럼 `systemctl show` 로 확인할 수 있다.
  - 호스트 설정(nixos-config)은 바뀌지 않는다. 단위와 소켓은 모듈이 만든다.
- **대안**:
  - **같은 `stt` 계정의 하위 프로세스가 Landlock·seccomp 를 스스로 걸기**: 바로 죽일 수 있고 새 단위도 없다. 그러나 UID 가 같아 커널 규칙 하나가
    유일한 벽이고, 워커 단위의 장치 정책은 NVIDIA 장치를 열어 둔다. 메모리 상한은 rlimit·`OOMPolicy` 손질로 흉내 내야 하고, 설정 조회로
    확인할 수 없다. 기각.
  - **워커가 `systemd-run` 으로 작업마다 임시 단위를 띄우기**: `stt` 계정에 시스템 관리자를 부를 권한(polkit)을 줘야 한다. 임시 단위는 `User=root`
    도 고를 수 있어 권한 상승 길이 된다. 기각.
  - **root 로 도는 상주 중개 서비스가 작업마다 사용자를 바꿔 띄우기**: systemd 가 이미 하는 일을 특권 코드로 새로 쓰는 셈이다. 기각.
  - **bubblewrap 등 사용자 이름공간**: `RestrictNamespaces` 를 풀어야 하고, 사용자 이름공간은 큰 커널 공격면이다. P1 의 목적과 반대라 기각.
  - **작업별 고정 사용자**: 사용자 묶음 관리와 작업마다 소유권 바꾸기(root 필요)가 따른다. `DynamicUser` 가 같은 일을 한다. 기각.
  - **처리 단계 전체(GPU 단계 포함)를 작업별 단위로 옮기기**: GPU 단계는 녹음을 해석하지 않으므로(R11) 필요 없다. GPU 잠금·엔진 구조를 다 바꾸게
    된다. 기각.

## R5. 읽기 단위의 안 — 감독과 해독 자식, 바로 멈추기

- **결정**: 읽기 인스턴스의 주 프로세스(`stt server read`, 이하 「감독」)는 표준 라이브러리만 쓰고, 녹음 바이트도 해독된 음성도 읽지 않는다.
  - 감독은 요청과 기술자를 받아 해독 자식(`python -m stt.server.reader child …`)을 띄우고, 기술자를 자식에게만 넘긴다.
  - 그다음 자식의 끝, 호출하는 쪽의 연결 끊김, 자기 시간 상한 가운데 먼저 오는 것을 기다린다. 끊김이나 상한이 오면 자식을 SIGKILL 로 죽이고 끝낸다.
  - 자식의 표준 입력·출력·오류는 `/dev/null` 이다.
- **근거**:
  - FR-013(지우기·매일 정리가 오면 읽기를 바로 멈춘다)은 해독기가 장악됐을 때에도 성립해야 한다.
    - 받기·워커는 UID 도 PID 이름공간도 다른 읽기 인스턴스에 신호를 보낼 수 없다.
    - 장악된 자식이 끊김을 무시해도, 녹음을 해석하지 않는 감독이 자식을 죽인다.
    - 감독이 죽으면 인스턴스가 끝나고, systemd 가 cgroup 전체를 죽인다. 감독은 `PrivatePIDs` 의 PID 1 이므로, 감독이 끝나면 커널도 그 PID
      이름공간의 남은 프로세스를 모두 죽인다(`pid_namespaces(7)`). 자식이 손자를 띄워 빠져나갈 길이 없다.
    - PID 1 은 처리기 없는 SIGTERM 을 무시하므로(001 워커와 같은 까닭, `nix/module.nix:161-164`) 읽기 단위에도 `KillSignal=SIGKILL` 을 둔다.
      두지 않으면 시간 상한·OOM 으로 멈출 때 `TimeoutStopSec`(90초)만큼 늦어진다.
  - 자식은 연결 소켓을 물려받지 않는다(`close_fds`). 그래서 감독의 대답을 위조할 수 없다.
  - 자식은 감독의 메모리에 닿을 수 없다.
    - `ptrace`(`@debug`)는 묶음 밖이다.
    - `process_vm_readv`·`process_vm_writev` 는 읽기 단위에서 따로 막는다.
    - Yama `ptrace_scope=1` 🟢 가 자손이 아닌 프로세스로의 접근을 막는다.
  - 자식이 할 수 있는 일은 출력 파이프에 바이트를 쓰는 것과 끝 코드를 내는 것뿐이다.
  - 🟢 묶음 실측(2026-11-09, 사용자 관리자 `systemd-run --user`, 배포된 서버 해석기)
    - 건 제한: `SystemCallArchitectures=native`, `@system-service ~@privileged`, `~process_vm_readv process_vm_writev io_uring_setup
      io_uring_enter io_uring_register userfaultfd`, `MemoryDenyWriteExecute`, `LimitFSIZE=0`, 소켓 생성 거부, `LockPersonality`,
      `RestrictRealtime`, `MemoryMax=512M`, `TasksMax=32`.
    - 결과
      - 감독 역할의 부모가 자식을 띄웠고, 자식의 AAC 해독이 제한 없을 때와 같은 640 342 바이트를 냈다.
      - 일반 파일 쓰기는 `EFBIG`, 소켓 만들기는 `EAFNOSUPPORT` 였다.
      - 커널 기록에 막힌 호출이 없었다.
    - `~@resources` 까지 막자 자식이 `mbind`(호출 237)에서 SIGSYS 로 죽었다. 그래서 `@resources` 는 막지 않는다. 이 묶음은 자기 자원 사용을
      바꾸는 호출이라 권한을 넓히지 않는다.
    - io_uring 은 이 호스트에서 모든 사용자에게 열려 있다(`kernel.io_uring_disabled=0`). 읽기에는 필요 없다.
  - 🟢 CPython 은 `SIGXFSZ`·`SIGPIPE` 를 무시한다(`signal.getsignal` = SIG_IGN). 파일 크기 상한과 끊긴 파이프는 프로세스를 죽이지 않고 오류로
    돌아온다.
- `[VERIFY]` (B-P2-1): 다음 둘은 VM 시험에서 본다.
  - 단위 파일의 `RestrictAddressFamilies=none` 이 받아들여지는지. 사용자 관리자의 `systemd-run -p` 는 `Invalid argument` 로 거절해서, 그때는
    거부 목록으로 대신 시험했다.
  - `ProtectProc=invisible`·`ProcSubset=pid`·`InaccessiblePaths`·`PrivateDevices` 를 건 채로 해독이 되는지. 마운트 이름공간이 필요해 사용자
    관리자로는 시험하지 못했다.
- **대안**: 해독 프로세스 하나가 연결을 스스로 살피기(협조) — 장악되면 무시할 수 있다. 그때 남는 것은 systemd 의 시간 상한뿐이라 FR-013 이 그만큼
  늦어진다. 기각.

## R6. 「시작」의 경계 — FR-015 와 FR-016 을 가르는 선

- **결정**: 감독은 자식을 띄우는 데 성공하면 `{"started": true}` 를, 실패하면 `{"started": false}` 를 보낸다. 호출하는 쪽은 다음과 같이 나눈다.
  - **띄우지 못함**: 「시작」 전의 모든 실패 — 접속 실패(ENOENT·ECONNREFUSED·EAGAIN), 「시작」 전의 끊김, `started: false`, 30초 안에
    「시작」이 오지 않음.
  - **읽기 실패**: 「시작」 뒤의 모든 실패 — 판정 없는 끊김, `failed`, 형식이 틀린 판정, 상한 넘김, 출력 상한 넘김·홀수 바이트.
- **근거**: 녹음 바이트를 처음 해석하는 것은 자식이고, 자식은 「시작」 바로 전에 뜬다. 그 전의 실패는 녹음과 무관한 서버 문제다(동적 사용자 할당·
  실행 실패·`MaxConnections` 초과 등으로 systemd 가 인스턴스를 띄우지 못함). 장악된 자식은 감독의 소켓을 쥐지 못하므로 「띄우지 못함」을 흉내 낼 수
  없다.
- 프로토콜 전체: [contracts/reader.md](contracts/reader.md).

## R7. 해독기는 하나 — PyAV 로 길이 재기와 해독, 서버 경로에서 ffmpeg 명령을 뺀다

- **결정**: 읽기 자식은 PyAV(휠에 든 FFmpeg)로 길이를 재고 해독한다.
  - 길이 재기는 지금 `intake.probe_duration`(`src/stt/server/intake.py:79-113`) 그대로다. 형식 허용목록으로 열어, 컨테이너 길이가 있으면 그것을,
    없으면 패킷 길이의 합을 쓴다.
  - 해독은 faster-whisper `decode_audio` 와 같은 순서다: 첫 잘못된 프레임에서 멈춤(그 앞까지 씀), 50만 샘플 묶음,
    `AudioResampler(format="s16", layout="mono", rate=16000)`, 끝에 비우기.
  - 화자 구분도 이 해독 결과를 쓴다. 서버 경로는 nixpkgs `ffmpeg` 명령을 더는 부르지 않는다.
  - 명령줄(`stt transcribe --diarize`)은 지금처럼 `ffmpeg` 명령을 쓴다(FR-022).
  - 실행 파일은 명령줄을 위해 `ffmpeg` 를 PATH 에 둔 채 감싸져 있고(`nix/package.nix:84-90`), 서버 단위도 같은 실행 파일을 띄운다. PATH 에서는
    빼지 않는다 — 서버용 감싸기를 따로 두면 패키지가 둘이 된다. 대신 엔진 시험이 `ffmpeg` 없는 PATH 와 `diarize.to_wav` 금지로 서버 경로가 그것을
    부르지 않음을 지킨다(tasks T019(f), analyze F1 2026-11-09).
- **근거** 🟢 (2026-11-09, 30분 사인파 AAC, 48 kHz 스테레오, 128 kbit/s):
  - 기술자로 받아 조금씩 써 낸 16 kHz 모노 s16 바이트가 `faster_whisper.decode_audio` 결과(×32768 정수화)와 SHA-256 까지 같았다. 전사 입력은
    지금과 같다.
  - 같은 파일을 `ffmpeg -ac 1 -ar 16000 -f s16le`(nixpkgs 8.1.2)로 바꾼 바이트도 PyAV(av 18.0.0, libavcodec 62.28.102)와 같았다. 화자 구분
    입력도 같을 것으로 본다.
  - 해독기가 하나면, 서버 경로에서 판을 관리할 구성 요소도 하나로 준다(R16).
- `[VERIFY]` (C-P2-5): 기준 녹음(001 T076 의 60초·1시간 녹음)으로 두 해독기의 s16 이 같은지 `cmp` 로 본다. 이어 1차·2차의 전사록·문단 목록을
  비교한다(SC-007). 다르면 차이를 운영자에게 보이고, 받아들인 뒤에만 완료로 친다.
- **대안**: 해독은 `ffmpeg` 명령, 길이 재기는 PyAV 로 나누기 — 해독기 둘이 그대로 남아 공격면과 갱신 경로가 둘이다. 읽기 단위에서 전사용(PyAV)과
  화자 구분용(`ffmpeg`)을 둘 다 만들기 — 같은 이유에 출력도 두 배라 기각.
- **덧붙임**: 코덱 허용목록(쓸 수 있는 해독기 이름의 제한)은 넣지 않는다. 지금 받는 MP4 계열 첨부의 영상 트랙 때문에 길이 재기가 달라질 수 있고,
  해독기가 장악돼도 R4 가 범위를 막는다. 후속 후보로 `docs/server-security.md` 에 적는다.

## R8. 해독된 음성 — 작업마다, 작업 폴더에, WAV 로, 파이프로 받아 워커가 쓴다

- **결정**:
  - 작업마다 새로 해독한다. 재전사도 자기 읽기를 한다(US2-2). 앞 작업의 해독 결과를 다시 쓰지 않는다.
  - 자식은 해독한 s16 샘플만 출력 파이프에 쓴다(머리 없음). 워커가 파이프를 읽어 `work/<request_id>/audio.wav`(0600, 16-bit 모노 16 kHz)에
    쓰고, 머리는 표준 `wave` 가 쓴다. 바이트 수가 홀수이거나 `MAX_DECODED_BYTES`(3시간 × 1.05 = 11 340초 × 32 000 B = 362 880 000 B)를 넘으면
    읽기 실패다.
  - 엔진(GPU 단계)은 이 WAV 만 받는다(R11).
- **근거**:
  - 삭제: `work/<request_id>/` 는 모든 끝 경로가 이미 지운다 — `lifecycle.finish`(완료·실패·지우기·매일 정리가 모두 이리로 온다), 워커의
    `_remove_work_folder`, 죽은 워커 정리 `clear_dead_worker`. 새 삭제 경로가 필요 없다(FR-012, R13).
  - 녹음마다 한 번 만들어 다시 쓰면 큰 자료(3시간에 약 346 MB)가 녹음 폴더에 23:58 까지 남는다. 작업 사이에 자료를 나누게 되고, 대기 중인 1차를
    가리키는 2차(001 R13)와도 겹친다. 해독은 3시간 녹음에도 18초 안팎이라(R9) 최대 5번 해도 싸다.
  - 출력을 파일 기술자가 아니라 파이프로 받는 까닭은 둘이다. 읽기 단위가 쓸 수 있는 파일이 하나도 없게 하고(`LimitFSIZE=0`), 크기 상한과 중단을
    워커가 쥔다. 워커가 파이프를 닫으면 자식의 다음 쓰기는 실패한다.
  - WAV 머리는 믿는 코드(워커)가 쓴다. GPU 단계가 해석하는 것은 그 머리와 정수 샘플뿐이고, 화자 구분의 `read_wav`(`src/stt/diarize.py:744`)를
    그대로 쓴다.
  - 공간 🟢: `/var/lib/stt` 는 LUKS 위의 루트이고 여유는 175 GB 다(2026-11-09). 워커는 한 번에 한 작업만 한다.
- **대안**: 원시 PCM 파일(`read_wav` 의 짝을 따로 써야 함), 녹음마다 재사용(위 이유), 읽기 단위가 출력 파일에 직접 쓰기(크기와 중단을 읽기 단위에
  맡기게 됨) — 기각.

## R9. 상한 값 — 이 호스트에서 잰 해독 속도로 정한다

🟢 2026-11-09. 사인파 30분, 48 kHz 스테레오, 배포와 같은 av 18.0.0, 한 스레드 Python 루프, `captest` 로 잼:

| 형식(허용목록 안) | 30분 해독 | 3시간 추정 | 최대 RSS |
|---|---|---|---|
| AAC(M4A) 128k | 2.1초 | 13초 | 104 MB |
| MP3 128k | 2.0초 | 12초 | 92 MB |
| Opus 64k(Ogg) | 3.0초 | 18초 | 97 MB |
| Vorbis q4(Ogg) | 1.6초 | 10초 | 97 MB |
| FLAC | 1.3초 | 8초 | 97 MB |
| WMA v2 128k | 1.4초 | 8초 | 97 MB |
| WAV s16 | 0.8초 | 5초 | 97 MB |

길이 재기(컨테이너 길이)는 3 ms, Python 시작과 `import av` 는 0.07초·35 MB 였다.

- **결정**(상수는 [contracts/reader.md](contracts/reader.md) §4):
  - 길이 재기 상한 30초, 해독 상한 240초. 해독 상한은 3시간 최악 추정(18초)의 13배다.
  - 해독 상한은 작업 처리 상한의 하한(5분, 001 R12)보다 짧다. 그래서 멈춘 해독은 늘 `processing_timeout` 이 아니라 `unreadable_audio` 로 끝난다
    (경계 사례 4 의 뒤쪽). 작업 상한이 먼저 오는 앞쪽은 상한을 작게 주입한 시험에서만 생긴다.
  - 「시작」 기다림은 30초다. 감독의 상한 위에 systemd `RuntimeMaxSec=300` 을 받침으로 둔다.
  - 읽기 인스턴스: `MemoryMax=768M`(측정 최대 104 MB 의 약 7배 — 사용자 관리자 시험은 512M 에서도 통과했다), `MemorySwapMax=0`, `LimitCORE=0`, `LimitFSIZE=0`, `TasksMax=32`.
- **근거**: 경계 사례 3(3시간 한도에 맞는 정상 녹음)을 실패시키지 않을 만큼 여유를 둔다. 받기의 접수 확인은 「직후」여야 한다(001 SC-011) —
  잰 것은 Python + PyAV 시작 0.07초(감독·자식 두 번)와 길이 재기 3 ms 라, systemd 의 인스턴스 시작까지 더해도 1초 안으로 본다. 나쁜 첨부 하나가
  받기를 붙잡는 시간의 상한(최종 검토에서 바로잡음): 읽기 단위가 정상이면 길이 재기 30초 + 판정 여유 5초, 곧 약 35초다. 인스턴스 시작이
  멈추면(「시작」 기다림 30초) 약 65초다. 그래서 받기를 지나는 나쁜 첨부 둘이 정상 요청 앞에 서면 그 요청의 접수 확인이 001 SC-011 의 1분을 넘을 수
  있다. `PROBE_LIMIT_S` 는 그대로 둔다(2026-11-09 결정).
- `[VERIFY]` (C-P2-3): 호스트에서 3시간 사인파(Opus·AAC)로 해독 시간과 최대 메모리를 한 번 더 잰다. 여유가 5배 아래면 상한을 고친다.
- `[VERIFY]` (C-P2-6): systemd 의 인스턴스 시작(동적 사용자 할당·이름공간 준비)을 더한 접수 확인 시간. 001 SC-011 의 「직후」 안이어야 한다.

## R10. 실패 이유와 작업 상한

- **결정**: [data-model.md](data-model.md) §5 의 표를 따른다. 요점:
  - **읽기 프로세스를 띄우지 못함**:
    - 처리 단계: `engine_error`, 고정 문장 「The server could not start the audio reader.」.
    - 받기: 예외를 올려 001 FR-032a 의 요청별 처리를 따른다 — 로그에 작업 ID 와 이유를 남기고 inbox 에서 치운다. 응답은 없고, 호출하는 쪽은
      5분 무응답으로 안다.
  - **시작 뒤 실패**: 받기·처리 모두 `unreadable_audio`, 문장은 받기가 지금 쓰는 「The recording could not be read as audio.」.
  - **작업 상한**(`processing_limit`)은 읽기 단계 시작부터 GPU 단계 끝까지 하나로 잰다. 읽는 중에 오면 `processing_timeout` 이고, GPU 단계는 남은
    시간을 받는다.
  - **GPU 단계**의 분류는 그대로다. 엔진이 내는 이유에서 `unreadable_audio` 가 빠진다(R11).
- **순서가 바뀌는 곳**: 지금은 엔진의 시작 점검(GPU·모델)이 해독보다 먼저다. 이제는 읽기가 먼저이므로, 녹음도 못 읽고 GPU 도 없는 이중 고장은
  `gpu_unavailable` 이 아니라 `unreadable_audio` 로 끝난다. 둘 다 참인 이유라 계약 위반은 아니다.
- **근거**: FR-015~018, 운영자 결정(2026-11-08). 읽기 실패는 읽기 단계에서, GPU 실패는 GPU 단계에서 정한다. 그래서 「판정 없이 끝난 엔진 =
  `engine_error`」(`src/stt/server/worker.py:37`)는 GPU 단계에만 남는다.

## R11. GPU 단계 — 해독된 음성만 받고, 명령줄은 그대로

- **결정**:
  - 워커가 쓰는 `spec.json` 에서 `audio_path` 를 뺀다(`src/stt/server/worker.py:46-63`).
  - 엔진은 `work_dir/audio.wav` 를 읽고 머리(1채널·2바이트·16 kHz)를 확인한다. 다르면 서버 버그이므로 `engine_error` 다.
  - 전사: WAV 샘플을 `int16 → float32 / 32768.0`(faster-whisper 와 같은 식)으로 바꿔 배열로 넘긴다. faster-whisper 는 배열을 받으면 해독하지
    않는다(설치본 `faster_whisper/transcribe.py:386-387`·`:875-876` 🟢).
  - 화자 구분: 해독된 WAV 로 바로 화자 구분과 문장 목소리 계산을 한다. `ffmpeg` 변환은 건너뛴다.
  - 명령줄 경로가 바뀌지 않도록, `transcribe_file`·`diarized_blocks`(`src/stt/transcribe.py:745`·`:871`)의 안쪽만 떼어 내 두 경로가 같은
    설정(빔 크기·VAD·힌트·단어 시각·화자 배정)을 나눠 쓰게 한다. 기존 명령줄 시험이 출력이 같음을 지킨다(FR-022).
  - 엔진의 `classify_error`(`src/stt/server/engine.py:54-76`)에서 PyAV 오류 → `unreadable_audio` 를 지운다. 워커가 엔진에서 받는 이유는
    `gpu_out_of_memory`·`gpu_unavailable`·`models_unavailable`·`engine_error` 넷이다.
- **근거**: FR-010. 엔진은 더는 해독하지 않으므로, 해독 오류 분류는 일어날 수 없는 상태를 위한 방어 분기가 된다(헌법 V).
- **바꿔야 하는 기존 시험**(내부 경계가 바뀌어서). 과제마다 까닭을 적는다.
  - `tests/server/test_engine.py:104`, `tests/server/test_us1.py:201`: 엔진 spec 의 `audio_path`.
  - `tests/server/test_us3.py:80`·`:103`: 2차가 1차의 녹음을 쓰는지. 이제는 읽기 요청이 받은 기술자로 확인한다.
  - `tests/server/test_intake.py:160-244`: 길이 재기 시험. 길이·형식 시험은 읽기 단위 시험으로, 「길이 재기가 실패하면 사본·기록 없음」 시험은
    받기 + 실패 대역 시험으로 옮긴다(tasks T013·T020(b)).
  - `tests/server/test_us5.py:147`·`:163`: 길이를 주입하는 지점.

## R12. 단위 설정과 호스트의 몫

- **결정**: [contracts/module.md](contracts/module.md). 요점:
  - 소켓은 `/run/stt-reader.sock` 이다. root 소유인 `/run` 바로 아래라 `stt` 가 지우거나 바꿔치기할 수 없다. `SocketUser=stt`·`SocketGroup=stt`·
    `SocketMode=0600`, `MaxConnections=4`(받기 1 + 워커 1 + 여유).
  - `stt-reader@.service`: `CollectMode=inactive-or-failed`(`systemd.socket(5)` 의 권고 — 실패한 인스턴스가 쌓이지 않게),
    `StandardInput=socket`·`StandardOutput=null`·`StandardError=journal`.
  - `InaccessiblePaths=-/var/lib/stt -/var/lib/stt-models -/run/stt -/nix/var -/mnt -/srv -/opt -/media`: 앞의 세 경로는 권한으로도 막히지만 한 겹 더 둔 것이다. 특히
    `/run/stt/gpu.lock` 은 0644 라 일회용 사용자도 열어 `flock` 할 수 있다. 가리지 않으면 장악된 읽기가 GPU 줄을 붙잡을 수 있다. 다만 `/run`·`/var` 를 빈 tmpfs 로
    덮은 뒤로는(아래 H1) 이 세 경로가 없어 `-` 때문에 건너뛰어진다. 설정은 두 번, 실제로는 tmpfs 가 가린다. `InaccessiblePaths` 의 세 항목은 기본이 아닌
    `stateDir`·`modelsDir`, 또는 tmpfs 항목이 빠질 때(호스트의 `lib.mkForce`)에만 일한다(재감사 RL3).
  - **자료 디스크와 덧붙인 설치 자리(재감사 RM2·RL1·RL2, 2026-11-10)**: `/mnt`·`/srv`·`/opt`·`/media` 의 누구나 읽을 수 있는 파일은 장악된 읽기가 읽어 자기 전사록으로 내보낼 수 있다
    (이 호스트의 `/mnt` 아래에는 0644 대용량 보관 파일이 있다). 그 자리를 열어 둔 채 붙들면 호스트의 `umount` 가 `EBUSY` 로 실패하고(RL1, 확인하지 않음), `/opt`(0711)는
    목록을 볼 수 없어 잠금 파일이 없는지 확인하지 못했다(RL2). 읽기는 이 아래에서 아무것도 필요로 하지 않으므로 운영자가 가리기로 정했다(2026-11-10). 모두 `-` 를 붙인다.
    RED 는 VM 의 권한 점검 대상에 네 자리 아래의 0644 root 파일을 더해 먼저 보았다: 옛 설정에서 `host-files` 의 성공이 12(파일마다 보기·열기·잠그기 3)였다.
    GREEN 에서 0 이다.
  - **호스트의 잠금 파일(최종 감사 H1, 2026-11-10)**: 같은 까닭이 호스트의 다른 0644 잠금 파일에도 닿는다. `flock(2)` 은 읽을 수 있는 기술자만 있으면 읽기 전용
    마운트에서도 걸리고, `fcntl` 읽기 잠금은 `O_RDONLY` 면 된다. `flock`·`fcntl` 은 필터(`@system-service`)가 허용한다. 읽기 인스턴스는 길이 재기 35초, 해독
    245초(받침 `RuntimeMaxSec=300`) 동안 산다. 그동안 `/run/nixos/switch-to-configuration.lock`(0644 root)을 쥐면 `nixos-rebuild switch` 가 막히거나 실패하고,
    `/nix/var/nix/db/db.sqlite-shm`(0644)에 SQLite 잠금을 쥐면 nix-daemon 쓰기가 `SQLITE_BUSY` 를 만난다. 그래서 `/run`·`/var` 를 빈 읽기 전용 tmpfs 로 덮고
    (`TemporaryFileSystem=/dev/shm:ro /run:ro /var:ro`), `/nix/var`(`/nix/store` 는 아니다 — 파이썬과 PyAV 가 거기서 읽힌다)를 가린다. 읽기는 `/run` 아래의 것을
    쓰지 않는다: 연결은 표준 입력, 로그는 표준 오류(기술자)로 받는다. 가리는 경로는 모두 `-` 를 붙인다 — `/run`·`/var` 를 덮고 나면 `/run/stt`·`/var/lib/stt` 가
    없어서, `-` 가 없으면 단위 시작이 실패한다. `[VERIFY]` (B-P2-1) — **닫음(VM, 2026-11-10)**: `TemporaryFileSystem=/var:ro` 는 `DynamicUser` 의 사설 `/tmp`·`/var/tmp` 와 함께 되고(VM 에서 `PrivateTmp=yes` 로 보이며 호스트에
    `systemd-private-*stt-reader*` 폴더가 남지 않는다), 파이썬과 PyAV 가 그대로 떠서 2초 녹음의 길이 재기·해독과 형식별 해독이 통과했다. 그래서 `/var` 전체를 덮고
    물러서지 않았다. `systemctl show` 는 `InaccessiblePaths` 를 `-` 를 붙인 채 보여 준다. 권한 점검(B-P2-4)에서 `/run/nixos/switch-to-configuration.lock`·`/nix/var/nix/db`
    아래 0644 root 파일은 열기·`flock -n` 이 모두 실패한다. 점검 대상 목록 파일은 이제 `/etc/vm-double/targets.json` 이다 — 이전 자리(`/run/vm-double`)는 `/run` 을 덮은 뒤 읽히지 않는다.
    같은 점검이 `/run`·`/var` 를 덮기 전(옛 설정)에는 `host-files` 에서 실패하는 것을 먼저 보았다.
  - **메모리 상한에서의 기록(재감사 RM1, 2026-11-10)**: 호스트의 `DefaultOOMPolicy=stop` 에서 읽기 단위가 `OOMPolicy` 를 두지 않으면, 메모리 상한이 해독 자식을 죽인 순간 관리자가 단위를
    멈추며 감독도 SIGKILL 로 죽인다. 감독이 `failed` 를 보내고 `killed by signal 9` 줄을 쓰기 전에 끝나는 경주가 된다. 그래서 `OOMPolicy=continue` 를 둔다. 감독은 `PrivatePIDs` 의
    PID 1 이고 가장 큰 프로세스가 아니라서 커널의 cgroup OOM 은 자식을 고른다. 감독 아래 메모리만 쓰는 자식을 띄우는 데 운영 코드에 시험용 길이 필요하지 않았다 —
    `reader.serve(child_command=…)` 가 이미 자식 명령을 인자로 받는다. VM 의 specialisation `reader-supervised-memory` 가 진짜 감독을 `serve(child_command=(python, 메모리만 쓰는 자식))` 으로 돌린다
    (자식은 녹음을 열지 않고 16 MiB 씩 할당만 한다. FR-028). `[VERIFY]` — **닫음(VM, systemd 261.1, 2026-11-10)**: 요청은 `unreadable_audio` 로 거절되고, 저널에 `reader child probe killed by signal 9`
    줄이 경고 수준으로 오며, 같은 인스턴스 이름으로 systemd 의 `The kernel OOM killer killed some processes in this unit.` 도 온다. 인스턴스는 정상 종료라 `--failed` 에 없다. 옛 메모리 대역(감독을 대신해
    대역이 직접 죽는 것)에서는 `Main process exited, code=killed, status=9/KILL` 과 `Failed with result 'signal'` 이 온다. 호스트의 systemd 262 로는 아직 돌려 보지 않았다 —
    262 의 `libsystemd-core` 에 OOM 문구와 `Service reached runtime time limit. Stopping.` 가 같은 글자로 있음만 `strings` 로 보았다. 실제 줄은 T042 에서 본다.
    systemd 의 OOM 줄은 notice 수준(이 호스트의 262 저널에서 확인)이고 `Main process exited`·`Failed with result`·시간 상한 줄은 warning 이다. 그래서 운영자 확인 명령(`docs/server-security.md` §3)은
    `-p warning` 없이 `-g 'reader child|oom|runtime time limit|main process exited'` 이고, VM 시험이 그 명령을 커서 이후로 좁혀 진짜 감독의 OOM(감독의 줄 + OOM 줄)과 감독이 죽는 OOM
    (`Main process exited, code=killed` + OOM 줄, 감독의 줄 없음) 둘 다에서 돌린다. `OOMPolicy`·`SyslogLevel` 의 RED 는 VM 단언이 아니라 변경 전 `nix eval`(`serviceConfig ? OOMPolicy` 가 `false`)과
    옛 모듈에 두 이름이 없다는 확인으로 남겼다. 속성 단언(`OOMPolicy == continue`, 단위 파일의 `SyslogLevel=warning`)은 옛 모듈에서 실패하는 결정적 단언이다.
  - 받기·워커는 `wants`·`after` 에 `stt-reader.socket` 을 두고, `ExecStart` 에 `--reader-socket /run/stt-reader.sock` 을 붙인다.
  - P1 의 세 설정은 읽기 단위에도 있고 더 좁다. P2 뒤로는 SC-001 의 「서버 단위」에 읽기 단위가 들어간다(단위 다섯).
  - nixos-config 는 바꿀 것이 없다. `DynamicUser` 의 이름 풀이(nss-systemd)가 없어도 UID 는 할당된다.
- `[VERIFY]` (B-P2-2): 다음을 VM 시험에서 본다.
  - 쓰기 금지(`ProtectSystem=strict`) 마운트 아래의 소켓에 받기·워커가 접속하는지. 유닉스 소켓 접속은 읽기 전용 마운트의 쓰기 금지에 걸리지 않는
    것으로 안다.
  - `PrivateNetwork` 단위에서 경로 소켓으로 접속하는지.

## R13. 삭제 — 새 자료가 지나는 길

| 경우 | 해독된 음성 `work/<id>/audio.wav` | 읽기 인스턴스 | 근거 코드 |
|---|---|---|---|
| 정상·실패로 끝남 | `finish` 가 작업 폴더를 지우고, 워커가 한 번 더 지운다 | 이미 끝남 | `lifecycle.py:143-169`, `worker.py:251-259` |
| 읽는 중 「다 지우기」 | `delete_everything` → `finish(deleted)` 가 지운다 | 워커가 job 이 `running` 이 아님을 보고(2초마다) 연결·파이프를 닫는다 → 감독이 자식을 죽이고 끝난다 | `lifecycle.py:194-216`, `worker.py:66-71` |
| 읽는 중 23:58 정리 | `purge_audio` → `finish(failed audio_deleted)` 가 지운다 | 같음 | `lifecycle.py:172-191` |
| 읽는 중 워커 강제 종료 | 다음 워커 시작이나 청소의 `clear_dead_worker` 가 `work/*` 를 지운다 → `interrupted` | 워커의 기술자가 닫혀 감독이 끊김을 본다 | `lifecycle.py:219-247` |
| 길이 재는 중 받기 강제 종료 | 해당 없음. 녹음 복사본은 기록 없는 폴더로 남고 청소가 1시간 뒤 지운다(001 그대로) | 같음 | `lifecycle.py:323-328` |

- **읽기 인스턴스의 메모리**: 스왑·덤프가 없다(`MemorySwapMax=0`·`LimitCORE=0`). `/tmp` 는 사설 tmpfs 라 인스턴스가 끝나면 사라지고 메모리
  상한에 든다. `LimitFSIZE=0` 이라 어떤 파일에도 자료를 쓰지 못한다.
- **지운 녹음의 inode**: 읽기 인스턴스가 기술자를 쥐는 동안만 남는다. 위 표대로 끊김 뒤 바로 끝나므로 「직후」 안이다.
- **시험**: 다섯 경우 모두 「남은 파일 0」을 본다(SC-008). pytest 는 감독과 대역으로, VM 은 진짜 단위로 본다.

## R14. 시험 전략 — 공격 파일 없이

- **pytest**(GPU·망·systemd 없이):
  - `tests/server/conftest.py` 에 읽기 소켓을 흉내 내는 장치를 둔다. 임시 SEQPACKET 소켓에서 연결을 받을 때마다 `python -m stt server read`(진짜
    감독)나 대역 스크립트를 띄우고, 연결을 표준 입력으로 준다. systemd 의 `Accept=yes` 와 같다.
  - 감독은 자식 명령을 인자로 받는다(워커가 엔진 명령을 받듯이). 그래서 대역 자식 — 오류, 멈춤, SIGKILL 로 끝남, 엉터리 출력, 상한을 넘는 출력 —
    을 넣을 수 있다.
  - 진짜 자식은 `encode_tone`·`make_wav` 로 만든 사인파 파일로 시험한다: 길이 재기, 해독, faster-whisper 와 바이트가 같은지.
  - 기존 받기 시험의 호출 64곳은 이 장치를 자동으로 쓰게 해서 호출부를 바꾸지 않는다.
- **VM 시험**(GPU 없이, 진짜 systemd):
  - 설정 값: P1 의 네 단위, 읽기 단위.
  - 진짜 해독을 거친 뒤의 `gpu_unavailable`.
  - 녹음 확장자를 붙인 텍스트(`corrupt.mp3`, 이미 있다)의 `unreadable_audio`, 그리고 함께 맡긴 정상 요청의 처리.
  - specialisation 으로 읽기 단위의 `ExecStart` 를 대역으로 바꾼 시험
    - 권한 점검 대역: 다른 작업의 녹음·작업 폴더·기록, inbox·outbox, `gpu.lock`, `/dev/nvidia*`(시험용 가짜 노드를 만들어 둔다), 망을 열어 보고
      결과를 판정에 담아 돌려준다.
    - 실패 대역: 판정 없이 끝남, 멈춤, 메모리 넘김.
    - 두 읽기의 UID 비교.
    - 읽는 중 「다 지우기」·23:58·워커 강제 종료.
- **호스트 시험**(운영자와): 관측(R2), 쓰기·실행 메모리 금지(R3), 설정 조회(인계 §6 의 명령), 기준 녹음의 1차·2차 비교, 텍스트 파일, 재전사 3번,
  1시간 녹음의 처리 시간, 읽는 중인 인스턴스의 `/dev` 확인(선택).
- **근거**: FR-028 과 001 T078 의 교훈 — 해독기를 노리는 입력은 만들지 않는다. 실패는 대역으로, 격리는 권한 점검으로 본다. 대역은 읽기 단위의
  실제 제한 안에서 돌기 때문에, 대역이 얻은 결과가 곧 그 단위의 권한이다.
- **대안**: 해독기를 깨는 파일로 시험하기 — FR-028 위반, 기각. 권한 점검을 `systemd-run` 으로 같은 속성을 흉내 내서 하기 — 그 단위 자체가 아니므로
  보조로만 쓴다.

## R15. 운영자가 보는 곳 — 작업 실패와 단위 비정상 종료 (FR-023)

- **결정**: 알림 길은 새로 만들지 않는다. `docs/server-security.md` §3 과 회신에 다음을 적는다.
  - **작업 하나의 실패**: 응답의 `failure.reason`, 그리고 단위 로그의 `<request_id> failed <reason>` 줄(`journalctl -u stt-worker -u stt-intake`).
  - **서버 단위의 비정상 종료**: `systemctl --failed`, 그리고 `systemctl show -p Result -p ExecMainCode -p ExecMainStatus <unit>`. OOM 이면
    `Result=oom-kill`, 호출 필터면 SIGSYS(31)이다. 막힌 호출 번호는 커널 기록(`journalctl -k -g 'type=1326'`)에 있다.
  - **읽기 인스턴스의 실패**(메모리 상한 등): 그 작업의 `unreadable_audio` 로 이미 응답에 나오고, `journalctl -u 'stt-reader@*'` 에 남는다. 감독이 자식의
    비정상 종료와 일찍 멈춘 해독에 고정된 영어 한 줄을 쓴다(작업 ID·경로·해독기 문구 없음, [contracts/reader.md](contracts/reader.md) §5.7, 최종 감사 M1·H2,
    운영자 결정 2026-11-10).
    인스턴스는 `CollectMode` 로 치워져 `--failed` 에 쌓이지 않는다. 단위 비정상 종료로 세지 않는다.
- **근거**: 운영자 결정(2026-11-08, Q1 A). 서버 전체의 `OnFailure` 알림은 nixos-config 의 별도 과제다.

## R16. P3 — 판 기록과 갱신 정책

- **결정**:
  - 기록과 정책은 `docs/server-security.md` §4 한 곳에 둔다. 표의 칸은 구성 요소, 서버 경로에서 하는 일, 지금 판, 고정한 곳, 들어온 날(커밋),
    마지막 갱신일이다. 그 아래에 정책(FR-025)과 매달 확인 기록표(확인한 날·본 곳·결과·보안 공지와 처리)를 둔다.
  - P2 뒤 서버 경로에서 녹음 바이트를 해석하는 것은 PyAV 휠의 FFmpeg 라이브러리뿐이다(R7). 그래서 매달의 갱신은
    `uv lock --upgrade-package av` 하나로 끝나고 nixpkgs 전체를 올릴 필요가 없다. 기술 메모 §6 의 `[VERIFY]`(해독기 둘만 올리기)는 이 설계로
    풀린다.
  - nixpkgs `ffmpeg` 명령은 운영자의 명령줄만 쓰고, nixpkgs 를 올릴 때 함께 바뀐다. 그 보안 공지도 같은 표에 적는다. 7일 안에 nixpkgs 를 올리거나,
    올리지 않는 이유(「서버 경로 밖」 등)를 적는다.
  - FFmpeg 보안 수정이 나왔는데 새 PyAV 휠이 아직 없으면, 「올리지 않는 이유」에 「배포된 휠 없음 + 읽기 격리(P2)가 범위를 막음」을 적고 다음 달
    확인에서 다시 본다.
  - 저장소 안에서 맞춰 볼 수 있는 값은 시험으로 지킨다. `tests/test_server_security_doc.py` 가 문서 표의 av 판(Python 3.12·3.11·3.10 셋, `uv.lock` 의 `resolution-markers` 로 구별)을 `uv.lock`
    과, nixpkgs 고정 커밋을 `flake.lock` 과 대조한다. Python 3.10 에는 `tomllib` 이 없으므로 문자열로 읽는다. 판을 올리고 문서를 안 고치면 CI 가
    실패한다(US5-3).
  - 확인할 곳: FFmpeg 보안 공지(<https://ffmpeg.org/security.html>), PyAV 새 판(<https://github.com/PyAV-Org/PyAV/releases>), nixpkgs 의
    ffmpeg 갱신.
- **근거**: 인계 요청 4, FR-024~026, 운영자 결정(2026-11-08).
- 달력에 기대는 시험(「이번 달 확인이 있는가」)은 두지 않는다. 날짜에 따라 CI 가 깨지면 관계없는 변경이 막힌다. 매달 확인은 운영자의 일이고,
  기록표가 그 증거다(SC-009).
- 기록된 날짜끼리 맞춰 보는 검사는 둔다(analyze D2·E3, 2026-11-09): 확인 기록의 첫 달과 마지막 달 사이에 빠진 달이 없음(US5-4), 공지마다
  7일 안에 올렸거나 이유가 있음(US5-5), 녹음을 해석하는 구성 요소의 기준 녹음 비교 날이 마지막 갱신일과 같거나 뒤임(US5-3). 오늘 날짜를 쓰지
  않으므로 날짜 때문에 CI 가 깨지지 않는다.
- 시험이 읽는 자리는 영어 표식이다(`<!-- stt:… -->` 주석, 영어 칸 이름·상태값, ISO 날짜). 문서는 한국어지만 시험 코드에는 한글을 쓰지 않는다
  (헌법 언어 규칙, analyze D1).

## R17. 회신 — 단계마다 하나

- **결정**: P1 배포 뒤에는 `specs/002-decoder-isolation/handoff/p1-reply.md`, P2·P3 배포 뒤에는 `p2-reply.md` 를 낸다.
  - 각 문서에는 인계 §6 의 확인 결과(설정 조회 값 그대로, 시험 결과), 요청 1~4 와 §5 질문의 답, bhu-llm 측정 문서 질문 6(보관 기간)의 답을 싣는다.
  - 운영자가 bhu-llm 대장 A18 로 옮긴다(FR-027).
  - 지금 판과 날짜는 P1 회신에 먼저 싣는다.
- **질문 6 의 답**(미리): stt 는 001 R22(2026-10-22)부터 녹음을 「다 지우기」나 23:58 까지 둔다고 가정했다. 그래서 보관 확대는 stt 의 삭제 설계를
  바꾸지 않는다. 그날 쌓인 다른 녹음에 닿는 위험은 P2 가 없앤다 — 읽기 단위는 자기 녹음 하나만 쥔다.

## R18. 계약 경계 — 바뀌지 않는 것과 고칠 내부 문서

- **바뀌지 않는 것**: 요청·응답·결과 스키마의 바이트와 `schema_version`(기존 바이트 일치 시험이 지킨다), inbox·outbox 경로와 권한, 「다 지우기」의
  의미, `from_request`, 23:58 정리, 실패 이유 목록 11개.
- **고칠 내부 문서**(구현 때):
  - `specs/001-server-transcription/contracts/module.md` §3: 읽기 단위 줄과 P1 설정.
  - `contracts/cli.md` §1: `stt server read` 와 `--reader-socket`.
  - `contracts/README.md` §2 그림: `/run/stt-reader.sock`.
  - 이 기능의 세부는 [contracts/](contracts/) 가 원천이다.
- **근거**: 헌법 VI, FR-020, 인계 §5. 내부 폴더와 엔진의 `spec.json` 은 계약 밖이다(001 contracts README §2).
