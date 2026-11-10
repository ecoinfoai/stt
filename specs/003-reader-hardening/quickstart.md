# Quickstart — 녹음 읽기 추가 좁히기와 알아차리기를 확인하는 순서

002 와 같은 세 층에서 확인한다. 앞 층이 통과해야 다음 층으로 간다. 배포는 한 번이다(단계 없음).

| 층 | 어디서 | GPU | 무엇을 증명하나 |
|---|---|---|---|
| A. 개발 시험 | 개발 셸, CI | 없음 | 해독기 목록의 동작·같음, 잘림 판정·줄, 원인 종류, 감독의 신호·안내, 현재 폴더, 0600, 문서 대조, 001·002 회귀 |
| B. Nix 검사 | `nix build` (평가 검사 + NixOS 시험 VM) | 없음 | systemd 판 단언, 진짜 읽기 단위에서 영상·이미지 해독기가 열리지 않음, 잘림 줄·원인 이름의 운영자 확인 명령 |
| C. 호스트 시험 | miniecoinfo, 운영자와 | 있음 | 기준 녹음 회귀 없음, 배포된 읽기 단위로 영상·표지 그림 녹음, 손 실행 안내 |

- 시험 녹음은 사인파·무음·단색 영상·단색 그림과 그 끝을 자른 것, 녹음 확장자를 붙인 텍스트 파일뿐이다(FR-017). 정확한 파일과 만드는 법은
  [contracts/module.md](contracts/module.md) §4(VM)와 [research.md](research.md) R12(pytest)에 있다.
- 무거운 실행은 모두 `captest <메모리> <시간> …` 로 돌린다(전역 규칙 §5). 아래 상한은 출발점이다 — 찍힌 최대값을 보고 맞춘다.
- 시나리오 ↔ 시험 이름은 `tasks.md` 가 과제마다 적는다. 이 기능의 pytest 는 `tests/server/test_hardening_us<N>.py` 의 `test_hardening_us<N>_<n>_…` 이다.

## A. 개발 시험

```sh
nix develop
uv sync --locked --extra server
captest 4G 20m uv run python -m pytest -q
# Python 3.11 (명령줄 판 av 18.1.0): 프로젝트 .venv 를 건드리지 않는 따로 된 환경으로
UV_PROJECT_ENVIRONMENT="$HOME/.cache/stt-py311" captest 4G 25m uv run --locked --python 3.11 --extra server python -m pytest -q
```

- Python 3.10(av 17.1.0)과 3.12 는 CI 가 돈다. 3.11 은 로컬에서 위 명령으로 돈다(인터프리터가 PATH 에 있어야 한다, 예: `nix shell nixpkgs#python311`).
- 기대: 001·002 시험을 포함해 모두 통과(SC-006). 바꾼 기존 시험은 [research.md](research.md) R6·R9 의 목록뿐이다.

