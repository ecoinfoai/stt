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
- 파일: `/var/lib/stt`(다른 작업의 녹음·기록·해독된 음성·응답·`state.lock`)와 `/var/lib/stt-models` 는 권한(일회용 UID 는 `stt` 가 아니다)으로 막히고,
  `/var` 를 덮은 빈 tmpfs(아래)에서는 경로 자체가 없다. `/run/stt`(0755)와 그 안의 `gpu.lock`(0644)은 권한으로는 읽을 수 있으므로 `/run` 을 덮은 빈 tmpfs(아래)가 막는다
  (research R12). 설정은 두 번 적혀 있지만(`InaccessiblePaths` 의 `-/var/lib/stt`·`-/var/lib/stt-models`·`-/run/stt` 와 tmpfs) 실제로 가리는 것은 tmpfs 다. tmpfs 아래에서는
  이 세 경로가 없어 `-` 때문에 건너뛰어진다. 이 항목들은 기본이 아닌 `stateDir`·`modelsDir` 를 쓰거나 tmpfs 항목이 빠질 때(호스트의 `lib.mkForce` 등)에만 일을
  한다. 그런 덮어쓰기를 잡는 호스트 쪽 시험은 없다.
- 쓰기: 이 단위에서 쓰기가 되는 자리는 인스턴스마다 따로인 사설 `/tmp`·`/var/tmp` 뿐이고, 거기에도 파일을 만들 수는 있어도 자료는 쓰지
  못한다(`LimitFSIZE=0`). 현재 폴더를 비롯한 그 밖은 `ProtectSystem=strict` 로 읽기 전용이다. `/dev/shm` 은 `PrivateDevices=yes` 에도 호스트의 것과 같아(같은 장치·아이노드, 1777,
  만든 파일이 호스트에 남음) 읽기 전용 빈 tmpfs 로 덮는다(`TemporaryFileSystem=/dev/shm:ro`).
- 호스트의 잠금 파일: `/run` 과 `/var` 는 빈 읽기 전용 tmpfs 로 덮고(`TemporaryFileSystem=/dev/shm:ro /run:ro /var:ro`) `/nix/var` 는 가린다
  (`InaccessiblePaths` 의 `-/nix/var`). `flock` 은 읽을 수 있는 기술자만 있으면 읽기 전용 마운트에서도 걸리므로, 누구나 읽을 수 있는 호스트의 잠금 파일
  (`/run/nixos/switch-to-configuration.lock`, `/nix/var/nix/db` 아래의 파일)을 장악된 읽기가 붙잡아 배포를 막는 길을 닫았다(최종 감사 H1, research R12).
  읽기는 `/run` 아래의 것을 쓰지 않는다. 가리는 경로는 모두 `-` 를 붙여, 없는 경로가 단위를 실패시키지 않는다. VM 시험의 권한 점검이 이 파일들을 열지도
  잠그지도 못함을 본다.
- 호스트의 자료 디스크와 덧붙인 설치 자리: `/mnt`·`/srv`·`/opt`·`/media` 도 `InaccessiblePaths` 로 가린다(`-/mnt -/srv -/opt -/media`. 운영자 결정 2026-11-10,
  재감사 RM2·RL1·RL2). 읽기는 이 아래에서 아무것도 필요로 하지 않는다. 자료 디스크의 누구나 읽을 수 있는 파일(대용량 보관 파일 포함)이 장악된 읽기의 읽기 범위에서
  빠진다. 그 자리의 파일을 열어 둔 채 호스트의 `umount` 가 `EBUSY` 로 실패하게 하는 길(마운트 붙들기)도, 읽기가 그 아래를 열 수 없으므로 닫힌다고 본다(추론이다. 실제 `umount` 로 확인하지는 않았다). VM 시험의 권한 점검이 네 자리 아래의 0644 root 파일을
  열지도 잠그지도 못함을 본다.
