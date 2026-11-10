# P2 회신 — 녹음 읽기 격리와 판 기록

이 문서는 [data-model.md](../data-model.md) §9 의 P2 열을 따른다. 운영자가 bhu-llm 대장 A18 로 옮긴다. P1 회신은 [p1-reply.md](p1-reply.md).

## 배포(T041)

- 2026-11-10: stt `master` 를 `be72910` 에서 `f41e921` 로 앞당겨 push 했다(운영자 승인). 운영자가 nixos-config 의 `stt` 입력을 `f41e921` 로
  갱신하고 `nxr` 했다. 배포 전 시험: Python 3.10·3.11·3.12 각 1194 통과, VM 시험(`server-vm`) 통과.
- 배포된 서버 환경: `av 19.0.2`, `ffmpeg_version_info 9.0.2`, systemd 262.

## 확인 결과(C-P2, T042 — 2026-11-10 13:08–13:28 KST)

### 설정(C-P2-1, [contracts/module.md](../contracts/module.md) §4 의 세 명령)

출력 그대로다. 단, `SystemCallFilter=` 의 풀린 호출 목록은 첫 몇 개만 싣는다(네 단위 392개, 읽기 단위 365개).

```text
User=stt
DynamicUser=no
PrivateDevices=no
SystemCallFilter=_llseek _newselect accept accept4 access add_key alarm arch_…
SystemCallArchitectures=native
MemoryDenyWriteExecute=yes

User=stt
DynamicUser=no
PrivateDevices=yes
SystemCallFilter=_llseek _newselect accept accept4 access add_key alarm arch_…
SystemCallArchitectures=native
MemoryDenyWriteExecute=yes

User=stt
DynamicUser=no
PrivateDevices=yes
SystemCallFilter=_llseek _newselect accept accept4 access add_key alarm arch_…
SystemCallArchitectures=native
MemoryDenyWriteExecute=yes

User=stt
DynamicUser=no
PrivateDevices=yes
SystemCallFilter=_llseek _newselect accept accept4 access add_key alarm arch_…
SystemCallArchitectures=native
MemoryDenyWriteExecute=yes
----
CollectMode=inactive-or-failed
RuntimeMaxUSec=5min
OOMPolicy=continue
MemoryMax=805306368
MemorySwapMax=0
LimitFSIZE=0
LimitCORE=0
SyslogLevel=4
DynamicUser=yes
InaccessiblePaths=-/var/lib/stt -/var/lib/stt-models -/run/stt -/nix/var -/mnt -/srv -/opt -/media
PrivateTmp=yes
PrivateDevices=yes
PrivateNetwork=yes
SystemCallFilter=_llseek _newselect accept accept4 access alarm arch_prctl ar…
SystemCallArchitectures=native
RestrictAddressFamilies=
MemoryDenyWriteExecute=yes
TemporaryFileSystem=/dev/shm:ro /run:ro /var:ro
KillSignal=9
----
SocketUser=stt
SocketMode=0600
Accept=yes
Listen=/run/stt-reader.sock (SequentialPacket)
MaxConnections=4
```

- 첫 묶음은 `stt-worker`·`stt-intake`·`stt-sweep`·`stt-models` 차례다. 네 단위는 P1 그대로 `User=stt` 이고, 읽기 단위만 `DynamicUser=yes`·
  `PrivateDevices=yes`·`PrivateNetwork=yes` 다. `SyslogLevel=4` 는 `warning` 이다. `systemctl --failed` 는 확인 내내 비어 있었다.

### 정상 녹음과 텍스트 첨부(C-P2-2, 인계 §6)

- 같은 때 맡긴 두 요청: 60초 녹음(`clip60.m4a`) → `done`(13:08:17 접수, 19초 만에 끝). 텍스트 한 줄을 `.m4a` 로 붙인 요청 →
  `unreadable_audio`(13:08:17).
- `journalctl -u 'stt-reader@*' -p warning`: 같은 시각(13:08:17)에 `reader child probe exited with status 1` 한 줄. 감독의 기록이 호스트에서 산다.
- 확인 내내(13:08 부터) `journalctl -k -g 'type=1326'` 0줄.
- 권한 점검(도는 읽기 인스턴스 안, `sudo nsenter -t <PID> -m ls`): `/run` 은 비었고, `/nix/var`·`/mnt`·`/srv`·`/opt` 는 권한 없는 빈 자리(`d---------`),
  `/media` 는 이 호스트에 없다. `/var` 에는 개인 `tmp` 하나뿐이다. `/var/lib/stt`·`/run/stt`·`/var/lib/stt-models` 는 없다.