| 시나리오·요구 | 시험(파일 · 무엇을 보나) |
|---|---|
| US1-1, SC-002(앞) | `test_hardening_us1.py` · 형식 8가지(PyAV 로 만든 사인파)와 ALAC·PCM 변형이 목록을 건 열기로 길이 재기 값이 같고 해독 바이트가 같다. 진짜 감독·자식(흉내 소켓)으로도 `ok`·`ok` |
| US1-2, 경계 1 | 같은 파일 · AAC + 단색 MPEG-4/H.264 영상: 진짜 자식의 probe·decode 가 음성만 든 녹음과 같은 값·바이트. 프로세스 안에서 `reader._open_container` 로 연 영상 흐름이 `pix_fmt is None`, `decode(video=0)` 이 `av.error.ArgumentError`. 워커(가짜 엔진)로 `done` |
| US1-3 | 같은 파일 · MP3 + 단색 PNG 표지: 위와 같은 단언 |
| US1-4 | 같은 파일 · 영상만 든 MP4 → 받기 `unreadable_audio`, 영상 흐름 `pix_fmt is None` |
| US1-5, FR-003 | 같은 파일 · WAV 안 Yamaha ADPCM·MOV(`.m4a`) 안 QuickTime IMA4(PyAV 로 만듦) → 받기 `unreadable_audio`(길이 재기의 이름 확인). 해독 자식을 직접 부르면 끝 코드 1(`ArgumentError`). 길이 재기 이름 확인 함수의 순수 시험(가짜 컨테이너: 해독기 없음·목록 밖·목록 안) |
| 경계 2 | 같은 파일 · 음성 흐름 둘: 첫 흐름만, 바이트 그대로 |
| R2 더한 녹음기 코덱 | 같은 파일 · WAV 안 IMA·Microsoft ADPCM, MP2(`.mp3`·`.wav`), MP4 안 AC-3·E-AC-3(PyAV 19 인코더)과, 설치된 PyAV 에 `libopencore_amrnb` 인코더가 있으면(17·18 — Python 3.10·3.11) 3GP 안 AMR-NB(`.m4a`): 진짜 감독·자식으로 probe·decode `ok`, 사인파 길이. 인코더가 없는 판은 이유를 적고 건너뛴다. WMA Pro·Voice·Lossless·AMR-WB 는 인코더가 없어 A 층 시험이 없다(research R2 의 `[VERIFY]`) |
| FR-005 | 같은 파일 · `stt.audio_decode.decode_samples` 는 목록 없이 Yamaha ADPCM 도 해독한다(명령줄 그대로) |
| US1-6, FR-004 | `tests/test_server_security_doc.py` · `<!-- stt:audio-decoders -->` 의 `decoder` 집합 == `reader.AUDIO_DECODERS`. 그리고 `open_container(..., codec_whitelist=…)` 가 형식 옵션과 모든 흐름의 코덱 문맥 옵션에 그 값을 둔다(가로챈 `av.open` 과 열린 컨테이너로) |
| US2-1, SC-003(앞) | `test_hardening_us2.py` · 사인파 10초 MP3·FLAC·색인이 앞인 M4A 를 반으로 자른 것: 받기 → 워커(가짜 엔진) → `done`, 표준 출력에 `<id> running`, 잘림 줄, `<id> done` 이 이 순서로 한 번 |
| US2-2·3, SC-003(뒤) | 같은 파일 · 형식 8가지 정상 녹음은 줄 0개. 비교 함수: 차이 정확히 1.0 → 참, 0.999… → 거짓, 해독이 더 김 → 거짓, 0.046(WMA 끝)·0.0065(Opus)·−0.056(기준 1시간) → 거짓 |
| US2-4 | 같은 파일 · 색인이 앞인 M4A 를 음성 자료 시작에서 자른 것: `done`(빈 전사록), 줄 하나 |
| US2-5 | 같은 파일 · 잘린 녹음의 1차와 재전사 둘: 작업마다 줄 하나 |
| FR-006 | 같은 파일 · 줄에 요청 ID·경로·파일 이름·숫자가 없다 |
| US3-1, SC-004 | `test_hardening_us3.py` · 소켓 없음·`not-started`·「시작」 전 끊김: 응답 `engine_error`·「The server could not start the audio reader.」, 줄 `<id> failed engine_error reader_unavailable` |
| US3-2, SC-004 | 같은 파일 · 녹음 없음·디렉터리·FIFO·권한 000: 같은 응답, 줄 `… recording_unusable`, 읽기 인스턴스를 띄우지 않음. FIFO 도 곧 끝난다 |
| US3-3, FR-009 | 같은 파일 · 일반 파일 아님 작업 뒤 같은 실행에서 다음 작업이 `done`, 워커 끝 코드 0 |
| US3-4, 경계 6 | 같은 파일 · `engine_error` 줄이 모두 `^<uuid> failed engine_error (reader_unavailable\|recording_unusable\|unexpected\|engine_failed\|engine_no_verdict)$`. `os.pipe2` 의 EMFILE → `unexpected`, 엔진 판정 → `engine_failed`, 판정 없음 → `engine_no_verdict`. `<!-- stt:engine-error-causes -->` 집합 == `worker.ENGINE_ERROR_CAUSES` |
| US4-1, FR-010 | `test_hardening_us4.py` · 하위 프로세스 감독(`reader_harness.py`) + 대역 자식 `sigint-parent`: 판정 `ok`, 끝 코드 0, 표준 오류에 트레이스백 없음 |
| US4-3, FR-012 | 같은 파일 · `_write_audio` 가 만든 직후 경로가 지워지면 경로에 파일이 다시 생기지 않는다. 정상이면 0600·WAV 머리 그대로 |
| US4-4, FR-013 | `tests/server/test_server_cli.py` 의 「끝 코드 2」 시험을 고침 · 표준 오류가 `MANUAL_RUN_LINE` 한 줄, 표준 출력 없음, 끝 코드 2 |
| US4-5, FR-014 | `test_hardening_us4.py` · 현재 폴더에 `json.py` 를 심고 `reader.DEFAULT_CHILD_COMMAND`·`worker.DEFAULT_ENGINE_CMD` 를 그 폴더에서 띄움: 심은 모듈이 읽히지 않음. 콘솔 스크립트 `stt` 도 같은 폴더에서 읽지 않음(이미 참 — 회귀 지킴) |
| FR-015, SC-006 | 기존 001·002 시험 전부, `git diff --stat master -- src/stt/server/schemas` 가 비어 있음 |