- 호스트의 다른 파일(남는 위험): `/mnt`·`/srv`·`/opt`(와 `/media`)는 가렸다. 남는 것은 `/etc`·`/nix/store`·`/usr` 의 누구나 읽을 수 있는 파일이다. 이 경로 밖에서 누구나 읽을 수 있는 파일은 읽기 단위도 읽을 수 있다. 장악된 읽기는
  그 내용을 자기 작업의 전사록으로 내보낼 수 있다 — 읽은 내용을 녹음 속 음성으로 합성해 두면 Whisper 가 받아 적고 전사록이 그 작업의 호출자에게 돌아간다
  (느리다. 다른 이용자의 녹음·전사록은 닿지 않는다). 망으로 나가는 길은 없다(`PrivateNetwork`). 홈은 `ProtectHome=true` 로 닫혔다. 이 호스트에서는 다른
  서비스의 상태가 `/var/lib/private`(0700)에 있어 일회용 UID 로는 들어가지 못하고, `/var` 는 위에서 덮었다.
  운영자 결정(2026-11-09): 이 잔여 노출을 받아들이고 모듈은 바꾸지 않으며 나중에 다시 검토한다. 운영자가 읽은 내용이 전사록으로 나갈 수 있다는 것까지 알고
  다시 받아들였다(2026-11-10). 같은 날 재감사가 이 호스트의 자료 디스크(`/mnt` 아래, 누구나 읽을 수 있는 대용량 보관 파일)가 이 기록에 없음을 짚었고, 운영자가
  `/mnt`·`/srv`·`/opt`(와 `/media`)를 읽기 단위에서 가리기로 정했다(2026-11-10). 위 「자료 디스크」 항목이 그 결과다. 잔여 노출은 시스템 경로 쪽으로 좁혀졌다.
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

**끊기 신호와 손 실행.** 감독은 시작하자마자 SIGINT 를 무시한다. 해독 자식이 감독에게 SIGINT 를 보내도 감독은 자식의 끝을 기다려 평소의 판정과 줄을 낸다.
운영자가 터미널에서 `stt server read` 를 손으로 띄우면 표준 입력이 연결된 소켓이 아니라서 아무것도 읽지 않고, 표준 오류에 아래 한 줄을 쓰고 끝 코드 2 로 끝난다(표준 출력에는 아무것도 없다).

```text
stt server read only serves a connection that systemd passes from stt-reader.socket on standard input
```

해독 자식과 엔진은 `python -I` 로 떠서 현재 폴더를 모듈 찾는 길에 넣지 않는다.

### 2.1 해독기 목록

서버 경로(받을 때의 길이 재기와 처리 때의 해독)는 아래 음성 해독기만 연다. 영상·이미지·자막 해독기는 녹음 상자에 그 트랙이 있어도 열리지 않는다 — 분석
(`find_stream_info`)과 해독 모두 FFmpeg 의 `codec_whitelist` 로 거절된다. 영상 트랙이 든 녹음(영상 강의의 MP4, 표지 그림이 든 MP3)은 음성만 읽는다(운영자 결정
2026-11-10). 영상 트랙만 든 녹음과, 목록 밖 음성 코덱의 녹음은 받을 때 `unreadable_audio` 다. 명령줄(`stt`)의 해독은 이 목록을 쓰지 않는다.

<!-- stt:audio-decoders -->

| decoder | codec | formats |
|---|---|---|
| `aac` | AAC (LC, HE) | m4a, aac |
| `alac` | Apple Lossless | m4a |
| `flac` | FLAC | flac, ogg |
| `mp3float` | MP3 | mp3 |
| `mp2` | MPEG Layer II | mp3, wav |
| `opus` | Opus | opus, ogg |
| `vorbis` | Vorbis | ogg |
| `wmav1` | WMA 1 | wma |
| `wmav2` | WMA 2 | wma |
| `wmapro` | WMA Pro | wma |
| `wmavoice` | WMA Voice | wma |
| `wmalossless` | WMA Lossless | wma |
| `ac3` | AC-3 | m4a |
| `eac3` | E-AC-3 | m4a |
| `amrnb` | AMR-NB | m4a |
| `amrwb` | AMR-WB | m4a |
| `adpcm_ima_wav` | IMA ADPCM | wav |
| `adpcm_ms` | Microsoft ADPCM | wav |
| `pcm_alaw` | G.711 A-law | wav, m4a |
| `pcm_f32be` | PCM float 32 big-endian | m4a |
| `pcm_f32le` | PCM float 32 little-endian | wav, m4a |
| `pcm_f64be` | PCM float 64 big-endian | m4a |
| `pcm_f64le` | PCM float 64 little-endian | wav, m4a |
| `pcm_mulaw` | G.711 mu-law | wav, m4a |
| `pcm_s16be` | PCM 16-bit big-endian | m4a |
| `pcm_s16le` | PCM 16-bit little-endian | wav, m4a |
| `pcm_s24be` | PCM 24-bit big-endian | m4a |
| `pcm_s24le` | PCM 24-bit little-endian | wav, m4a |
| `pcm_s32be` | PCM 32-bit big-endian | m4a |
| `pcm_s32le` | PCM 32-bit little-endian | wav, m4a |
| `pcm_s8` | PCM 8-bit signed | m4a |
| `pcm_u8` | PCM 8-bit unsigned | wav, m4a |

