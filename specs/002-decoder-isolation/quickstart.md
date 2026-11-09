# Quickstart — 녹음 해독 격리를 확인하는 순서

001 과 같은 세 층에서 확인한다. 앞 층이 통과해야 다음 층으로 간다. 배포 단계(P1 → P2·P3)마다 B 와 C 를 다시 돈다.

| 층 | 어디서 | GPU | 무엇을 증명하나 |
|---|---|---|---|
| A. 개발 시험 | 개발 셸, CI | 없음 | 읽기 프로토콜·감독·해독 같음·실패 분류·삭제·기존 동작 회귀·판 기록 대조 |
| B. VM 시험 | `nix build` (NixOS 시험 VM) | 없음 | 단위 설정 값, 진짜 systemd 위의 격리·권한·실패·삭제 |
| C. 호스트 시험 | miniecoinfo, 운영자와 | 있음 | CUDA 쪽 호출·쓰기·실행 메모리, 기준 녹음 비교, 처리 시간, 인계 §6 |

- 해독기를 노리는 파일은 어느 층에서도 만들거나 쓰지 않는다(FR-028). 「나쁜 첨부」는 녹음 확장자를 붙인 텍스트 파일 하나뿐이고, 읽기의 실패는
  시험용 대역으로 만든다([contracts/reader.md](contracts/reader.md) §9).
- 무거운 실행은 모두 `captest <메모리> <시간> …` 로 돌린다(전역 규칙 §5). 아래 상한은 첫 실행의 출발점이다 — 찍힌 최대값을 보고 맞춘다.
- 시나리오 ↔ 시험 이름은 `tasks.md` 가 과제마다 적는다. 이 기능의 pytest 이름은 `test_decoder_us<N>_<n>_...` 꼴이다(001 의 `test_us<N>_...`
  와 겹치지 않게).

## A. 개발 시험

```sh
nix develop
uv sync --locked --extra server
captest 4G 20m uv run python -m pytest -q
```

- pytest-xdist 는 의존성에 없다 — `-n` 없이 한 프로세스로 돈다(001 과 같다).

- 기대: 기존 시험을 포함해 모두 통과(SC-007). 바꾼 기존 시험은 [research.md](research.md) R11 의 목록뿐이다.
- 읽기 단위: `tests/server/test_reader.py`
  - 감독: 요청 검사, `started` 의 두 값, 끊김·상한에서 자식 죽이기, 판정 만들기(대역 자식 주입).
  - 진짜 자식: `encode_tone`·`make_wav` 의 사인파(M4A·MP3·ADTS·Ogg·FLAC·WAV)로 길이 재기를 본다. 해독 출력은 `faster_whisper.audio.decode_audio`
    와 바이트까지 같아야 한다.
  - 녹음 확장자를 붙인 텍스트 → probe `failed`.
- 호출하는 쪽: `tests/server/test_reader_client.py` — 다섯 결과(`ok`·`failed`·`unavailable`·`stopped`·`timeout`)의 경계, 홀수·상한 넘는 출력, 엉터리
  판정.
- 시나리오: `tests/server/test_decoder_us2.py`·`_us3.py`·`_us4.py` — 흉내 소켓(연결마다 진짜 감독 또는 `fake_reader.py`)과 가짜 엔진(`--record`)
  으로 본다.
  - GPU 단계가 받는 입력에 녹음 경로가 없음(SC-003).
  - 실패 방식 넷 → `unreadable_audio`, 띄우지 못함 → `engine_error`, 그리고 다음 작업의 완료(SC-005).
  - 한 녹음에 작업 5개와 「다 지우기」(SC-006).
  - 다섯 경우의 남은 파일 0(SC-008).
- 문서 대조: `tests/test_server_security_doc.py` — `docs/server-security.md` 의 영어 표식만 읽는다.
  - §1 표와 `nix/module.nix` 의 필터·예외·좁힘·쓰기·실행 메모리 금지(US1-3·4).
  - §4 의 av 판과 nixpkgs 커밋이 `uv.lock`·`flake.lock` 과 같은지, 정책 표식, 확인 기록의 빠진 달, 공지 뒤 7일, 기준 녹음 비교 날(US5-1~5).