- 호스트의 누구나 읽을 수 있는 잠금 파일(`find` 로 `*.lock` 등): 모두 `/var` 아래(`/var/lib/private`·`/var/lib/stt-models`)라 읽기 단위에서 보이지 않는다.
  `/etc/.pwd.lock` 은 0600 이다. 점검 대상에 더할 것은 없다.

### 해독 시간의 여유(C-P2-3)

배포된 서버 해석기(`stt-server-env`, av 19.0.2)로 3시간 사인파를 읽기 단위와 같은 해독(`write_s16`)으로 쟀다(`captest 1G 10m`).

| 형식 | 해독 시간 | 최대 RSS | cgroup 최대(파일 캐시 포함) |
|---|---|---|---|
| AAC(M4A) 128k | 10.8초 | 93 MB | 155 MiB |
| Opus 64k(Ogg) | 18.2초 | 78 MB | 56 MiB |

기대(해독 48초·메모리 153 MB 이하) 안이다. 해독 상한 240초는 가장 느린 것의 13배, 메모리 상한 768M 은 가장 큰 RSS 의 8배다. 상한은 고치지 않는다.

### 재전사와 「다 지우기」(C-P2-4)

- 60초 녹음 하나로 1차, 2차(화자 구분·힌트 4), 재전사(`from_request`) 3번 — 작업 5개 모두 `done`.
- 「다 지우기」(13:26:17) → `deleted`. 1분 뒤 그 녹음을 쓴 작업 5개와 1시간 녹음의 작업 2개 모두 녹음·결과 파일 0개다. 남은 것은 상태 기록
  (`deleted` 로 표시된 작업·녹음 기록)뿐이고 001 대로 그날 23:58 정리에서 지워진다. `work`·`queue`·inbox 는 비었다.

### 기준 녹음 비교(C-P2-5, SC-007)

- s16 비교(배포된 해석기의 PyAV 대 명령줄 `ffmpeg`): `clip60.m4a` 는 바이트까지 같다. `hour.m4a` 는 앞 57,600,890표본이 같고 끝 7.75 ms 만
  다르다. 이 끝 차이는 운영자가 이미 받아들였다(2026-11-10, `docs/server-security.md` §4.1 — FFmpeg 9 가 마지막 AAC 프레임을 선언된 길이에 맞춰 자른다).
- 전사록·문단 목록(기준 C-0 대 배포 뒤):

| 녹음·방식 | 문단 | 기준과 비교 |
|---|---|---|
| 60초 1차 | 1 | 같다(재전사 3번도 같다) |
| 60초 2차(화자 구분·힌트 4) | 1 | 같다 |
| 1시간 1차 | 37 | 마지막 문단(58:50)의 낱말 하나만 다르다: 「왕납니다」→「왕래입니다」 |
| 1시간 2차(화자 구분·힌트 7) | 57 | 같다. 힌트 반영 7/7 |

- `transcript.txt` 는 위 차이 말고는 머리의 원본 이름(요청 ID) 한 줄만 다르다.
- 명령줄(`stt transcribe`, Python 3.12 의 av 19)도 같은 해독(`stt.audio_decode`)을 쓰므로 같은 끝 차이를 갖는다. 운영자의 수락은 명령줄에도 적용된다.
- 1시간 1차의 낱말 하나는 녹음 길이가 3600.064 초에서 3600.056 초로 준 끝 차이에서 온 것으로 보고, 운영자가 받아들였다(2026-11-10).

### 처리 시간과 접수 확인(C-P2-6)

- 1시간 녹음 1차: 시작부터 끝까지 4분 25초(목표 10분). 2차: 5분 53초(목표 25분). 둘 다 대기 없이 돌았다.
- 접수 확인: 맡긴 그 초 안에 `accepted_at` 이 찍혔다(13:14:21 에 맡긴 1시간 녹음의 길이 재기 포함). 읽기 인스턴스를 띄우는 시간을 더해도 001 SC-011 의 「직후」 안이다.

### 실제 인스턴스 안(C-P2-7)

- 도는 읽기 인스턴스의 UID 는 61945 로 `stt`(986)와 다르다. `/dev` 에는 `core fd full hugepages log mqueue null ptmx pts random shm stderr
  stdin stdout tty urandom zero` 뿐이고 NVIDIA 장치가 없다.