인코더가 없어 끝까지 읽어 보지 못한 이름은 `wmapro`·`wmavoice`·`wmalossless`·`amrwb` 넷이다(시험 파일을 밖에서 받지 않는다). 그런 녹음이 처음 올 때 운영 확인으로
본다.

**남는 범위.** 분석기(parser)와 형식(demuxer)은 목록이 막지 않는다. 길이 재기의 분석 중 영상 분석기가 영상 묶음 몇 개를 읽을 수 있고, 해독 중에도 영상 묶음이
형식·분석기를 지난다(research R3 — 해독 단계에서 영상 묶음을 버리는 안은 운영자가 미뤘다, 2026-11-10). 공지가 분석기·형식에 관한 것이면 지금처럼 판정한다.

**바꾸는 규칙.** 목록에 이름을 더하거나 빼면 상수(`reader.AUDIO_DECODERS`)·이 표·형식별 시험을 함께 바꾼다. 표와 상수가 어긋나면 `tests/test_server_security_doc.py` 가 실패한다.

## 3. 작업 실패와 단위 비정상 종료 구별

새 알림 길은 없다. 운영자는 다음 두 곳을 구별해서 본다.

- **작업 하나의 실패**: 응답의 `failure.reason`, 그리고 단위 로그의 `<request_id> failed <reason>` 줄.

  ```sh
  journalctl -u stt-worker -u stt-intake
  ```

  `failure.reason` 이 `engine_error` 인 작업의 줄에는 늘 원인 이름이 하나 붙는다: `<request_id> failed engine_error <cause>`. 다른 이유의 줄은
  `<request_id> failed <reason>` 그대로다. 줄에는 경로·파일 이름·오류 문구·errno 가 없고 분류 이름만 있다. 응답의 `failure.detail` 은 이름과 관계없이
  고정 문장 그대로다(읽기 단계의 셋은 「The server could not start the audio reader.」, `engine_failed` 는 「The engine stopped because of an unexpected
  error.」, `engine_no_verdict` 는 「The engine stopped without a verdict.」).

<!-- stt:engine-error-causes -->

| cause | stage | 뜻 | 볼 것 |
|---|---|---|---|
| `reader_unavailable` | read | 읽기 단위를 쓸 수 없었다: 소켓 없음, 접속 거부, 「시작」 전 끊김, 「시작」 기다림 넘김. 녹음 탓이 아니다. | `systemctl status stt-reader.socket`, `journalctl -u 'stt-reader@*'` |
| `recording_unusable` | read | 워커가 그 작업의 녹음을 일반 파일로 열지 못했다(없음, 심볼릭 링크, 디렉터리·FIFO, 권한, 기술자 부족). 읽기 단위에는 아무것도 보내지 않았다. | 그 작업의 녹음 폴더(`/var/lib/stt/audio/<audio_id>/`)가 있는지, 일반 파일·`stt` 소유·0600 인지. 녹음을 열지는 않는다 |
| `unexpected` | read | 읽기 단계의 그 밖의 입출력·값 오류(파이프·소켓 만들기, 「시작」 뒤의 입출력 오류, 작업 기록 읽기). | 같은 시각의 워커 저널(기술자·메모리), `/var/lib/stt` 의 기록 |
| `engine_failed` | gpu | 엔진이 예상 못 한 오류(`engine_error`)로 멈췄다고 판정을 썼다. | 같은 시각의 GPU·모델 상태. 같은 녹음을 재전사해 되풀이되는지 |
| `engine_no_verdict` | gpu | 엔진이 쓸 만한 판정 없이 끝났다(판정 파일 없음·깨짐, 결과 파일 빠짐, 목록 밖 이유). | `systemctl status stt-worker`, `journalctl -k`(엔진 프로세스가 죽었는지) |