## B. Nix 검사

```sh
captest 8G 15m nix build .#checks.x86_64-linux.module-eval -L
captest 8G 60m nix build .#checks.x86_64-linux.server-vm -L
```

### B-003-6 — systemd 판 단언 (US4-2, FR-011)

- `module-eval` 이 평가된다(빌드 산출물 없음): systemd `256.10` → 실패한 단언 문장이 `services.stt needs systemd 257 or later` 로 시작하고 `256.10` 을
  담는다. `257` 과 고정된 nixpkgs 의 판(261.1)은 그 문장이 없다([contracts/module.md](contracts/module.md) §2).
- CI 의 `nix flake check --no-build` 도 이 검사를 평가한다. `[VERIFY]` 그 평가 시간·메모리.

### B-003-1 — 진짜 읽기 단위가 음성 해독기만 연다 (US1-1~5, SC-001·SC-002, 경계 1)

VM 의 읽기 단위에는 해독기 감시 라이브러리가 실려 있다([contracts/module.md](contracts/module.md) §3). 진짜 읽기 단위에서 비음성 해독기가 열리면 해독
자식이 SIGABRT 로 끝나고 감독이 `reader child <op> killed by signal 6` 를 쓴다.

1. B-P2-8(형식 8가지)에 더한 녹음기 코덱 다섯(`tone-adpcm-ima.wav`·`tone-adpcm-ms.wav`·`tone-mp2.mp3`·`tone-ac3.m4a`·`tone-eac3.m4a`)을 더해, 감시 아래에서
   모두 probe·decode `ok`, 2초 ± 0.35, 최대 진폭 0.08~0.2 이고 `type=1326`·`reader child` 줄이 없다(US1-1, research R2). 목록을 넓히기 전(22개)에는 이
   다섯이 `failed` 다(RED).
2. `tone-video.m4a`·`tone-video.wma`·`tone-cover.mp3` 를 `readFormats` 로 `stt` 가 읽는다: probe·decode 모두 `ok`, 2초 ± 0.35, 최대 진폭 0.08~0.2.
   마지막 인스턴스가 끝난 뒤 `journalctl --sync` 하고 커서 이후에 `type=1326` 과 `reader child` 줄이 없다(US1-2·3, SC-001).
3. `video-only.m4a`·`tone-speex.ogg`: probe·decode 모두 `failed`(`readFormats` 는 둘 다 부른다). 커서 이후 저널에 `reader child probe exited with status 1`
   과 `reader child decode exited with status 1` 이 파일마다 하나씩 있고 `killed by signal` 은 없다(US1-4·5 — 해독기를 열지 않고 거절).
4. RED: 해독기 목록을 넣기 전에 2 를 돌리면 `killed by signal 6` 가 나오고 probe 가 `failed` 다. `[VERIFY]` 감시 라이브러리가 단위의 제한 안에서
   실리는지는 이 RED 로 확인한다.

### B-003-2 — 끝이 잘린 녹음의 줄 (US2-1·2·4, SC-003)

1. `cut-tone.mp3`·`cut-tone.flac`·`cut-faststart.m4a`·`nopackets-faststart.m4a` 와 대조 `tone10.mp3` 를 한 묶음으로 맡긴다. 모두 받아들여진다(`queued`).
   VM 에는 GPU 가 없어 모두 `gpu_unavailable` 로 끝난다(줄은 GPU 단계 전에 남는다).
2. 문서의 확인 명령을 커서 이후로 좁혀 돌린다:
   `journalctl -u stt-worker -g ' running$|shorter than the probed length'`. 잘린 넷은 각자의 `<id> running` 바로 다음 줄이 잘림 줄이고, `tone10.mp3` 의
   `<id> running` 다음에는 없다. 잘림 줄의 수는 정확히 4다.
3. `[VERIFY]` 빌드 안에서 `head -c` 로 자른 파일이 이 세션의 측정과 같게 읽힌다(처음 잰 길이 10초 안팎, 해독은 반쯤).

### B-003-3 — 서버 오류의 원인 이름 (US3-1·2·3, SC-004)

