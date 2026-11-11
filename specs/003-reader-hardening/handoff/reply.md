# 003 회신 — 녹음 읽기 추가 좁히기와 알아차리기

운영자가 bhu-llm 대장 A18 로 옮긴다. 002 회신은 [../../002-decoder-isolation/handoff/p2-reply.md](../../002-decoder-isolation/handoff/p2-reply.md).

## 1. bhu-llm 이 알 것

- **계약은 바뀌지 않았다.** 요청·응답·결과 파일의 모양과 경로(스키마 파일은 002 와 바이트까지 같다), 실패 이유의 목록, 고정 문장, 「다 지우기」,
  `audio.from_request` 재전사, 23:58 정리 모두 그대로다. stt-bridge 코드는 고칠 것이 없다.
- **거절되는 코덱.** 서버가 여는 해독기는 이제 음성 해독기 목록(`docs/server-security.md` §2.1, 32개)뿐이다. 받는 녹음 형식(확장자)은 그대로이고,
  영상 트랙이 든 녹음(영상 강의의 MP4, 표지 그림이 든 MP3)은 음성만 읽어 전사한다. 첫 음성 트랙이 Speex, GSM, 그 밖의 목록 밖 음성 코덱
  (예: MPEG Layer I, TrueSpeech, IMA·Microsoft 밖의 ADPCM — G.726·G.722·Yamaha·QuickTime IMA4)이면 받을 때 바로 `unreadable_audio`
  (「The recording could not be read as audio.」)로 거절된다. MP3 라고 적힌 상자(예: `.mp3` 안 MPEG Layer I)는 길이 재기를 지나므로 처리 때 같은
  이유로 끝난다. 002 까지는 이런 녹음도 전사됐다. 배포한 날(2026-11-11) 이미 받아 둔 그런 녹음의 재전사는 해독에서 `unreadable_audio` 가 된다(그날 23:58 까지).
- **바뀌지 않은 것.** 끝이 잘린 녹음은 지금처럼 앞부분 전사로 `done` 이다. 새로 생긴 것은 서버 운영자 기록의 한 줄뿐이다(아래 3). bhu-llm 은 지금처럼
  결과의 `summary.audio_duration_s`(실제로 해독된 길이)로 알아볼 수 있다.

## 2. 배포(T033)