읽기 단계의 어떤 실패도 워커를 멈추지 않는다. 그 작업만 위 표대로 끝나고 워커는 다음 작업으로 간다.

**끝이 잘린 녹음.** 받을 때 잰 길이보다 해독된 길이가 1초 이상 짧으면 워커가 표준 출력에 아래 고정된 영어 한 줄을 쓴다. 읽기가 `ok` 이고 GPU 단계 전이다. 작업은 앞부분 전사로
`done` 이 되고 응답·결과는 같다. 줄에는 작업 ID·경로·파일 이름·길이 값이 없다.

<!-- stt:truncation threshold_s=1.0 -->

```text
decoded audio is shorter than the probed length
```

```sh
journalctl -u stt-worker -g ' running$|shorter than the probed length'
```

- 이 줄 바로 앞의 `<request_id> running` 줄이 그 작업이다(워커는 한 번에 한 작업만 한다). 한 작업에 줄은 많아야 하나이고, 같은 녹음을 재전사하면 작업마다 하나씩 남는다.
- 알아보는 형식: 머리가 전체 길이를 적는 녹음 — 색인이 앞인 M4A, Xing·Info 머리가 있는 MP3, FLAC, 대부분의 WMA. 알아보지 못하는 형식: WAV·Ogg·ADTS AAC(길이를 파일 자체에서
  재서 잘리면 처음 잰 길이도 같이 줄어든다). 색인이 뒤인 M4A(휴대폰 녹음 대부분)는 끝이 잘리면 받을 때부터 `unreadable_audio` 다.
- 잘리지 않았는데 줄이 남는 경우: 처음 잰 길이가 추정값인 녹음(머리 없는 가변 비트율 MP3, ADTS AAC — 앞부분이 조용하면 길게 추정된다)과 컨테이너의 다른 트랙(영상)이 음성보다 긴
  녹음이다. 줄은 「해독된 길이가 처음 잰 길이보다 짧다」는 사실만 말하며, 녹음이 잘렸다는 증거는 아니다.
- 깨진 조각에서 일찍 멈춘 해독은 읽기 단위의 `reader child decode stopped early at invalid data` 와 이 줄이 함께 남을 수 있다(첫 조각부터 깨져 해독된 길이가 0 이어도 같다).

- **서버 단위의 비정상 종료**: 실패한 단위 목록과 종료 원인. 메모리 상한(OOM)이면 `Result=oom-kill`, 호출 필터에 걸렸으면 SIGSYS(31)이다. 읽기 인스턴스는
  예외다: 단위에 `OOMPolicy=continue` 가 있어 해독 자식만 메모리 상한에 죽으면 인스턴스는 정상으로 끝난다(아래 「읽기 인스턴스의 비정상 종료」).
  막힌 호출 번호는 커널 기록에 있다.

  ```sh
  systemctl --failed
  systemctl show -p Result -p ExecMainCode -p ExecMainStatus <unit>
  journalctl -k -g 'type=1326'
  ```

