# P1 회신 — 네 단위의 시스템 호출 제한

이 문서는 [data-model.md](../data-model.md) §9 의 P1 열을 따른다. 운영자가 bhu-llm 대장 A18 로 옮긴다(2026-11-09).

## 기준 결과(C-0, T001)

- 보관한 날: 2026-11-09(21:11–21:22 KST 전사, 배포된 stt `ce5c325`, 필터 없음).
- 보관한 곳: miniecoinfo 의 `~/stt-baseline-002/2026-11-09/`(0700, 저장소 밖). 녹음 두 개와, 네 전사(60초·1시간 × 1차·2차)마다
  요청·응답·`transcript.txt`·`result.json`.

## 관측(C-P1-1, T002)

- 2026-11-09 21:22:46–21:25:01 KST, 배포된 stt `ce5c325`. 네 단위(`stt-intake`·`stt-worker`·`stt-sweep`·`stt-models`)에
  `SystemCallLog=~@system-service` 를 `/run` 의 임시 drop-in 으로 걸었다.
- 거친 일: 60초 녹음 1차(`done`), 2차(화자 구분·힌트 4, `done`), 녹음 확장자를 붙인 텍스트 첨부(`unreadable_audio`), 「다 지우기」 세 번
  (요청자가 다른 두 번은 `refused` — 정상), 청소 한 번(21:25:00), `systemctl restart stt-models`(성공).
- `journalctl -k -g 'type=1326'`: 0줄. 대조: 같은 설정의 일회용 단위가 묶음 밖 호출 101 을 부르자 `code=0x7ffc0000`(기록만) 줄이 남았다.
- 결론: 네 단위 모두 묶음 밖 호출이 없어 예외를 더하지 않는다. drop-in 은 지우고 다시 읽혔다.

## 배포(T008)

- 2026-11-09: stt `master` 를 P1 커밋 `be72910` 으로 앞당겨 push 했다. 운영자가 nixos-config 의 `stt` 입력을 `be72910` 으로 갱신하고 `nxr` 했다.
- 배포 전 확인: 같은 P1 제한(필터·`native`·쓰기·실행 메모리 금지)을 건 일회용 단위에서 60초 녹음 2차(화자 구분·힌트 4) 엔진이 CUDA 로
  `done`, 문단이 기준과 같았다.

## 확인 결과(C-P1-2, T009)

- `systemctl show`(contracts/module.md §4, 네 단위): 모두 `User=stt`, `DynamicUser=no`, `SystemCallFilter=` 풀린 호출 392개(맨 `~` 아님),
  `SystemCallArchitectures=native`, `MemoryDenyWriteExecute=yes`. `PrivateDevices` 는 워커 `no`, 나머지 셋 `yes`.
- 재전사 비교(22:09–22:21 KST): 60초·1시간 × 1차·2차 넷 모두 `done`(`cuda`), `result.json` 의 `paragraphs` 가 기준과 같다(1·1·37·57문단).
  `transcript.txt` 는 머리의 원본 이름(요청 ID) 한 줄만 다르다. 힌트 반영 4/4·7/7.
- 녹음 확장자를 붙인 텍스트 → `unreadable_audio`. 같은 녹음의 재전사(`from_request`) 3번 모두 `done`.
- 「다 지우기」 두 번 `deleted`(결과·녹음 폴더 없음), 청소(22:23:00) 성공, `systemctl restart stt-models` 성공, `systemctl --failed` 비어 있음,
  `journalctl -k -g 'type=1326'`(22:09:18 부터) 0줄, `work`·`queue`·inbox 비어 있음.
- 워커의 쓰기·실행 메모리 금지는 CUDA 에서 거부되지 않았다 → `mdwe=on`.

## 요청 1 — 단위별 필터·아키텍처·예외·쓰기·실행 메모리 금지

| 단위 | `SystemCallFilter` | `SystemCallArchitectures` | 더한 예외 | `MemoryDenyWriteExecute` |
|---|---|---|---|---|
| `stt-intake` | `@system-service` | `native` | 없음 | 켬 |
| `stt-worker` | `@system-service` | `native` | 없음 | 켬 |
| `stt-sweep` | `@system-service` | `native` | 없음 | 켬 |
| `stt-models` | `@system-service` | `native` | 없음 | 켬 |

- 예외가 없는 근거: 위 「관측」에서 네 단위 모두 묶음 밖 호출 0. CUDA 를 쓰는 워커도 예외가 필요 없었다.
- 쓰기·실행 메모리 금지: 받기는 켤 수 있다(켰다). 워커도 CUDA·화자 구분과 함께 켠 채로 1시간 녹음까지 결과가 기준과 같아, 불가피한 예외가 아니다.
- 목록 밖 호출은 그 프로세스를 SIGSYS(31)로 끝낸다(오류 번호로 돌려주지 않음). 기록은 `docs/server-security.md` §1.

## 요청 2·3 — 어느 단계가 어디서 도는가(지금과 P2 계획)

**지금(P1 만 배포)**
- 길이 재기: 받기(`stt-intake`, `stt` 계정, GPU 장치 없음) 프로세스 안의 PyAV.
- 해독: 워커(`stt-worker`, `stt` 계정, NVIDIA 장치 열림)가 띄운 엔진 프로세스 안의 PyAV. 화자 구분용 WAV 변환의 ffmpeg 도 같은 단위다.
- 저장: 모든 작업이 `stt` 계정의 `/var/lib/stt` 를 함께 쓴다. 작업별 분리는 아직 없다.

