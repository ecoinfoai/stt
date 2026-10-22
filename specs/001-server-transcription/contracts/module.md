# 배치 계약 — NixOS 모듈 `services.stt` 와 호스트(nixos-config)의 몫

stt 저장소는 패키지와 NixOS 모듈을 낸다(`flake.nix` 의 `packages.<system>.server`, `nixosModules.default`). 호스트에 붙이는 일은
nixos-config 가 한다 — geo-scout 와 같은 모양이다(`nixos-config:flake.nix:78-92`). 모듈은 bhu-llm 의 단위를 건드리지 않는다.

## 1. 선택지(options)

| 선택지 | 형식 | 기본값 | 뜻 |
|---|---|---|---|
| `services.stt.enable` | bool | `false` | 단위·계정·폴더를 만든다 |
| `services.stt.package` | package | 이 flake 의 서버 패키지 | 시험에서만 바꾼다 |
| `services.stt.huggingFaceTokenFile` | 절대 경로 \| null | `null` | `HF_TOKEN=...` **한 줄만 든 전용** agenix 비밀 파일(다른 키가 든 공용 파일을 넘기지 않는다 — §4). 모델 받기 단위만 `EnvironmentFile` 로 읽는다. null 이면 모델 받기 단위를 만들지 않는다 |

비밀은 값이 아니라 경로로만 받는다(geo-scout 와 같은 규칙). 폴더 위치는 선택지로 두지 않는다 — 계약(README §2)의 일부다.

## 2. 계정·폴더

- 시스템 계정 `stt`(그룹 `stt`), 그룹 `stt-requests`(구성원은 호스트가 정한다).
- tmpfiles: README §2 의 폴더와 권한, inbox 의 기본 ACL `default:user:stt:rwx`, `/run/stt` 0755, `/run/stt/gpu.lock` 0644.
- `environment.variables.STT_GPU_LOCK = "/run/stt/gpu.lock"` — 운영자의 명령줄 전사가 같은 줄에 서게 한다([cli.md](cli.md) §2).

## 3. 단위

| 단위 | 깨우는 것 | 명령 | 망 | GPU | 상한 |
|---|---|---|---|---|---|
| `stt-intake.path` → `stt-intake.service` | `PathExistsGlob=/var/lib/stt/requests/inbox/*.json` | `stt server intake` | 없음 | 없음 | MemoryMax 1G · 10분 · `StartLimitIntervalSec=0` |
| `stt-worker.path` → `stt-worker.service` | `PathExistsGlob=/var/lib/stt/queue/*` | `stt server work --gpu-lock /run/stt/gpu.lock` | 없음 | 있음 | MemoryMax 12G · `TimeoutStartSec=infinity` — job 마다의 처리 상한은 워커가 지키고, GPU 잠금 대기에는 상한이 없다(FR-016). 24시간으로 두면 줄이 길 때 처리 중인 job 이 끊긴다 · `after`·`wants` `stt-models.service` · `StartLimitIntervalSec=0` · `KillSignal=SIGKILL` · `restartIfChanged=false` |
| `stt-sweep.timer` → `stt-sweep.service` | 1분마다(`OnCalendar=minutely`, `AccuracySec=1s`, `Persistent=true`) — 기본 `AccuracySec=1min` 이면 자정 삭제가 1분 넘게 늦을 수 있다 | `stt server sweep` | 없음 | 없음 | MemoryMax 512M · 5분 |
| `stt-models.service` | 부팅마다(`wantedBy multi-user`, 조건 없음, `RemainAfterExit=true`). `.complete` 의 고정 목록이 같으면 망에 닿지 않고 바로 끝나고, stt 판이 고정 목록을 바꾸면 새 판을 받는다. 판이 바뀐 배포에서 NixOS 가 다시 돌린다 `[VERIFY]`(research R9). `wants`·`after` `network-online.target` | `stt server fetch-models` | 있음 | 없음 | 2시간 |