- **읽기 단위를 쓸 수 없을 때**: 읽기 소켓이 없거나 인스턴스가 뜨지 못하면 받기·워커는 녹음을 직접 해독하는 대체 길 없이 그 요청·작업만 실패로
  끝낸다. 보이는 것은 다음과 같다.
  - 워커 로그의 `<request_id> failed engine_error reader_unavailable`(응답의 `failure.reason` 도 `engine_error`).
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
  파일 이름·해독기 문구는 없다. 정상 판정에는 아무것도 쓰지 않는다. 줄은 경고 수준(`SyslogLevel=warning`)이라 `journalctl -p warning` 에도 나온다. 장악된 자식은 이 줄을 속일 수 있으므로 운영자에게 알리는 신호일 뿐 보안 통제가 아니다.

  ```text
  reader child <probe|decode> exited with status <n>
  reader child <probe|decode> killed by signal <n>
  reader child decode stopped early at invalid data
  reader child <probe|decode> timed out
  reader child probe returned an invalid result
  ```

  손으로 `stt server read` 를 띄웠을 때만 나오는 줄이 하나 더 있다. 읽기 단위 안에서는 표준 입력이 늘 연결된 소켓이라 운영 중에는 나오지 않는다. 이 줄에도 작업 ID·경로·파일 이름이 없다.

  ```text
  stt server read only serves a connection that systemd passes from stt-reader.socket on standard input
  ```

  ```sh
  journalctl -u 'stt-reader@*' -g 'reader child|oom|runtime time limit|main process exited'
  ```

  `reader child` 는 감독의 줄(경고 수준)이고, 나머지 셋은 systemd 자신이 그 인스턴스 이름으로 쓰는 줄이다. `-p warning` 을 붙이지 않는다: systemd 의 OOM 줄
  (`The kernel OOM killer killed some processes in this unit.`)은 notice 수준이라 `-p warning` 에 가려진다(이 호스트의 systemd 262 저널에서 확인). 패턴을 소문자로 쓰면
  `journalctl -g` 는 대소문자를 가리지 않는다. 시각 범위는 `--since` 로 좁힌다. 감독의 줄만 훑을 때는 `journalctl -u 'stt-reader@*' -p warning -g 'reader child'` 가 된다.
  VM 시험은 이 명령(커서 이후로 좁힘)의 출력으로 아래 두 경우를 확인한다.
  - `killed by signal 31` 은 시스템 호출 필터(SIGSYS), `killed by signal 9` 는 메모리 상한(OOM)이거나 밖에서 죽인 것이다. 읽기 단위에는 `OOMPolicy=continue` 가 있다. 메모리 상한이
    죽이는 것은 해독 자식이고, 기본 정책(`stop`)이면 관리자가 단위를 멈추며 감독도 죽여 `failed` 를 보내고 줄을 쓰기 전에 끝날 수 있기 때문이다(재감사 RM1). `continue` 면 감독이
    살아 둘 다 하고, 같은 인스턴스 이름으로 systemd 의 `The kernel OOM killer killed some processes in this unit.` 도 저널에 온다(VM 시험이 진짜 감독과 메모리만 쓰는 자식으로
    systemd 261.1 에서 확인했다. 262 의 `libsystemd-core` 에도 같은 문구가 있다. 호스트에서는 메모리 상한에 걸려 본 적이 없다 — 2026-11-10 호스트 확인에서 본 systemd 262 의 줄은 아래
    「요청 없이 끊긴 연결」의 줄이다).
  - 감독 자신이 메모리 상한에 죽은 경우(자식이 아니라 감독이 가장 큰 프로세스일 때)는 감독의 줄이 없고 systemd 의 줄만 남는다: `The kernel OOM killer killed some processes in this
    unit.`, `Main process exited, code=killed, status=9/KILL`, 그리고 `Failed with result 'signal'`(`OOMPolicy=continue` 에서는 `oom-kill` 이 아니다). 위 명령이 앞의 둘을 보여 준다.
    호출한 쪽에는 연결 끊김이라 작업은 같은 `unreadable_audio` 다. VM 시험이 감독을 대신하는 메모리 대역으로 이 줄들을 확인한다.
  - 감독이 시간 상한에 죽인 자식은 `timed out` 이다. 단위의 `RuntimeMaxSec` 가 cgroup 전체(감독 포함)를 죽인 경우는 감독의 줄이 남지 않는다. 그때는 systemd 가 인스턴스 이름으로
    `Service reached runtime time limit. Stopping.` 을 쓴다(systemd 261·262 의 `libsystemd-core` 에서 문구를 확인했고, 실제로 인스턴스가 상한에 걸리게 해 본 적은 없다). 읽기 인스턴스는
    `CollectMode` 로 치워져 `systemctl --failed` 에 쌓이지 않으므로, 감독의 줄과 이 systemd 줄이 단위 수준의 흔적이다.
  - 연결만 하고 요청을 보내지 않은 채 끊거나 「시작」 기다림(30초)을 넘긴 연결은 감독의 줄 없이 systemd 의 `Main process exited, code=exited, status=1/FAILURE` 만 남는다
    (2026-11-10 호스트의 systemd 262 에서 확인). 받기·워커는 연결하자마자 요청을 보내므로 평소에는 없는 줄이다. 소켓은 `stt` 만 열 수 있으니(0600), 운영자의
    시험이 아니라면 `stt` 권한의 프로세스가 이상한 것이다. 요청을 받은 뒤 호출하는 쪽이 끊은 경우는 끝 코드 0 이라 이 줄도 없다.
  - 가장 흔한 줄은 `reader child probe exited with status 1` 이다. 읽을 수 없는 첨부(텍스트 파일, 깨진 머리, 목록 밖 형식, 목록 밖 음성 코덱)마다 길이 재기 자식이 끝 코드 1 로 끝나며 이 줄이 나온다.
    그 요청은 `unreadable_audio` 로 거절되고 서버는 정상이다. 이 줄이 가끔 있는 것은 사고가 아니다. 같은 줄이 갑자기 많아지거나 정상 녹음에서 나올 때만 본다.
  - `timed out` 은 감독이 시간 상한(길이 재기 30초·해독 240초)에 자식을 죽였다는 뜻이다. `returned an invalid result` 는 길이 재기 자식이 정상으로 끝났지만 결과가
    규격에 맞지 않았다는 뜻이다. 호출하는 쪽이 먼저 끊은 경우(다 지우기 등)는 정상이라 줄이 없다.
  - `stopped early at invalid data` 는 오류가 아니다: 녹음 가운데의 깨진 조각에서 해독이 멈췄고, 그 앞까지 전사해 작업은 `done` 으로 끝난다(faster-whisper 와
    같은 동작, 운영자 결정 2026-11-10). 첫 조각부터 깨졌으면 전사록이 빈 채 `done` 이다. 명령줄은 같은 일에 표준 오류로 경고 한 줄을 낸다.
  - 한 녹음에서만 `killed by signal 31`(또는 `type=1326`)이 나오고 같은 형식의 다른 녹음은 읽힌다면, 해독기를 노린 녹음을 필터가 막은 것일 수 있다. 그 녹음을
    직접 열지 말고(아래 규칙) 시각으로 요청을 찾아 호출하는 쪽에 알린다. 이 줄과 `type=1326` 은 길이 재기(`probe`)에서도 같은 모양으로 나온다.
  - 이 줄에는 작업 ID 가 없다. 어느 작업인지는 줄의 시각을 받기·워커 기록(`journalctl -u stt-intake -u stt-worker`)의 시각과 맞춰 찾는다. 커널 기록
    `journalctl -k -g 'type=1326'` 의 시각도 같은 방법으로 맞춘다.
  - `type=1326` 의 있고 없음보다 감독의 줄(`killed by signal 31`)을 믿는다. 장악된 자식이 손자 프로세스를 여럿 띄워 막힌 호출을 되풀이하면 커널의 출력 속도 제한이 다른 인스턴스의
    `type=1326` 줄을 떨어뜨릴 수 있다. 감독의 줄은 따로 쌓이므로 남는다(재감사 RL7).