- 운영자용 재확인 명령(`docs/server-security.md` §3 「안전한 재확인」)을 호스트에서 돌려 `ok 2.0 ok 2.0 s` 를 얻었다.
- 위 점검은 요청 없이 연결만 잡아 인스턴스를 붙들어 했다. 그 인스턴스는 감독의 줄 없이 systemd 262 의 `Main process exited,
  code=exited, status=1/FAILURE` 만 남겼다. 받기·워커는 연결하자마자 요청을 보내므로 평소에는 없는 줄이다. 뜻을 §3 에 적었다.

## 요청 1 — 읽기 단위 덧붙임

| 단위 | `SystemCallFilter` | `SystemCallArchitectures` | 더한 예외 | `MemoryDenyWriteExecute` |
|---|---|---|---|---|
| `stt-intake`·`stt-worker`·`stt-sweep`·`stt-models` | `@system-service` | `native` | 없음 | 켬 (P1 그대로) |
| `stt-reader@` | `@system-service` 에서 `@privileged`·`@keyring`·`process_vm_readv`·`process_vm_writev`·`io_uring_*`·`userfaultfd` 를 뺌 | `native` | 없음(더 좁혔다) | 켬 |

- 읽기 단위에서 뺀 것의 이유: 같은 동적 UID 를 쓰는 인스턴스끼리 커널 키링을 함께 쓰지 않게(`@keyring`), 다른 프로세스 메모리 읽기·쓰기와
  커널 공격면이 넓은 호출을 닫으려고. VM 시험이 허용한 형식 8가지를 실제 읽기 단위로 해독해 막힌 호출이 없음을 확인했고(FR-028), 호스트에서도
  확인 내내 `type=1326` 이 0줄이었다.
- 목록 밖 호출은 그 프로세스를 SIGSYS(31)로 끝낸다. 읽기 단위에서는 감독이 `reader child <probe|decode> killed by signal 31` 을 남긴다.

## 요청 2·3 — 배포된 것: 어느 단계가 어디서 도는가

- 길이 재기와 해독: 소켓으로 띄우는 일회용 단위 `stt-reader@.service` 안에서만 PyAV(FFmpeg 9.0.2)가 녹음 바이트를 해석한다. 연결 하나가 인스턴스
  하나이고, 끝나면 지워진다. 동적 UID(`stt` 아님), GPU 장치 없음, 망 없음·소켓 생성 없음, 다른 프로세스 보이지 않음, 메모리 768M·스왑 0, 시간 상한
  (길이 재기 30초·해독 240초, 받침으로 `RuntimeMaxSec=300`).
- 받기·워커는 녹음을 열어 **읽기 전용 기술자**만 넘긴다. 읽기 단위는 경로를 쓰지 않고 `/var/lib/stt`·`/var/lib/stt-models`·`/run/stt` 에 닿지
  못한다. `/run`·`/var`·`/dev/shm` 은 빈 읽기 전용 tmpfs 로 덮였고, `/nix/var`·`/mnt`·`/srv`·`/opt`·`/media` 는 가렸다(호스트에서 위와 같이 확인).
- GPU 단계(워커의 엔진)는 해독된 16 kHz 음성만 받는다. 녹음을 해독하지 않고, 화자 구분용 WAV 변환의 `ffmpeg` 명령도 더는 부르지 않는다.
- 요청 3(작업별 저장 분리)에 대한 답은 P1 회신대로다. 저장 위치를 작업마다 나누지 않는다. 대신 녹음을 해석하는 프로세스가 자기 녹음 하나의
  기술자 말고는 저장 위치에 닿지 못한다. 장악된 읽기가 그날 다른 이용자의 녹음·전사록을 읽거나 바꾸는 길은 닫혔다. 이것을 호스트에서 확인했다.

## 요청 4 — 정책과 첫 매달 확인

- 판 기록·갱신 정책·보안 공지 기록·매달 확인 기록은 `docs/server-security.md` §4 에 있다. 서버 경로에서 녹음 바이트를 해석하는 구성 요소는 PyAV 휠의
  FFmpeg 하나뿐이다.
- 정책: 운영자가 매달 FFmpeg 보안 공지·PyAV 새 판·nixpkgs `ffmpeg` 를 본다. 서버 판에 해당하는 보안 공지가 나오면 7일 안에 올리거나,
  올리지 않는 이유를 적는다.
