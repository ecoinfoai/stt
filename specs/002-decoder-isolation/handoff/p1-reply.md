# P1 회신 — 네 단위의 시스템 호출 제한 (초안)

이 문서는 [data-model.md](../data-model.md) §9 의 P1 열을 따른다. T010 에서 완성해 운영자에게 넘긴다.

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

## 요청 2·3 — 어느 단계가 어디서 도는가(지금과 P2 계획)

## 요청 4 — 구성 요소의 판과 날짜

## §5 질문

## 질문 6 — 보관 기간

## 남는 위험