1. 기존 B-P2-5 의 `reader-not-started` 대역: 처리에서 `engine_error` 인 작업의 워커 줄이 `<id> failed engine_error reader_unavailable` 이다
   (`journalctl -u stt-worker -g 'failed engine_error'`).
2. 녹음 문제: 운영자가 GPU 잠금을 쥔 동안(`hold_gpu`) 작업을 맡겨 `queued` 로 둔 뒤, root 로 그 작업의 `audio/<id>/audio.wav` 를 지우고 같은 이름의
   FIFO(`mkfifo`)를 둔다. 잠금을 풀면 그 작업은 `engine_error`·「The server could not start the audio reader.」(1 과 같은 응답)이고 줄은
   `… recording_unusable` 이다. 워커는 막히지 않고, 바로 뒤의 정상 작업이 `gpu_unavailable` 까지 가며 `systemctl --failed` 에 stt 단위가 없다.

### B-003-4 — 손으로 띄운 읽기 명령 (US4-4)

- `${package}/bin/stt server read </dev/null` (root): 끝 코드 2, 표준 오류가 `MANUAL_RUN_LINE` 한 줄.

### B-003-5 — 회귀

- 기존 부분 시험이 모두 통과한다(B-P1·B-P2 전부, 감시 라이브러리가 실린 채로). 읽기 단위의 설정 단언(B-P2-1)은 바뀌지 않는다.

## C. 호스트 시험 (운영자와, miniecoinfo)

배포: `T-Verify-Final` 뒤 master 합치기와 push(운영자 승인) → nixos-config 의 `stt` 입력 갱신 → `nxr`. 배포가 단언을 통과한다(systemd 262).

1. **C-003-1 적용 확인**: `systemctl show -p MainPID stt-worker.service` 가 스위치 전과 다르거나 0(보안 문서 §3 배포 메모). 읽기 단위 설정은 002 와 같다
   (002 contracts/module.md §4 의 명령).
2. **C-003-2 기준 녹음**(SC-002, SC-003 뒤): `clip60.m4a`·`hour.m4a` 를 1차·2차로 전사해 `transcript.txt` 와 `result.json` 의 `paragraphs` 를 2026-11-10
   배포 뒤 결과(002 C-P2-5 의 보관본)와 비교한다. 같아야 한다 — 이 세션에서 두 녹음의 해독 바이트가 목록 유무와 무관하게 같았다(research R1).
   그 시각의 `journalctl -u stt-worker -g 'shorter than the probed length'` 가 비고, `journalctl -k -g 'type=1326'` 이 비어 있다.
3. **C-003-3 영상·표지 그림**(SC-001 뒤): 운영자가 호스트의 `ffmpeg` 로 사인파 + 단색 영상 `m4a` 와 사인파 MP3 + 단색 PNG 표지를 만들고, 보안 문서 §3 의
   「안전한 재확인」 스니펫으로 배포된 읽기 소켓을 거쳐 읽는다: `ok … ok` 와 맞는 길이, 같은 시각에 `reader child` 줄과 `type=1326` 없음. (선택) 같은 파일을
   1차 작업으로 맡겨 `done` 을 본다.
4. **C-003-4 (선택) 잘린 녹음**: 사인파 10초 MP3 를 반으로 잘라 1차로 맡긴다 → `done` 이고 그 시각에 잘림 줄 하나.
5. **C-003-5 손 실행**: 셸에서 `stt server read` 를 띄우면 안내 한 줄이 나오고 끝 코드가 2 다(`echo $?`).
6. **C-003-6 회신**(운영자 결정 2026-11-10): `specs/003-reader-hardening/handoff/reply.md` 를 쓰고 운영자가 bhu-llm 대장으로 옮긴다. 내용:
   - 계약(스키마·실패 이유·고정 문장·지우기·재전사·23:58)은 바뀌지 않았다.
   - 한 단락: 서버가 여는 해독기는 이제 음성 해독기 목록(research R2, 보안 문서 §2)뿐이다. 받는 녹음 형식은 그대로이고 영상 트랙이 든 녹음은 음성만 읽는다.
     첫 음성 트랙이 Speex, GSM, 그 밖의 목록 밖 음성 코덱(예: MPEG Layer I, TrueSpeech, IMA·Microsoft 밖의 ADPCM — G.726·G.722·Yamaha·QuickTime IMA4)이면
     받을 때 바로 `unreadable_audio`(「The recording could not be read as audio.」)로 거절된다. 002 까지는 이런 녹음도 전사됐다.
   - 배포 커밋·날짜와 C-003-2·C-003-3 의 결과.