- CI 는 Python 3.10·3.12 둘 다 돈다. `socket.send_fds`·`recv_fds`(3.9+)는 둘 다에 있고, `tomllib`(3.11+)은 쓰지 않는다.

## B. VM 시험

```sh
captest 8G 60m nix build .#checks.x86_64-linux.server-vm -L
```

### B-P1 — 네 단위의 호출 제한 (US1, SC-001)

1. `isolation settings` 부분 시험이 받기·워커·청소에서 `SystemCallArchitectures=native`, 허용 목록 꼴의 `SystemCallFilter`(`~` 로 시작하지
   않음), `MemoryDenyWriteExecute`(§1 표의 값)를 본다. `fetcher` 노드에서 모델 받기도 같은 것을 본다.
2. 기존 부분 시험이 모두 필터를 건 채로 통과한다 — 받기의 받아들임·거절, 길이 재기(P1 동안은 받기 안의 PyAV), 줄, GPU 잠금, 지우기, 청소, 죽은 워커
   정리, 모델 받기의 망 실패까지(US1-6).
3. `journalctl -k` 에 `type=1326` 줄이 없다 — 막힌 호출이 없었다.

### B-P2 — 읽기 단위 (US2·US3·US4, SC-002~006·008)

1. **B-P2-1 설정과 진짜 해독**: `stt-reader@check.service`·`stt-reader.socket` 의 값([contracts/module.md](contracts/module.md) §4 명령)을
   확인한다. 2초 사인파 job 은 읽기 단계(진짜 PyAV)를 지나 GPU 단계에서 `gpu_unavailable` 로 끝난다. 막힌 CUDA 를 흉내 내 엔진을 붙잡아 둔 동안
   (`hangingCuda`, 001 VM 시험), 다음을 본다.
   - `work/<id>/audio.wav` 가 있다(16-bit 모노 16 kHz).
   - `spec.json` 에 `audio_path` 가 없다(US2-5).
   - `RestrictAddressFamilies=none`·`ProtectProc`·`ProcSubset`·`InaccessiblePaths` 를 건 채로 해독이 끝났다.
2. **B-P2-2 접속**: 받기·워커가 자기 샌드박스(읽기 전용 루트, 망 이름공간 없음)에서 소켓에 접속했다 — B-P2-1 이 통과하면 성립한다.
3. **B-P2-3 텍스트 첨부**: `corrupt.mp3`(녹음 확장자를 붙인 텍스트)와 정상 녹음을 같은 묶음으로 맡긴다. 텍스트는 `unreadable_audio`, 정상은
   `queued` 가 되고, 받기·워커·청소 단위는 `failed` 가 아니다(US3-1, SC-004).
4. **B-P2-4 권한 점검**: specialisation 으로 읽기 단위의 `ExecStart` 를 권한 점검 대역으로 바꾼다. 먼저 VM 에 `/dev/nvidia0`·`nvidiactl`·
   `nvidia-uvm` 시험 노드를 만든다(장치 번호만 같은 가짜). 요청자가 다른 작업 A·B 와 A 의 재전사를 둔 뒤, A 의 읽기와 재전사 A′ 의 읽기에서 각각
   대역이 다음을 시도하고 성공 수를 판정에 담는다. 기대는 모두 0 이다(US2-1·2·3·4, SC-002). 반대로 자기가 받은 녹음 기술자는 읽혀야 한다
   (US2-2 — 공유하는 녹음에는 기술자로만 닿는다).
   - B 의 녹음, A·B 의 작업 폴더(해독된 음성), 힌트가 든 기록, `requests/inbox`·`outbox` 의 파일, `state.lock`, `/run/stt/gpu.lock` 의 읽기·쓰기·지우기
   - `/dev/nvidia*` 열기
   - `socket(AF_INET)`·`socket(AF_UNIX)`
   - 자기 녹음 기술자에 쓰기
   - 쓰기 가능한 자리(`/tmp`·`/var/tmp`·현재 폴더)에 자료 쓰기(`LimitFSIZE=0`)
   - 대역은 자기 UID 도 보고한다. 받기의 길이 재기와 워커의 해독을 겹쳐 돌려 두 UID 가 서로 다르고 `stt` 와도 다른지 본다(US2-6, 경계 사례 2).
   - 읽기가 끝난 뒤 `systemctl list-units --all 'stt-reader@*'` 가 비어 있고, 호스트의 `/tmp`·`/var/tmp` 에 그 인스턴스의 사설 폴더가 없다(US2-7).
