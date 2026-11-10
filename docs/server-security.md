# 서버 보안 기록 — 단위별 제한, 녹음 읽기 격리, 판 기록

이 문서는 `002-decoder-isolation` 기능이 요구하는 운영 기록이다. `tests/test_server_security_doc.py` 가 표식 주석(`<!-- stt:… -->`),
영어 칸 이름, 영어 상태값, ISO 날짜만 읽고 저장소의 값과 대조한다. 이유 같은 자유 문장은 한국어로 쓴다.

## 1. 단위별 제한과 예외

<!-- stt:syscall-table -->

| unit | filter | added | narrowed | reason | observed | mdwe | mdwe_evidence |
|---|---|---|---|---|---|---|---|
| `stt-intake` | `@system-service` | - | - | - | - | on | - |
| `stt-worker` | `@system-service` | - | - | - | - | on | - |
| `stt-sweep` | `@system-service` | - | - | - | - | on | - |
| `stt-models` | `@system-service` | - | - | - | - | on | - |
| `stt-reader@` | `@system-service` | - | @privileged, @keyring, process_vm_readv, process_vm_writev, io_uring_setup, io_uring_enter, io_uring_register, userfaultfd | 녹음 해독기를 노리는 입력이 읽기 프로세스를 장악해도 다른 프로세스의 메모리를 읽거나 쓰지 못하고, 권한 호출과 커널 비동기 입출력 길을 쓰지 못하게 한다. 해독은 이 호출 없이 된다(사용자 관리자에서 확인, 2026-11-09) | - | on | - |

- 네 단위는 `nix/module.nix` 의 공통 묶음 `hardening` 에서 같은 값을 받는다: `SystemCallArchitectures=native`,
  `SystemCallFilter=@system-service`, `MemoryDenyWriteExecute=true`. `SystemCallErrorNumber` 는 두지 않아, 목록 밖의 호출은 SIGSYS(31)로
  그 프로세스를 끝내고 커널 기록에 호출 번호가 남는다.
- 읽기 단위 `stt-reader@` 는 공통 묶음이 아니라 자기 설정을 가진다. 허용은 같은 `@system-service` 이고, FR-008 의 「더 좁은 제한」으로
  `narrowed` 칸의 호출 묶음·호출을 뺀다. 해독이 이 제한 아래에서 되는 것은 VM 시험이 2초 사인파로 확인한다(`MemoryDenyWriteExecute` 도 켠 채로).
- 더한 예외는 없다. 호스트 관측(R2, 2026-11-09 21:22–21:25 KST, 배포된 stt `ce5c325`): 네 단위에 `SystemCallLog=~@system-service` 를
  임시로 걸고 60초 녹음의 1차·2차(화자 구분·힌트) 전사, 녹음 확장자를 붙인 텍스트 첨부 하나, 「다 지우기」 세 번, 청소 한 번,
  `systemctl restart stt-models` 를 거쳤다. 커널 기록의 `type=1326` 은 0줄이었다. 같은 설정의 일회용 단위로 묶음 밖 호출(101)을 하나 부르자
  기록이 남는 것을 먼저 확인했으므로, 0줄은 묶음 밖 호출이 없었다는 뜻이다. 관측 설정은 지웠다. 앞으로 관측에서 묶음 밖 호출이
  나오면 그 호출만 해당 단위의 `added` 에 적고, `reason`(무엇이 쓰는가)과 `observed`(관측한 날, 호출 번호)를 채운 뒤에 `nix/module.nix` 에
  이 표의 같은 줄을 가리키는 주석과 함께 더한다. 표에 없는 예외는 두지 않는다.
- 워커의 쓰기·실행 메모리 금지(`mdwe`)는 `on` 이다. P1 배포(2026-11-09) 뒤 호스트에서 켠 채로 60초·1시간 녹음의 1차·2차(화자 구분)
  전사가 CUDA 에서 끝났고 결과가 기준과 같았다. 나머지 세 단위는 VM 시험이 켠 채로 통과한 것을 근거로 `on` 이다.

## 2. 녹음 읽기 격리

**누가 무엇을 읽는가.** 받기(`stt-intake`)와 워커(`stt-worker`)는 녹음을 해석하지 않는다. 길이 재기(받기)와 해독(워커)은 연결마다 새로 뜨는
`stt-reader@` 인스턴스가 맡는다. 호출하는 쪽은 `/run/stt-reader.sock`(`stt:stt` 0600)에 연결해 녹음 하나를 읽기 전용 기술자로 넘기고(해독이면 결과
파이프의 쓰는 쪽도), 읽기 단위는 경로를 쓰지 않고 받은 기술자만 읽는다. 읽기 한 번이 인스턴스 하나이고, 끝나면 systemd 가 그 cgroup 을 지운다.