모든 단위 공통(geo-scout 의 묶음을 따른다): `Type=oneshot`, `User=stt`, `Group=stt`, `UMask=0027`, `NoNewPrivileges`,
`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `ProtectKernel*`, `ProtectControlGroups`, `RestrictNamespaces`, `RestrictSUIDSGID`,
`LockPersonality`, `CapabilityBoundingSet=""`, `PrivatePIDs`, `SuccessExitStatus=75`, `StateDirectory=stt`·`StateDirectoryMode=0751`
(기본값 0755 — contracts/README §2), 그리고 이 기능에 더하는 것:

- **`MemorySwapMax=0`** — 이 호스트의 스왑은 평문이다(bhu-llm F198). 녹음을 푼 메모리가 스왑으로 내려가지 않게 한다(FR-035).
- **`LimitCORE=0`** — 엔진이 죽어도 녹음이 담긴 오류 덤프가 남지 않게 한다(FR-035).
- 망: 모델 받기 말고는 `PrivateNetwork=true`, `RestrictAddressFamilies=AF_UNIX`(FR-015).
- 워커만: `PrivateDevices=false`, `DevicePolicy=closed`, `DeviceAllow=/dev/nvidiactl rw, /dev/nvidia0 rw, /dev/nvidia-uvm rw, /dev/nvidia-uvm-tools rw`
  (이 호스트에서 확인한 장치 파일, research R2). 나머지 단위는 `PrivateDevices=true`.
- 워커만: `RemoveIPC=true` — 엔진이 강제 종료돼도 stt 계정의 IPC 객체(공유 메모리 등)가 남지 않게. 계정의 객체를 모두 지우는 설정이라 다른
  단위에는 두지 않는다(그 단위가 멈출 때 돌고 있는 워커의 것까지 지운다, research R20-17).
- 받기·워커: `StartLimitIntervalSec=0` — path 단위가 깨우는 단위라 요청이 몰리면 systemd 기본 시작 상한(10초에 5번)에 걸리고, 그러면 path 단위까지
  `unit-start-limit-hit` 로 멈춰 다시는 깨우지 않는다(VM 시험에서 요청 6개로 드러났다). 상한을 없애는 대가: 지우지 못하는 파일에서 단계가 거듭 죽으면
  멈추지 않고 되풀이해 돈다 — 계약 §7 의 「5분 안에 응답 없음」이 그것을 드러낸다.
- 워커만: `KillSignal=SIGKILL`, `restartIfChanged=false` — 워커는 `PrivatePIDs` 의 PID 1 이라 처리기 없는 SIGTERM 을 받지 않는다. 기본 정지는
  SIGTERM 을 묶음 전체에 보내 엔진만 죽이고, 워커는 판정 없는 엔진을 `engine_error` 로 게시한 뒤 다음 job 을 시작한다. SIGKILL 로 바로 멈추면
  처리 중인 job 은 다음 워커 시작이나 청소에서 `interrupted` 가 된다. 배포(`nixos-rebuild switch`)는 도는 전사를 끊지 않고, 다음 path 시작이 새
  `ExecStart` 를 쓴다.
- 쓰기: 받기·워커·청소는 `/var/lib/stt`(StateDirectory), 모델 받기는 `/var/lib/stt-models`. 워커는 모델 폴더를 읽기만 한다.
- 엔진 환경은 **워커가 엔진 하위 프로세스에만** 넣는다: `HF_HOME`(= `--models`), `HF_HUB_OFFLINE=1`, `TMPDIR=<job 작업 폴더>`. 단위 환경에는
  두지 않는다 — 모듈이 모든 단위에 `HF_HUB_OFFLINE` 을 걸면 모델 받기가 동작하지 않는다(research R21-5).
- 모델 받기만: `Environment=HF_XET_CACHE=/var/lib/stt-models/xet` — huggingface_hub 는 hf_xet 의 캐시를 `$HF_HOME/xet` 에 두는데, 단위의
  홈(`/var/empty`)은 쓸 수 없다(`ProtectSystem=strict`, `ProtectHome`). `fetch-models` 는 `cache_dir` 만 넘긴다. `HF_HOME`·`HF_HUB_OFFLINE` 은
  여기에도 두지 않는다. 패키지가 `PATH` 에 ffmpeg 를 넣는다.
  GPU 라이브러리 경로는 패키지가 휠의 RUNPATH 에 넣는다 — `LD_LIBRARY_PATH` 를 두지 않는다(research R1).

## 4. 호스트(nixos-config)의 몫 — 이 저장소 밖

1. flake 입력에 stt 를 더하고(`follows` 없이 — stt 는 자기 nixpkgs 를 고정한다) `services.stt.enable = true`.
2. `huggingFaceTokenFile` 에 `HF_TOKEN=...` 한 줄만 든 **전용** agenix 비밀을 준다. 지금 토큰이 든 `shared-api.env` 는 다른 API 키도 담고 있어
   넘기지 않는다(넘기면 모델 받기 단위가 그 키들까지 받는다). 모델은 처음 한 번 받는다.
3. bhu-llm 의 OWUI 서비스 계정을 `stt-requests` 에 넣고, 그 단위에 `ReadWritePaths=/var/lib/stt/requests/inbox`,
   `ReadOnlyPaths=/var/lib/stt/requests/outbox` 를 준다(bhu-llm L1).
4. `/var/lib` 이 암호화된 루트에 있어야 한다(지금은 LUKS 위 btrfs — 2026-10-22 확인). `/var/lib/stt` 를 백업·스냅샷 대상에 넣지 않는다.
5. 스왑 암호화(F198)는 호스트의 일이다. stt 단위는 그와 상관없이 스왑을 쓰지 않는다.
6. 고정 모델 목록이 바뀐 stt 판을 배포했는데 `stt-models.service` 가 다시 돌지 않았으면(`[VERIFY]` 결과에 따라)
   `systemctl start stt-models.service` 를 부른다. 그 전까지 job 은 `models_unavailable` 로 끝난다.
7. `boot.kernelModules = [ "nvidia_uvm" ]` — 지금 이 호스트는 `nvidia_uvm` 을 부팅 때 올리지 않는다(research R2). 권한 없는 워커는 모듈을 올릴 수
   없으므로, 빠지면 재부팅 뒤 첫 job 이 `gpu_unavailable` 로 끝난다.
8. 모듈을 켠 뒤 첫 부팅·배포는 모델 받기가 끝날 때까지 기다린다 — `multi-user.target` 이 원하는 oneshot 단위라 그 뒤에 선다(최대 2시간).
   그다음 부팅은 `.complete` 가 같아 바로 끝난다.
9. 모델 받기 단위가 실패하면 워커가 시작할 때마다(`wants`·`after`) 다시 돈다 — 망이 돌아오면 다음 job 이 모델을 받게 한다. 그동안 job 은
   `models_unavailable` 로 끝난다.