- **한 형식이 늘 `unreadable_audio` 일 때**: 같은 확장자의 정상 녹음이 모두 `unreadable_audio` 로 끝나면 녹음 탓이 아니라 그 형식의 해독 경로가 시스템 호출 필터나
  메모리 상한에 막힌 것일 수 있다. 위의 `journalctl -k -g 'type=1326'` 에 읽기 UID 의 줄이 있는지, `reader child decode killed by signal 31`(길이 재기가 막혔으면 `reader child probe killed by signal 31`)이 같은 시각에 있는지 본다.
  VM 시험이 `AUDIO_FORMATS` 의 형식마다 사인파 녹음 하나를 실제 읽기 단위로 해독해 이 기록이 비어 있음을 확인하지만(FR-028), 실제 코덱 경로는 FFmpeg 판에
  따라 달라질 수 있다.
- **서버 녹음을 직접 열지 않는다**: `/var/lib/stt/audio/` 의 녹음을 `stt transcribe`·`ffprobe`·`ffmpeg` 로 직접 열지 않는다. 명령줄은 격리되지 않아(FR-022) 모든
  FFmpeg 형식을 열고, 해독기를 노리는 파일이면 운영자(root) 또는 `stt` 권한으로 모든 이용자의 녹음·GPU·망이 닿는 곳에서 돈다. 읽히지 않는 녹음을 다시 확인하려면
  읽기 단위를 거친다.