**무엇에 닿지 못하는가.**
- 파일: `/var/lib/stt`(다른 작업의 녹음·기록·해독된 음성·응답·`state.lock`)와 `/var/lib/stt-models` 는 권한(일회용 UID 는 `stt` 가 아니다)과
  `InaccessiblePaths` 의 두 겹으로 막힌다. `/run/stt`(0755)와 그 안의 `gpu.lock`(0644)은 권한으로는 읽을 수 있으므로 `InaccessiblePaths` 와 `/run` 을 덮은 빈 tmpfs(아래) 두 겹이
  막는다(research R12).
- 쓰기: 이 단위에서 쓰기가 되는 자리는 인스턴스마다 따로인 사설 `/tmp`·`/var/tmp` 뿐이고, 거기에도 파일을 만들 수는 있어도 자료는 쓰지
  못한다(`LimitFSIZE=0`). 현재 폴더를 비롯한 그 밖은 `ProtectSystem=strict` 로 읽기 전용이다. `/dev/shm` 은 `PrivateDevices=yes` 에도 호스트의 것과 같아(같은 장치·아이노드, 1777,
  만든 파일이 호스트에 남음) 읽기 전용 빈 tmpfs 로 덮는다(`TemporaryFileSystem=/dev/shm:ro`).
- 호스트의 잠금 파일: `/run` 과 `/var` 는 빈 읽기 전용 tmpfs 로 덮고(`TemporaryFileSystem=/dev/shm:ro /run:ro /var:ro`) `/nix/var` 는 가린다
  (`InaccessiblePaths` 의 `-/nix/var`). `flock` 은 읽을 수 있는 기술자만 있으면 읽기 전용 마운트에서도 걸리므로, 누구나 읽을 수 있는 호스트의 잠금 파일
  (`/run/nixos/switch-to-configuration.lock`, `/nix/var/nix/db` 아래의 파일)을 장악된 읽기가 붙잡아 배포를 막는 길을 닫았다(최종 감사 H1, research R12).
  읽기는 `/run` 아래의 것을 쓰지 않는다. 가리는 경로는 모두 `-` 를 붙여, 없는 경로가 단위를 실패시키지 않는다. VM 시험의 권한 점검이 이 파일들을 열지도
  잠그지도 못함을 본다.
- 호스트의 다른 파일(남는 위험): 위에서 닫은 경로 밖에서 누구나 읽을 수 있는 파일(`/etc`, `/nix/store`, `/usr` 등)은 읽기 단위도 읽을 수 있다. 장악된 읽기는
  그 내용을 자기 작업의 전사록으로 내보낼 수 있다 — 읽은 내용을 녹음 속 음성으로 합성해 두면 Whisper 가 받아 적고 전사록이 그 작업의 호출자에게 돌아간다
  (느리다. 다른 이용자의 녹음·전사록은 닿지 않는다). 망으로 나가는 길은 없다(`PrivateNetwork`). 홈은 `ProtectHome=true` 로 닫혔다. 이 호스트에서는 다른
  서비스의 상태가 `/var/lib/private`(0700)에 있어 일회용 UID 로는 들어가지 못하고, `/var` 는 위에서 덮었다.
  운영자 결정(2026-11-09): 이 잔여 노출을 받아들이고 모듈은 바꾸지 않으며 나중에 다시 검토한다. 운영자가 읽은 내용이 전사록으로 나갈 수 있다는 것까지 알고
  다시 받아들였다(2026-11-10).
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
`KillSignal=SIGKILL` 이라 단위 정지도 기다리지 않는다. 읽기 단위의 자식은 로그를 쓰지 않고, 감독은 아래 §3 의 고정된 영어 한 줄만 쓴다(감독 내부 예외의 트레이스백은 예외다. 코드 위치만 담고 녹음 경로는 담지 않는다). 저널에 작업 ID·경로·
파일 이름·해독기 문구가 남지 않는다.

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
    `systemctl show -p MainPID stt-worker.service` 의 값이 스위치 전과 달라졌는지 확인한다(0 이 아닌 같은 값이면 옛 워커가 아직 돈다. 스위치 때 워커가 놀고 있었다면 전후 모두 0 이고, 다음 시작부터 새 코드다).
  - `systemctl restart stt-reader.socket` 을 하기 전에 `stt-intake.path` 와 `stt-worker.path` 를 멈추고 받기·워커가 놀 때까지(`ActiveState=inactive`)
    기다린 뒤, 소켓을 다시 띄우고 두 path 단위를 다시 시작한다. 그래야 그 사이 들어온 요청이 유실되지 않는다.
  - 읽기 단위 템플릿(`stt-reader@.service`)이나 패키지가 바뀌어도 도는 읽기는 끝까지 마치고, 받기·워커와 소켓은 멈추지 않는다(`restartIfChanged=false`,
    받기·워커는 소켓을 `Requires` 가 아니라 `Wants` 로 둔다). 다음 읽기부터 새 템플릿이다. 워커는 도는 전사를 끊지 않고 다음 시작부터 새 설정을 쓴다.
  - 소켓 설정(경로·모드·`MaxConnections` 등)을 바꾼 배포는 스위치가 소켓 단위를 다시 띄우지 않는다. 적용하려면 운영자가
    `systemctl restart stt-reader.socket` 을 따로 한다. 그동안 들어온 요청과 작업은 위와 같이 끝난다(워커 `engine_error`, 받기는 001 FR-032a 대로
    응답 없음. 운영자 결정 2026-11-08).