5. **B-P2-5 실패 대역**: 판정 없이 끊기, 멈추기(상한까지), 메모리 넘기기(768M 초과 할당), `started: false`. 각각 받기에서는 그 요청만 거절되거나
   치워지고, 처리에서는 그 작업만 `unreadable_audio`(마지막 것은 `engine_error`)로 끝난다. 바로 뒤의 정상 작업이 `gpu_unavailable` 까지 가고,
   `systemctl --failed` 에 stt 단위가 없다(US3-2·3·4·5·6·8, SC-005). 각 실패 뒤 `stt-reader@*` 인스턴스가 남지 않는다(US2-7).
6. **B-P2-6 읽는 중의 삭제**: 멈추는 대역으로 읽기를 붙잡아 둔 채 다음 셋을 한다.
   - 「다 지우기」 → 1분 안에 `stt-reader@*` 인스턴스가 없고 그 작업의 파일이 0개다(US4-4).
   - VM 시계를 23:58 로 옮겨 청소 → 같음(US4-5).
   - `systemctl stop stt-worker` → 인스턴스가 끝나고, 청소 뒤 `interrupted`, `work/` 가 비어 있다(US4-6).
7. **B-P2-7 로그**: 기존 「힌트 단어·요청자 없음」 시험에 더해, `journalctl -u 'stt-reader@*'` 에 작업 ID 가 하나도 없다(FR-019).

## C. 호스트 시험 (운영자와, miniecoinfo)

시험 요청은 001 T076 처럼 `sudo` 로 stt 소유·`stt-requests` 그룹·0640 파일을 inbox 에 쓴다. 기준 녹음은 T076 의 60초(`clip60.m4a`)와 1시간
녹음이다.

### C-0 — 기준 결과 (P1 배포 전)

- 두 녹음을 1차(힌트·화자 구분 없음)와 2차(힌트·화자 구분)로 전사하고, `transcript.txt` 와 `result.json` 의 `paragraphs` 를 보관한다
  (운영자의 기기, 저장소 밖).

### C-P1 — 시스템 호출 제한

1. **C-P1-1 관측**(필터 없이 기록만, [research.md](research.md) R2):

   ```sh
   for u in stt-intake stt-worker stt-sweep stt-models; do
     sudo mkdir -p /run/systemd/system/$u.service.d
     printf '[Service]\nSystemCallLog=~@system-service\n' | sudo tee /run/systemd/system/$u.service.d/observe.conf
   done
   sudo systemctl daemon-reload
   # 1차·2차 전사, 텍스트 첨부 하나, 「다 지우기」, 청소 1분, sudo systemctl restart stt-models
   journalctl -k -g 'type=1326' --since '<시작 시각>'
   sudo rm /run/systemd/system/stt-{intake,worker,sweep,models}.service.d/observe.conf
   sudo systemctl daemon-reload
   ```

   - 나온 호출 번호마다 `nix shell nixpkgs#libseccomp -c scmp_sys_resolver <번호>` 로 이름을 찾는다. 단위·호출·이유를 `docs/server-security.md`
     §1 에 적고, 그 예외만 모듈에 더한다(FR-002).