- **안전한 재확인**: 읽기 단위가 정상 녹음을 읽는지, 형식 하나가 깨졌는지는 사고 중에도 `stt` 사용자로 읽기 소켓을 거쳐 확인할 수 있다. 아래는 VM 시험의 `readFormats` 와 같은 길이다
  (`stt` 가 `reader_client` 로 기술자만 넘기고, 해독은 샌드박스 안에서 된다). 서버 안의 녹음이 아니라 운영자가 만든 알려진 정상 파일(아래 첫 줄의 2초 사인파)을 `stt` 가 읽을 수 있는 자리에 둔다. `-I` 는 현재 폴더를 모듈 찾는 길에서 뺀다.

  ```sh
  ffmpeg -nostdin -loglevel error -f lavfi -i "sine=frequency=440:duration=2" -ac 1 -ar 44100 -c:a aac -f ipod /tmp/known-good.m4a
  chmod 0644 /tmp/known-good.m4a
  BIN=$(systemctl cat stt-intake.service | sed -n 's|^ExecStart=\(.*\)/stt server .*|\1|p')
  sudo -u stt "$BIN/python" -I - /tmp/known-good.m4a <<'EOF'
  import sys
  from stt.server.reader_client import ReaderConfig, decode, probe
  config = ReaderConfig()
  for path in sys.argv[1:]:
      probed = probe(config, path)
      decoded = decode(config, path, job_running=lambda: True, job_limit_s=240)
      print(path, probed.outcome, probed.duration_s, decoded.outcome, len(decoded.audio) // 2 / 16000, "s")
  EOF
  ```

  `ok ... ok` 와 맞는 길이가 나오면 그 형식의 읽기 길은 산다. 그 형식만 `failed` 면 위의 줄(`journalctl -u 'stt-reader@*'`)과 `type=1326` 을 본다. 의심스러운 서버 녹음은 이 방법으로도
  넣지 않는다 — 알려진 정상 파일만 쓴다(2026-11-10 호스트에서 이 스니펫이 `ok 2.0 ok 2.0 s` 를 냈다).

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
  올렸고, 서버에는 2026-11-10 의 P2 배포(`f41e921`)로 들어갔다(배포된 서버 환경에서 `av 19.0.2`, `ffmpeg_version_info 9.0.2` 확인. 그 전은 18.0.0·8.1.2).
  기준 녹음 비교(2026-11-10, 읽기 단위와 같은 해독으로 av 18.0.0 대 19.0.2 의 s16 출력): `clip60.m4a` 는 960,171표본이 바이트까지 같다.
  `hour.m4a` 는 앞 57,600,890표본이 같고 끝의 10표본이 조금 다르며(최대 74) 19.0.2 쪽이 124표본(7.75 ms) 짧다(18.0.0 은 57,601,024표본).
  원인은 측정으로 확인했다 — 두 판 모두 AAC 프레임 168,753개를 해독하는데, FFmpeg 9 는 마지막 프레임을 선언된 길이에 맞춰 잘라(48 kHz 에서
  1024 → 652표본, 16 kHz 로는 124표본) 출력이 선언된 3600초에서 정확히 끝나고, FFmpeg 8.1.2 는 인코더 끝 채움 372표본을 그대로 냈다.
  앞의 같은 구간은 바이트까지 같고 stt 의 해독이 빠뜨린 것은 없다(faster-whisper 의 같은 알고리즘도 판마다 같은 바이트를 낸다). 영향은 녹음 맨
  끝 7.75 ms 이하다. 운영자가 이 차이를 받아들였다(2026-11-10).
  배포 뒤 전사록·문단 목록 비교(2026-11-10, 정책 4단계): 60초 녹음의 1차·2차와 재전사 3번은 문단 목록이 기준과 같다. 1시간 녹음의 2차는 57문단이 같고,
  1차는 37문단 중 마지막 문단(58:50)의 낱말 하나가 다르다(「왕납니다」→「왕래입니다」). 녹음 길이가 3600.064 초에서 3600.056 초로 준 위의 끝 차이에서 온 것으로 보고
  운영자가 받아들였다(2026-11-10). 배포된 서버의 s16 도 명령줄 `ffmpeg` 와 비교해 `clip60.m4a` 는 같고 `hour.m4a` 는 위와 같은 끝 차이뿐이다.
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
| CVE-2026-30997 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-04-13 | 2026-11-10 | AV1 해독기(`libavcodec/av1dec.c`)의 범위 밖 읽기, FFmpeg 9.0 에서 고침. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 서버에는 2026-11-10 의 P2 배포(`f41e921`)로 들어갔다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |
| CVE-2026-12706 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-06-19 | 2026-11-10 | RASC 영상 해독기의 해제 뒤 사용, 9.0 에서 고침. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 서버에는 2026-11-10 의 P2 배포(`f41e921`)로 들어갔다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |
| CVE-2026-66038 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-07-24 | 2026-11-10 | LCL/ZLIB 영상 해독기의 초기화되지 않은 힙 노출, 8.1.2 까지 해당하고 9.0 에서 고침. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 서버에는 2026-11-10 의 P2 배포(`f41e921`)로 들어갔다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |
| CVE-2026-70629 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-08-06 | 2026-11-10 | RSCC 해독기의 초기화되지 않은 힙 읽기, 9.0 미만이 해당. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 서버에는 2026-11-10 의 P2 배포(`f41e921`)로 들어갔다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |
| CVE-2026-70631 | PyAV 휠의 FFmpeg 8.1.2, nixpkgs ffmpeg 8.1.2 | 2026-08-06 | 2026-11-10 | TIFF 해독기의 초기화되지 않은 힙 노출, 9.0 미만이 해당. 정책 시작(2026-11-09) 전에 나온 공지. 영상·이미지 해독기라 음성만 꺼내는 서버 경로에서 닿을 가능성은 낮지만 허용한 컨테이너(mov·asf 등)의 분석 중 불릴 수 있어 해당으로 본다. 저장소에서 av 19.0.2(FFmpeg 9.0.2)로 올림(2026-11-10). 서버에는 2026-11-10 의 P2 배포(`f41e921`)로 들어갔다. nixpkgs ffmpeg 는 서버 경로 밖(명령줄)이라 올리지 않는다. |