- **읽기 인스턴스의 비정상 종료와 일찍 멈춘 해독**: 읽기 단위의 감독은 해독 자식이 비정상으로 끝나거나 시간 상한을 넘기거나 엉터리 결과를 내면 단위 로그에 아래 고정된 영어 한 줄을 쓴다. 작업 ID·경로·
  파일 이름·해독기 문구는 없다. 정상 판정에는 아무것도 쓰지 않는다. 장악된 자식은 이 줄을 속일 수 있으므로 운영자에게 알리는 신호일 뿐 보안 통제가 아니다.

  ```text
  reader child <probe|decode> exited with status <n>
  reader child <probe|decode> killed by signal <n>
  reader child decode stopped early at invalid data
  reader child <probe|decode> timed out
  reader child probe returned an invalid result
  ```

  ```sh
  journalctl -u 'stt-reader@*' -g 'reader child'
  ```

  - `killed by signal 31` 은 시스템 호출 필터(SIGSYS), `killed by signal 9` 는 메모리 상한(OOM)이거나 밖에서 죽인 것이다. 감독이 시간 상한에 죽인 자식은 `timed out` 이고, 단위의 `RuntimeMaxSec` 가 cgroup 전체(감독 포함)를 죽인 경우는 줄이 남지 않는다. 읽기 인스턴스는
    `CollectMode` 로 치워져 `systemctl --failed` 에 쌓이지 않으므로, 이 줄이 단위 수준의 유일한 흔적이다.
  - `timed out` 은 감독이 시간 상한(길이 재기 30초·해독 240초)에 자식을 죽였다는 뜻이다. `returned an invalid result` 는 길이 재기 자식이 정상으로 끝났지만 결과가
    규격에 맞지 않았다는 뜻이다. 호출하는 쪽이 먼저 끊은 경우(다 지우기 등)는 정상이라 줄이 없다.
  - `stopped early at invalid data` 는 오류가 아니다: 녹음 가운데의 깨진 조각에서 해독이 멈췄고, 그 앞까지 전사해 작업은 `done` 으로 끝난다(faster-whisper 와
    같은 동작, 운영자 결정 2026-11-10). 첫 조각부터 깨졌으면 전사록이 빈 채 `done` 이다. 명령줄은 같은 일에 표준 오류로 경고 한 줄을 낸다.
  - 한 녹음에서만 `killed by signal 31`(또는 `type=1326`)이 나오고 같은 형식의 다른 녹음은 읽힌다면, 해독기를 노린 녹음을 필터가 막은 것일 수 있다. 그 녹음을
    직접 열지 말고(아래 규칙) 시각으로 요청을 찾아 호출하는 쪽에 알린다. 이 줄과 `type=1326` 은 길이 재기(`probe`)에서도 같은 모양으로 나온다.
  - 이 줄에는 작업 ID 가 없다. 어느 작업인지는 줄의 시각을 받기·워커 기록(`journalctl -u stt-intake -u stt-worker`)의 시각과 맞춰 찾는다. 커널 기록
    `journalctl -k -g 'type=1326'` 의 시각도 같은 방법으로 맞춘다.