- 2026-11-11: stt `master` 를 `0cfae45` 에서 `f02120a` 로 앞당겨 push 했다(운영자 승인). CI(Python 3.10·3.12 pytest, `nix flake check --no-build`)
  통과(<https://github.com/ecoinfoai/stt/actions/runs/42930683429> — `nix flake check --no-build` 30 초). 운영자가 nixos-config 의 `stt` 입력을 `f02120a` 로 갱신하고 `nxr` 했다.
  스위치는 systemd 판 단언(257 이상)을 통과했다(호스트 systemd 262).
- 배포 전 시험: Python 3.12 1338 통과·2 건너뜀(AMR-NB 인코더 없음), 3.11·3.10 각 1340 통과, VM 시험(`server-vm`) 통과, `module-eval` 통과.

## 3. 확인 결과(T034 — 2026-11-11 11:12–11:27 KST)

| 확인 | 결과 |
|---|---|
| C-003-1 적용 | 잠금의 `stt` 가 `f02120a`. 배포된 서버 환경의 `reader.py` 에 해독기 목록이 있다. 읽기 단위의 설정(메모리·시간·DynamicUser·망·장치·파일시스템 제한)은 002 와 같다. 실패한 stt 단위 없음 |
| C-003-2 기준 녹음 | 아래 표. 그 시각 잘림 줄은 잘린 시험 녹음의 하나뿐이고 `type=1326` 없음 |
| C-003-3 영상·표지 그림 | 호스트 `ffmpeg` 로 만든 사인파 정상 파일·사인파 + 단색 영상 `m4a`(영상 트랙이 첫째)·사인파 MP3 + 단색 PNG 표지를 「안전한 재확인」으로 읽어 셋 다 `ok 2.0 ok 2.0 s`. 그 시각 `reader child` 줄·`type=1326` 없음. 영상 녹음을 1차로 맡겨 `done`(해독 길이 2.0 초) |
| C-003-3 목록이 켜졌는가 | 사인파 Speex(`libspeex`, ogg)를 같은 방법으로 읽어 `failed None failed 0.0 s`. 그 시각 `reader child probe exited with status 1` 과 `reader child decode exited with status 1` 이 하나씩, `killed by signal` 없음. 002 코드라면 `ok` 였을 파일이다 |
| C-003-4 잘린 녹음 | 사인파 10초 MP3 를 바이트 반으로 자른 것을 1차로 → `done`(해독 길이 4.99 초). 워커 기록에서 그 작업의 `running` 바로 다음에 `decoded audio is shorter than the probed length` 한 줄. 자르지 않은 대조 MP3(10.0 초)와 다른 작업 6개에는 그 줄이 없다 |
| C-003-5 손 실행 | 셸에서 `stt server read` → 안내 한 줄(`stt server read only serves a connection that systemd passes from stt-reader.socket on standard input`), 끝 코드 2 |
| 해독기 이름(R2) | 배포된 해석기에서 목록 32개가 모두 FFmpeg 의 음성 해독기로 열린다. 시험 파일이 없는 `wmapro`·`wmavoice`·`wmalossless`·`amrwb` 도 이름은 있다 |

### 기준 녹음 비교(C-003-2)

002 배포 뒤 결과(2026-11-10)는 따로 보관하지 않았다. 그래서 2026-11-09 기준본(C-0)과 비교하고, 2026-11-10 에 운영자가 받아들인 차이와 같은지 본다.

| 녹음·방식 | 문단 | 2026-11-09 기준과 비교 | 2026-11-10 결과와 |
|---|---|---|---|
| 60초 1차 | 1 | 같다 | 같다 |
| 60초 2차(화자 구분·힌트 4) | 1 | 같다 | 같다 |
| 1시간 1차 | 37 | 마지막 문단(58:50)의 낱말 하나만 다르다(「왕납니다」→「왕래입니다」) | 같다(2026-11-10 의 차이와 같은 낱말 하나) |
| 1시간 2차(화자 구분·힌트 7) | 57 | 같다. 힌트 반영 7/7 | 같다 |

- `transcript.txt` 는 위 차이 말고는 머리의 원본 이름(요청 ID) 한 줄만 다르다. 해독 길이는 3600.056 초(002 와 같다).
- 처리 시간: 1시간 1차 4분 26초, 2차 5분 57초(002: 4분 25초, 5분 53초).

## 4. 남은 운영 확인

- **WMA Pro·Voice·Lossless, AMR-WB 녹음**: 인코더가 없어 시험 파일을 만들 수 없다. 끝까지 읽는 확인은 그런 녹음이 처음 들어올 때 한다(통과 기준:
  비어 있지 않은 전사록이고 그 시각에 일찍 멈춤·잘림·실패 줄이 없음). bhu-llm 이 그런 녹음을 알게 되면 코덱 이름과 시각을 운영자에게 알려 주면 된다 —
  `.wma`·`.m4a` 확장자만으로는 넷 중 어느 것인지 알 수 없다. 그 전까지 넷은 「알려질 때까지 확인 안 됨」이다(`docs/server-security.md` §4.4).
- **stt 쪽 별도 과제(bhu-llm 할 일 없음)**: 서버의 작업 기록 파일 하나가 깨지면 대기열·새 요청·지우기·정리가 멈춘다(001 부터의 동작, 003 이 바꾸지 않음).
  운영자가 알아보고 푸는 법은 `docs/server-security.md` §3 에 있다. 이 때문에 bhu-llm 이 보는 증상은 「응답이 오지 않음」이다.

## 5. 물을 것

없다.
