# 서버 보안 기록 — 단위별 제한, 녹음 읽기 격리, 판 기록

이 문서는 `002-decoder-isolation` 기능이 요구하는 운영 기록이다. `tests/test_server_security_doc.py` 가 표식 주석(`<!-- stt:… -->`),
영어 칸 이름, 영어 상태값, ISO 날짜만 읽고 저장소의 값과 대조한다. 이유 같은 자유 문장은 한국어로 쓴다.

## 1. 단위별 제한과 예외

<!-- stt:syscall-table -->

| unit | filter | added | narrowed | reason | observed | mdwe | mdwe_evidence |
|---|---|---|---|---|---|---|---|
| `stt-intake` | `@system-service` | - | - | - | - | on | - |
| `stt-worker` | `@system-service` | - | - | - | - | pending | - |
| `stt-sweep` | `@system-service` | - | - | - | - | on | - |
| `stt-models` | `@system-service` | - | - | - | - | on | - |

- 네 단위는 `nix/module.nix` 의 공통 묶음 `hardening` 에서 같은 값을 받는다: `SystemCallArchitectures=native`,
  `SystemCallFilter=@system-service`, `MemoryDenyWriteExecute=true`. `SystemCallErrorNumber` 는 두지 않아, 목록 밖의 호출은 SIGSYS(31)로
  그 프로세스를 끝내고 커널 기록에 호출 번호가 남는다.
- 더한 예외는 아직 없다. 호스트에서 필터를 걸지 않은 채 실제 전사를 관측하는 일(R2)은 이 표를 채운 시점에 하지 않았다. 관측으로 묶음 밖 호출이
  나오면 그 호출만 해당 단위의 `added` 에 적고, `reason`(무엇이 쓰는가)과 `observed`(관측한 날, 호출 번호)를 채운 뒤에 `nix/module.nix` 에
  이 표의 같은 줄을 가리키는 주석과 함께 더한다. 표에 없는 예외는 두지 않는다.
- 워커의 쓰기·실행 메모리 금지(`mdwe`)는 `pending` 이다. 호스트에서 켠 채로 1차·2차 전사를 돌려 보고 `on` 또는 `off`(거부된 지점을
  `mdwe_evidence` 에 적음)로 확정한다. 나머지 세 단위는 VM 시험이 켠 채로 통과한 것을 근거로 `on` 이다.

## 2. 녹음 읽기 격리

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

호출 번호는 `scmp_sys_resolver <번호>` 로 이름을 찾는다. 서버 전체의 `OnFailure` 알림은 nixos-config 의 별도 과제다.

## 4. 판 기록과 갱신 정책

### 4.1 구성 요소 판 기록

<!-- stt:versions -->

| component | role | version | pinned_in | since | last_updated | comparison |
|---|---|---|---|---|---|---|

### 4.2 갱신 정책

### 4.3 보안 공지 기록

<!-- stt:advisories -->

| advisory | component | published | updated | reason |
|---|---|---|---|---|

### 4.4 매달 확인 기록

<!-- stt:monthly-checks -->

| date | sources | result |
|---|---|---|