- **한 형식이 늘 `unreadable_audio` 일 때**: 같은 확장자의 정상 녹음이 모두 `unreadable_audio` 로 끝나면 녹음 탓이 아니라 그 형식의 해독 경로가 시스템 호출 필터나
  메모리 상한에 막힌 것일 수 있다. 위의 `journalctl -k -g 'type=1326'` 에 읽기 UID 의 줄이 있는지, `reader child decode killed by signal 31`(길이 재기가 막혔으면 `reader child probe killed by signal 31`)이 같은 시각에 있는지 본다.
  VM 시험이 `AUDIO_FORMATS` 의 형식마다 사인파 녹음 하나를 실제 읽기 단위로 해독해 이 기록이 비어 있음을 확인하지만(FR-028), 실제 코덱 경로는 FFmpeg 판에
  따라 달라질 수 있다.
- **서버 녹음을 직접 열지 않는다**: `/var/lib/stt/audio/` 의 녹음을 `stt transcribe`·`ffprobe`·`ffmpeg` 로 직접 열지 않는다. 명령줄은 격리되지 않아(FR-022) 모든
  FFmpeg 형식을 열고, 해독기를 노리는 파일이면 운영자(root) 또는 `stt` 권한으로 모든 이용자의 녹음·GPU·망이 닿는 곳에서 돈다. 읽히지 않는 녹음을 다시 확인하려면
  읽기 단위를 거친다(요청을 다시 맡기는 것 등).

호출 번호는 `scmp_sys_resolver <번호>` 로 이름을 찾는다. 서버 전체의 `OnFailure` 알림은 nixos-config 의 별도 과제다.

## 4. 판 기록과 갱신 정책

서버 경로에서 녹음 바이트를 해석하는 것은 PyAV 휠에 들어 있는 FFmpeg 라이브러리(libavcodec·libavformat) 하나뿐이다(P2 뒤 읽기 단위 안에서만).
nixpkgs 의 `ffmpeg` 명령은 운영자의 명령줄만 쓴다. 이 절의 표는 `tests/test_server_security_doc.py` 가 영어 표식·칸 이름·ISO 날짜로만 읽어
`uv.lock`·`flake.lock` 과 맞춰 본다. 오늘 날짜는 쓰지 않으므로 날짜 때문에 CI 가 깨지지 않는다.

### 4.1 구성 요소 판 기록

`role`: `decodes-recordings` 는 서버 경로에서 녹음 바이트를 해석, `cli-only` 는 운영자 명령줄만, `decoded-audio-only` 는 해독된 음성만 다룬다.
`since` 는 그 판을 넣은 커밋(`git log -S` 로 찾음)의 날짜와 짧은 해시, `last_updated` 는 판을 바꾼 마지막 날이다. `comparison` 은
`decodes-recordings` 줄에서 이 판으로 기준 녹음을 비교한 날이고, 다른 줄은 `-` 다. `nixpkgs ffmpeg` 의 `version` 은 `flake.lock` 의 nixpkgs 커밋
(ffmpeg 8.1.2)이다. `uv.lock` 의 `av` 세 항목은 `resolution-markers` 로 구별한다(17.1.0 은 `python_full_version < '3.11'`, 18.1.0 은 `== '3.11.*'`, 19.0.2 는 3.12 이상을 맡는다 — 항목의 `resolution-markers` 에 `== '3.12.*'`, `== '3.13.*'`, `>= '3.14'` 가 있다).

<!-- stt:versions -->

| component | role | version | pinned_in | since | last_updated | comparison |
|---|---|---|---|---|---|---|
| av (Python 3.12) | decodes-recordings | 19.0.2 | uv.lock | 2026-11-10 (c86fe6c) | 2026-11-10 | 2026-11-10 |
| av (Python 3.11) | cli-only | 18.1.0 | uv.lock | 2026-11-10 (c86fe6c) | 2026-11-10 | - |
| av (Python 3.10) | cli-only | 17.1.0 | uv.lock | 2026-07-31 (ad3d695) | 2026-07-31 | - |
| nixpkgs ffmpeg | cli-only | 148bab9c1c3c53136ecb44a6ea356a0ed5b39b06 | flake.lock | 2026-08-02 (13e2b00) | 2026-08-02 | - |
| faster-whisper | decoded-audio-only | 1.2.1 | uv.lock | 2026-07-31 (ad3d695) | 2026-07-31 | - |
| CTranslate2 | decoded-audio-only | 4.8.1 | uv.lock | 2026-07-31 (ad3d695) | 2026-07-31 | - |
| torch | decoded-audio-only | 2.13.0 | uv.lock | 2026-08-03 (47d6d56) | 2026-08-03 | - |
| pyannote.audio | decoded-audio-only | 4.0.7 | uv.lock | 2026-08-03 (47d6d56) | 2026-08-03 | - |