2. **C-P1-2 배포와 확인**: P1 을 배포한다(`nxr`).
   - 인계 §6 의 `systemctl show` 를 실행한다([contracts/module.md](contracts/module.md) §4). 네 단위 모두 필터 목록이 보이고
     `SystemCallArchitectures=native` 다(US1-1·2).
   - C-0 의 두 녹음을 같은 두 방식으로 다시 전사해 전사록·문단 목록을 비교한다(US1-5). 다르면 차이를 운영자에게 보이고 받아들인 뒤에만 완료다.
   - 워커의 `MemoryDenyWriteExecute=yes` 로 CUDA 가 거부되면(엔진이 `engine_error`·`gpu_unavailable` 로 끝나거나 오류 문장), 워커만 `false` 로
     바꾸고 거부된 지점을 §1 에 적는다(US1-4).
   - 받기의 받아들임·거절, 「다 지우기」, 청소, `systemctl restart stt-models` 가 평소대로 끝나고(US1-6), `journalctl -k -g 'type=1326'` 이
     비어 있다.
3. **C-P1-3 회신**: `handoff/p1-reply.md`([data-model.md](data-model.md) §9). 운영자가 bhu-llm 대장 A18 로 옮긴다.

### C-P2 — 읽기 격리 (P3 문서와 함께 배포)

1. **C-P2-1 설정**: [contracts/module.md](contracts/module.md) §4 의 세 명령. 읽기 단위는 `DynamicUser=yes`·`PrivateDevices=yes`·
   `PrivateNetwork=yes` 이고, 받기·워커는 그대로 `User=stt` 다.
2. **C-P2-2 인계 §6**: 같은 묶음에 정상 녹음 하나와 텍스트 파일을 `.m4a` 로 붙인 요청 하나를 맡긴다. 정상은 완료되고 텍스트는
   `unreadable_audio` 이며, `systemctl --failed` 에 stt 단위가 없다(SC-004).
3. **C-P2-3 해독 시간의 여유**: 3시간 사인파(Opus·AAC)의 해독 시간과 최대 메모리를 배포된 서버 해석기로 잰다(research R9 의 방법, `captest 1G 10m`).
   기대는 해독 48초 이하(상한 240초의 1/5)와 메모리 153 MB 이하(상한 768M 의 1/5)다. 넘으면 상한을 고친다.
4. **C-P2-4 재전사**: 한 녹음에 1차·자동 2차·재전사 3번(작업 5개)이 모두 완료되고, 「다 지우기」 1분 뒤 그 녹음을 쓴 작업들의 파일이 0개다(US4-2·3,
   SC-006). 인계 §6 의 「재전사 3번」도 이것으로 확인된다.
5. **C-P2-5 기준 녹음 비교**: C-0 과 1차·2차의 전사록·문단 목록을 비교한다(SC-007). 화자 구분 입력이 바뀌었으므로(ffmpeg 명령 → PyAV, research R7)
   두 녹음의 s16 을 먼저 `cmp` 한다.

   ```sh
   ffmpeg -nostdin -loglevel error -i clip60.m4a -ac 1 -ar 16000 -f s16le cli.raw
   # 같은 파일을 읽기 단위의 해독 자식과 같은 코드로 s16 으로 바꾼 pyav.raw 와 비교
   cmp cli.raw pyav.raw
   ```

6. **C-P2-6 처리 시간**: 대기 없는 1시간 녹음의 1차 10분·2차 25분 안(001 SC-001), 접수 확인은 맡긴 직후(001 SC-011)(US2-8·9).
7. **C-P2-7 (선택) 실제 인스턴스 안**: 긴 녹음을 읽는 동안 `systemctl list-units 'stt-reader@*'` 로 인스턴스를 찾는다. 그 PID 에
   `sudo nsenter -t <PID> -m ls /dev` 를 하면 NVIDIA 노드가 없고, `ps -o uid= -p <PID>` 는 `stt` 와 다른 UID 다.
8. **C-P2-8 회신**: `handoff/p2-reply.md`.

### C-P3 — 판 기록과 정책

- `docs/server-security.md` §4 의 표에서, 서버 경로에서 녹음 바이트를 해석하는 구성 요소가 PyAV 의 FFmpeg 하나뿐이고 판·고정한 곳·들어온 날·
  마지막 갱신일이 있는지 본다. 정책 절이 FR-025 그대로인지 본다(US5-1·2).
- 배포한 달의 확인 기록을 첫 줄로 남긴다(US5-4). A 층의 `tests/test_server_security_doc.py` 가 통과한다.
