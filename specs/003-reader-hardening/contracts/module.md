# 배치의 바뀜 — `services.stt` (003)

002 의 [`specs/002-decoder-isolation/contracts/module.md`](../../002-decoder-isolation/contracts/module.md)에 더하는 것만 적는다. 단위·소켓의 설정 값,
선택지(options), 호스트(nixos-config)의 몫은 하나도 바뀌지 않는다. 근거는 [research.md](../research.md) R7·R12 다.

## 1. systemd 판 단언 (US4-2, FR-011)

`nix/module.nix` 의 `config = lib.mkIf cfg.enable { … }` 에 더한다.

```nix
assertions = [
  {
    assertion = lib.versionAtLeast config.systemd.package.version "257";
    message = "services.stt needs systemd 257 or later: older systemd ignores PrivatePIDs= with only a warning, "
      + "so reader instances could see and signal each other; systemd.package is version ${config.systemd.package.version}.";
  }
];
```

- 257 은 서버 단위들이 쓰는 설정 가운데 가장 새것인 `PrivatePIDs=` 가 들어온 판이다(이 호스트의 systemd 262 설명서). `PrivatePIDs` 는 읽기 단위와
  공통 묶음 `hardening`(받기·워커·청소·모델 받기) 모두에 있다.
- 이 호스트는 262, 이 flake 의 nixpkgs(VM)는 261.1 이라 둘 다 통과한다.
- 거부는 `nixos-rebuild`·`nix build` 의 평가 단계에서 `Failed assertions:` 아래 위 문장으로 나온다.

## 2. 평가 시험 `checks.x86_64-linux.module-eval` (새것)

- 위치: `flake.nix` 의 `checks.x86_64-linux` 에 `module-eval`(본문은 `nix/module-eval.nix` 로 두어도 된다).
- 하는 일: `nixpkgs.lib.nixosSystem` 으로 `self.nixosModules.default` + `services.stt.enable = true; boot.isContainer = true;` 를 평가하고,
  `config.assertions` 가운데 실패한 것의 문장을 모은다(빌드하지 않는다).
  - `systemd.package` 를 `pkgs.systemd.overrideAttrs (_: { version = "256.10"; __intentionallyOverridingVersion = true; })` 로 바꾼 평가: 실패한 문장 가운데
    `services.stt needs systemd 257 or later` 로 시작하는 것이 정확히 하나이고 `256.10` 을 담는다.
  - `version = "257"` 인 평가와 바꾸지 않은 평가(261.1): 그 문장이 없다.
  - 위 셋을 평가 시점의 `assert` 로 두고, 통과하면 빈 결과물(`runCommand … "touch $out"`)을 낸다. 그래서 CI 의 `nix flake check --no-build` 도 단언이 빠지면
    실패한다.
- 비용: NixOS 평가 한 번의 최대 메모리가 약 4.2 GB 였다(research R7). 로컬은 `captest 8G 15m nix build .#checks.x86_64-linux.module-eval -L`.
- RED: 단언을 넣기 전에 이 검사를 먼저 넣으면 첫 `assert` 가 실패한다.

## 3. VM 시험 노드에만 있는 것 (`nix/vm-test.nix`, 운영 모듈에는 없음)

| 무엇 | 값 | 까닭 |
|---|---|---|
| 해독기 감시 라이브러리 | `pkgs.runCommandCC` 로 만든 공유 라이브러리 하나(`hangingCuda` 와 같은 방식). `avcodec_open2` 를 가로채, 진짜 함수(프로세스에 실린 `libavcodec` 를 `dl_iterate_phdr` 로 찾아 `dlopen(RTLD_NOLOAD)`)가 0 을 돌려줬는데 문맥의 `codec_type`(AVCodecContext 의 세 번째 필드)이 음성이 아니면 `abort()` | 진짜 읽기 단위 안에서 영상·이미지 해독기가 열리면 해독 자식이 SIGABRT 로 끝나고, 진짜 감독이 `reader child <probe\|decode> killed by signal 6` 를 저널에 쓴다. 그 줄이 없으면 열리지 않은 것이다(SC-001, research R12) |
| 노드 설정 | `systemd.services."stt-reader@".environment.LD_PRELOAD = "<감시 라이브러리>"` — `server` 노드의 기본 구성. specialisation 들도 물려받는다(대역은 `libavcodec` 를 싣지 않으므로 아무 일도 하지 않는다) | VM 의 모든 진짜 읽기(형식 8가지 B-P2-8 포함)가 감시 아래에서 돈다 |
| 시험 녹음 | 아래 §4 | |

- B-P2-1 의 설정 단언은 읽기 단위의 `Environment` 를 보지 않으므로 그대로 통과한다. 운영 단위의 설정 값과 다른 것은 이 한 줄뿐이다.
- `[VERIFY]` (B-003-1): 감시 라이브러리가 읽기 단위의 제한(동적 사용자, `MemoryDenyWriteExecute`, 호출 필터, `TemporaryFileSystem`) 안에서 실린다 —
  지금 코드로 먼저 돌려 `killed by signal 6` 이 나오는 것(RED)으로 확인한다.

## 4. VM 시험 녹음 (`audio` 파생물에 더함, nixpkgs `ffmpeg` 8.1.2)

| 파일 | 만드는 법 | 쓰는 곳 |
|---|---|---|
| `tone-video.m4a` | 사인파 2초 AAC + 단색 64×64 H.264(`libx264`), `-f mp4` | US1-2 |
| `tone-video.wma` | 사인파 2초 WMA 2 + 단색 WMV2, `-f asf` | US1-2 |
| `tone-cover.mp3` | `tone.mp3` + 단색 32×32 PNG, `-map 0 -map 1 -c copy -id3v2_version 3 -disposition:v attached_pic` | US1-3 |
| `video-only.m4a` | 단색 H.264 2초, 음성 없음 | US1-4 |
| `tone-ac3.m4a` | 사인파 2초 AC-3, `-f mp4` | US1-5 |
| `cut-tone.mp3`·`cut-tone.flac`·`cut-faststart.m4a` | 사인파 10초(M4A 는 `-movflags +faststart`)를 `head -c $((크기 / 2))` 로 반만 | US2-1 |
| `nopackets-faststart.m4a` | 색인이 앞인 사인파 10초 M4A 를 `mdat` 머리(크기·이름 8바이트) 바로 뒤에서 자름 | US2-4 |
| `tone10.mp3` | 사인파 10초 MP3(자르지 않음) | US2-2 대조 |

모두 사인파·단색 영상·단색 그림과 그 끝을 자른 것이다(FR-017). 해독기를 노리는 파일은 없다.

## 5. 배포 메모 (002 §3 의 규칙 그대로)

- 바뀌는 것은 패키지(코드)와 모듈의 단언 하나다. 소켓 설정이 바뀌지 않으므로 `systemctl restart stt-reader.socket` 은 필요 없다.
- 읽기 단위 템플릿은 `restartIfChanged=false` 라 도는 읽기는 옛 코드로 마치고 다음 읽기부터 새 코드다. 워커도 `restartIfChanged=false` 라 도는 전사를 끊지
  않는다. 새 코드가 적용됐다고 부르기 전에 `systemctl show -p MainPID stt-worker.service` 가 스위치 전과 다른지(또는 0 인지) 본다(보안 문서 §3 배포 메모).
- 목록 밖 음성 코덱의 녹음은 배포 뒤 받는 순간 `unreadable_audio` 다. 배포한 날 이미 받아 둔 그런 녹음의 재전사는 해독에서 `unreadable_audio` 가 된다
  (그날 23:58 까지만, research R2).