- `av (Python 3.12)`: 서버가 쓰는 휠(3.12 이상의 해석기를 맡는다). 안의 FFmpeg 는 9.0.2(`av.ffmpeg_version_info` 로 확인). 저장소의 잠금은 2026-11-10 에 18.0.0 에서
  올렸고, 배포된 서버 환경은 서버 배포(P2, T041) 전까지 `av 18.0.0`, `ffmpeg_version_info 8.1.2` 다(2026-11-09 확인).
  기준 녹음 비교(2026-11-10, 읽기 단위와 같은 해독으로 av 18.0.0 대 19.0.2 의 s16 출력): `clip60.m4a` 는 960,171표본이 바이트까지 같다.
  `hour.m4a` 는 앞 57,600,890표본이 같고 끝의 10표본이 조금 다르며(최대 74) 19.0.2 쪽이 124표본(7.75 ms) 짧다(18.0.0 은 57,601,024표본).
  원인은 측정으로 확인했다 — 두 판 모두 AAC 프레임 168,753개를 해독하는데, FFmpeg 9 는 마지막 프레임을 선언된 길이에 맞춰 잘라(48 kHz 에서
  1024 → 652표본, 16 kHz 로는 124표본) 출력이 선언된 3600초에서 정확히 끝나고, FFmpeg 8.1.2 는 인코더 끝 채움 372표본을 그대로 냈다.
  앞의 같은 구간은 바이트까지 같고 stt 의 해독이 빠뜨린 것은 없다(faster-whisper 의 같은 알고리즘도 판마다 같은 바이트를 낸다). 영향은 녹음 맨
  끝 7.75 ms 이하다. 운영자가 이 차이를 받아들였다(2026-11-10).
  `comparison` 날짜는 s16 `cmp` 만 뜻한다. 정책 4단계의 전사록·문단 목록 비교는 av 19 가 서버에 배포된 뒤(T041)에야 할 수 있어 T042 로 넘긴다.
- `av (Python 3.11)`: Python 3.11 의 명령줄에서만 쓴다. 안의 FFmpeg 는 8.1.2. 2026-11-10 전에는 3.12 와 같은 18.0.0 이었다.
- `av (Python 3.10)`: Python 3.10 의 명령줄에서만 쓴다(서버는 3.12). 안의 FFmpeg 는 8.1.1.
- `nixpkgs ffmpeg`: P2 뒤 서버의 GPU 단계는 `ffmpeg` 를 부르지 않는다. 운영자의 명령줄이 쓴다. 마지막 갱신일은 nixpkgs 커밋이 바뀐 날이다
  (`5afc555` 는 이 커밋을 바꾸지 않았다).
- `uv.lock` 은 2026-10-22(`eea5374`)에 바뀌었으나 위 판은 하나도 바뀌지 않았다.
- 후속 후보: 코덱 허용목록(쓸 수 있는 해독기 이름의 제한)은 넣지 않았다. 읽기 격리(P2)가 범위를 막는다(research R7).

### 4.2 갱신 정책

<!-- stt:policy owner=operator cadence=monthly advisory_days=7 sources=ffmpeg-security,pyav-releases,nixpkgs-ffmpeg -->

- **누가·언제**: 운영자가 매달 한 번 녹음 읽기에 쓰이는 구성 요소의 새 판이 있는지 확인한다. 확인한 날은 4.4 표에 적는다. 새 판이 있을 때만
  올린다.
- **볼 곳** (`sources`):
  - `ffmpeg-security` — FFmpeg 보안 공지, <https://ffmpeg.org/security.html>
  - `pyav-releases` — PyAV 새 판 소식, <https://github.com/PyAV-Org/PyAV/releases>
  - `nixpkgs-ffmpeg` — nixpkgs 의 `ffmpeg` 갱신, 예: `nix eval --raw github:NixOS/nixpkgs/nixos-unstable#ffmpeg.version`
- **보안 공지**: 서버의 판에 해당하는 공지가 나오면 7일 안에 올리거나, 올리지 않는 이유(이 서버의 판에 해당하지 않음 등)를 4.3 표의 `reason` 에
  적는다. 올렸으면 `updated` 에 날짜를 적는다.