**P2(구현·VM 시험 끝, 아직 배포 전 — P3 문서와 함께 배포 예정)**
- 녹음 읽기(길이 재기와 해독)를 소켓으로 띄우는 일회용 단위 `stt-reader@.service` 로 옮긴다. 연결 하나가 인스턴스 하나이고 끝나면 지워진다.
- 받기·워커는 녹음을 열어 **읽기 전용 기술자**만 넘긴다. 읽기 단위는 경로를 쓰지 않고, `/var/lib/stt`·`/var/lib/stt-models`·`/run/stt` 에
  닿지 못한다(권한 + `InaccessiblePaths`). 동적 UID(`stt` 아님), GPU 장치 없음, 망·소켓 생성 없음, 다른 프로세스 보이지 않음, 메모리 768M·시간 상한.
- GPU 단계(워커의 엔진)는 해독된 16 kHz WAV 와 작업 설정만 받고 녹음을 해독하지 않는다.
- 요청 3(작업별 저장 분리)에 대한 답: 저장 위치를 작업마다 나누는 대신, 녹음을 해석하는 프로세스가 **자기 녹음 하나의 기술자 말고는 저장 위치에
  닿지 못하게** 한다. 장악된 읽기 프로세스가 그날 다른 이용자의 녹음·전사록을 읽거나 바꾸는 길이 이것으로 닫힌다.
  읽기 인스턴스끼리는 같은 동적 UID 를 쓰지만 이름공간으로 서로 보지 못한다(운영자 결정 2026-11-09).

## 요청 4 — 구성 요소의 판과 날짜

녹음 읽기에 쓰이는 것(서버 경로, 배포된 판에서 직접 확인 2026-11-09):

| 구성 요소 | 하는 일 | 지금 판 | 고정한 곳 | 들어온 날 |
|---|---|---|---|---|
| PyAV(내장 FFmpeg 8.1.2) | 길이 재기·해독 | 18.0.0 | stt `uv.lock`(Python 3.12) | 2026-07-31 |
| faster-whisper | 해독 호출·전사 | 1.2.1 | stt `uv.lock` | 2026-07-31 |
| CTranslate2 | 전사(GPU) | 4.8.1 | stt `uv.lock` | 2026-07-31 |
| torch | 화자 구분(GPU) | 2.13.0+cu130 | stt `uv.lock` | 2026-08-03 |
| pyannote.audio | 화자 구분 | 4.0.7 | stt `uv.lock` | 2026-08-03 |
| ffmpeg(명령) | 화자 구분용 WAV 변환 | 8.1.2 | stt `flake.lock` 의 nixpkgs `148bab9c`(2026-08-01) | 2026-08-02 |

- 마지막 갱신: `uv.lock` 2026-10-22(`eea5374`, 위 판은 그대로), `flake.lock` 2026-10-22(`5afc555`, nixpkgs 그대로).
- 갱신 정책(누가·얼마 주기로·무엇을 보고)과 첫 매달 확인 기록은 P3 로 `docs/server-security.md` §4 에 싣고 P2 회신에서 알린다.

## §5 질문

- 계약: 요청·응답·결과 파일의 모양과 경로, 「다 지우기」의 의미, `from_request`, 23:58 정리는 P1·P2 모두 바뀌지 않는다. P1 은 단위 설정만 바꿨다.
  호스트에서 재전사 3번과 「다 지우기」가 그대로 된 것을 확인했다.
- 조작 파일: 녹음 확장자를 붙인 텍스트는 P1 뒤에도 `unreadable_audio` 로 끝나고 다른 작업·단위는 영향이 없다(호스트 확인).
- 단위 비정상 종료와 작업 실패의 구별(새 알림 길은 없음):
  - 작업 하나의 실패: 응답의 `failure.reason`, `journalctl -u stt-worker -u stt-intake` 의 `<request_id> failed <reason>`.
  - 단위의 비정상 종료: `systemctl --failed`, `systemctl show -p Result -p ExecMainCode -p ExecMainStatus <unit>`. OOM 이면 `Result=oom-kill`,
    호출 필터면 SIGSYS(31), 막힌 호출 번호는 `journalctl -k -g 'type=1326'`.
  - 자세한 표는 `docs/server-security.md` §3. 서버 전체의 `OnFailure` 알림은 nixos-config 의 별도 과제다.

## 질문 6 — 보관 기간

stt 는 001(2026-10-22 결정)부터 녹음을 「다 지우기」나 그날 23:58 정리까지 둔다고 가정했다. 그래서 bhu-llm 029 의 보관 확대(재전사를 위해
그날 23:58 까지)는 stt 의 삭제 설계를 바꾸지 않는다. 그날 쌓인 다른 녹음에 닿는 위험은 P2 가 없앤다 — 읽기 단위는 자기 녹음 하나만 쥔다.

## 남는 위험

P1 만 배포된 동안(P2 배포 전까지):
- 해독은 아직 `stt` 계정으로, 모든 작업이 함께 쓰는 `/var/lib/stt` 에 닿는 프로세스에서 돈다. 해독기를 장악하면 그날 다른 이용자의 녹음·전사록에
  닿을 수 있다(인계 §3-1). 호출 필터와 쓰기·실행 메모리 금지가 장악과 커널 공격을 어렵게 할 뿐 이 길을 닫지는 않는다.
- 전사용 해독은 NVIDIA 장치가 열린 워커 단위에서 돈다(인계 §3-3).
- 망이 없어 밖으로 빼낼 길이 없는 것은 그대로다.

P2 뒤에도 남는 것(운영자가 받아들임, 2026-11-09): 읽기 단위는 위에서 닫은 경로 밖의, 누구나 읽을 수 있는 호스트 파일은 읽을 수 있다.