첫 확인(2026-11-09)에서 CVE-2026-8461·CVE-2026-30999 는 FFmpeg 8.1.2 에서 이미 고쳐졌고 CVE-2026-30998 은 `tools/zmqsend.c` 의 문제로 라이브러리에
들어 있지 않아, 서버의 판에 해당하지 않음을 확인하고 표에 올리지 않았다.

### 4.4 매달 확인 기록

<!-- stt:monthly-checks -->

| date | sources | result |
|---|---|---|
| 2026-11-09 | ffmpeg-security, pyav-releases, nixpkgs-ffmpeg | 새 판 있음 — PyAV 19.0.2(휠의 FFmpeg 9.0.2, 2026-10-21), nixpkgs nixos-unstable 의 ffmpeg 9.0.2. 서버 판(av 18.0.0, FFmpeg 8.1.2)에 해당하는 보안 공지 5건(4.3 표). 올리지 않음 — av 19 는 API 가 바뀌어(`stream_options` 제거, 유리수 속성이 `av.AVRational`) 별도 과제로 운영자가 정한다. 이 첫 확인은 운영자 지시로 Claude 가 했다. 운영자가 2026-11-10 에 올리기로 정해 저장소의 잠금을 PyAV 19.0.2 로 올렸고(T044), 2026-11-10 의 P2 배포(`f41e921`)로 서버도 av 19.0.2·FFmpeg 9.0.2 가 됐다(배포 뒤 기준 녹음 비교는 4.1). |