- **휠이 아직 없을 때**: FFmpeg 보안 수정이 나왔는데 고친 판을 담은 새 PyAV 휠이 아직 없으면, `reason` 에 「배포된 휠 없음 + 읽기 격리(P2)가
  범위를 막음」을 적고 다음 달 확인에서 다시 본다.
- **올리는 순서**: (1) 잠금 파일 갱신(PyAV 는 `uv lock --upgrade-package av`, nixpkgs 는 `nix flake update nixpkgs`) → (2) 빌드·시험 →
  (3) 배포 → (4) 기준 녹음 비교(`cmp` 로 s16 비교, 전사록·문단 목록 비교) → (5) 4.1 표 고치기(`version`·`last_updated`·`comparison`).
  CI 시험이 지키는 것은 `av` 세 줄의 `version`(`uv.lock` 과 대조)과 `nixpkgs ffmpeg` 줄의 `version`(`flake.lock` 과 대조)뿐이다.
  `last_updated`·`comparison` 과 나머지 줄은 CI 가 아니라 운영자가 직접 고친다.
- 서버 경로의 해독기는 PyAV 휠의 FFmpeg 하나이므로 매달의 갱신은 보통 `av` 하나로 끝나고 nixpkgs 전체를 올릴 필요가 없다. `nixpkgs ffmpeg`
  의 공지는 서버 경로 밖(명령줄)이라는 이유로 올리지 않을 수 있고, 그 이유를 적는다.

### 4.3 보안 공지 기록

<!-- stt:advisories -->

| advisory | component | published | updated | reason |
|---|---|---|---|---|
| CVE-2026-30997 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-04-13 | 2026-11-10 | AV1 해독기(`libavcodec/av1dec.c`)의 범위 밖 읽기, FFmpeg 9.0 에서 고침. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 배포된 서버는 T041 까지 av 18.0.0 이므로 그때까지는 P1 의 호출 필터가 한도다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |
| CVE-2026-12706 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-06-19 | 2026-11-10 | RASC 영상 해독기의 해제 뒤 사용, 9.0 에서 고침. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 배포된 서버는 T041 까지 av 18.0.0 이므로 그때까지는 P1 의 호출 필터가 한도다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |
| CVE-2026-66038 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-07-24 | 2026-11-10 | LCL/ZLIB 영상 해독기의 초기화되지 않은 힙 노출, 8.1.2 까지 해당하고 9.0 에서 고침. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 배포된 서버는 T041 까지 av 18.0.0 이므로 그때까지는 P1 의 호출 필터가 한도다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |
| CVE-2026-70629 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-08-06 | 2026-11-10 | RSCC 해독기의 초기화되지 않은 힙 읽기, 9.0 미만이 해당. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 배포된 서버는 T041 까지 av 18.0.0 이므로 그때까지는 P1 의 호출 필터가 한도다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |
| CVE-2026-70631 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-08-06 | 2026-11-10 | TIFF 해독기의 초기화되지 않은 힙 노출, 9.0 미만이 해당. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 배포된 서버는 T041 까지 av 18.0.0 이므로 그때까지는 P1 의 호출 필터가 한도다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |

첫 확인(2026-11-09)에서 CVE-2026-8461·CVE-2026-30999 는 FFmpeg 8.1.2 에서 이미 고쳐졌고 CVE-2026-30998 은 `tools/zmqsend.c` 의 문제로 라이브러리에
들어 있지 않아, 서버의 판에 해당하지 않음을 확인하고 표에 올리지 않았다.

### 4.4 매달 확인 기록

<!-- stt:monthly-checks -->

| date | sources | result |
|---|---|---|
| 2026-11-09 | ffmpeg-security, pyav-releases, nixpkgs-ffmpeg | 새 판 있음 — PyAV 19.0.2(휠의 FFmpeg 9.0.2, 2026-10-21), nixpkgs nixos-unstable 의 ffmpeg 9.0.2. 서버 판(av 18.0.0, FFmpeg 8.1.2)에 해당하는 보안 공지 5건(4.3 표). 올리지 않음 — av 19 는 API 가 바뀌어(`stream_options` 제거, 유리수 속성이 `av.AVRational`) 별도 과제로 운영자가 정한다. 이 첫 확인은 운영자 지시로 Claude 가 했다. 운영자가 2026-11-10 에 올리기로 정해 저장소의 잠금을 PyAV 19.0.2 로 올렸다(T044, 서버 배포는 P2 와 함께). |