- 첫 매달 확인(2026-11-09): 서버 판(FFmpeg 8.1.2)에 해당하는 공지 5건(CVE-2026-30997·12706·66038·70629·70631, 모두 영상·이미지 해독기,
  9.0 에서 고침). 운영자가 올리기로 정했고, 이 배포로 서버는 av 19.0.2·FFmpeg 9.0.2 가 됐다. 배포 뒤 기준 녹음 비교는 위와 같다.

## §5 질문

- 계약: 요청·응답·결과 파일의 모양과 경로, 「다 지우기」의 뜻, `from_request`, 23:58 정리는 바뀌지 않았다. 호스트에서 재전사 3번과
  「다 지우기」를 다시 확인했다.
- 조작 파일: 녹음 확장자를 붙인 텍스트는 P2 뒤에도 `unreadable_audio` 로 끝나고, 다른 작업·단위는 영향이 없다(호스트 확인).
- **새로 알릴 것 — `done` 이 녹음 앞부분만일 수 있다(경계 사례 10)**: 녹음 가운데에 깨진 조각이 있으면 해독은 첫 깨진 조각에서 멈춘다. 그 앞까지만
  전사하고 작업은 `done` 으로 끝난다(faster-whisper 와 같은 동작, 운영자 결정 2026-11-10). 첫 조각부터 깨졌으면 전사록이 빈 채 `done` 이다.
  응답과 결과의 모양은 그대로다. 알아보는 방법:
  - 결과의 `summary.audio_duration_s` 는 실제로 해독된 길이다. 이 값이 그 녹음의 원래 길이(올린 쪽이 아는 길이)보다 뚜렷이 짧으면 앞부분만 전사된
    것이다. 마지막 문단의 `end` 도 그 근처에서 끝난다.
  - 서버 쪽에서는 그 작업 시각에 `journalctl -u 'stt-reader@*' -g 'reader child'` 의 `reader child decode stopped early at invalid data` 가 있다. 운영자에게
    시각을 알려 주면 확인할 수 있다.
- 단위 비정상 종료와 작업 실패의 구별: P1 회신대로이고, 읽기 단위의 몫이 더해졌다. 감독의 고정된 영어 줄 다섯 가지(종료 코드·신호·일찍 멈춤·시간 상한·
  엉터리 결과)와 systemd 의 OOM·시간 상한 줄은 운영자 확인 명령
  `journalctl -u 'stt-reader@*' -g 'reader child|oom|runtime time limit|main process exited'` 로 본다. 읽기 인스턴스는 치워지므로 `systemctl --failed`
  에 쌓이지 않는다. 자세한 표는 `docs/server-security.md` §3 에 있다.

## 남는 위험

- **P1 회신 바로잡기**: [p1-reply.md](p1-reply.md) 「남는 위험」의 「망이 없어 밖으로 빼낼 길이 없는 것은 그대로다」는 틀렸다. 망으로 나가는 길은 없다
  (`PrivateNetwork`). 하지만 장악된 해독기는 읽은 내용을 **자기 작업의 전사록**으로 내보낼 수 있다. 읽은 내용을 음성으로 합성해 두면 Whisper 가
  받아 적고, 그 전사록이 그 작업을 맡긴 쪽에 돌아간다. 느리고, 그 작업의 호출자에게만 간다.
- **P2 뒤에도 남는 것(운영자가 다시 받아들임, 2026-11-10)**: 읽기 단위는 닫은 경로 밖의, 호스트에서 누구나 읽을 수 있는 파일(`/etc`·`/nix/store`·
  `/usr`)을 읽을 수 있다. 장악되면 위의 길로 그 내용을 자기 작업의 전사록으로 내보낼 수 있다. 다른 이용자의 녹음·전사록, 모델, 서버 상태, 홈,
  `/run`·`/var`·`/nix/var`, 그리고 자료 디스크(`/mnt`)와 `/srv`·`/opt`·`/media` 에는 닿지 않는다. 운영자가 2026-11-09 에 받아들였고,
  전사록으로 나갈 수 있다는 것까지 알고 2026-11-10 에 다시 받아들였다. 같은 날 `/mnt`·`/srv`·`/opt`·`/media` 를 가리기로 해 잔여 노출이 시스템 경로
  쪽으로 좁혀졌다.
- 감독의 기록 줄은 장악된 자식이 속일 수 있다. 운영자에게 알리는 신호일 뿐 보안 통제가 아니다.
- 낮은 위험으로 따로 미룬 개선점은 stt 쪽 기록에 남겼다. 이 회신의 계약·격리에는 영향이 없다.
