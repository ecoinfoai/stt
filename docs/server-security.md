# 서버 보안 기록 — 단위별 제한, 녹음 읽기 격리, 판 기록

이 문서는 `002-decoder-isolation` 기능이 요구하는 운영 기록이다. `tests/test_server_security_doc.py` 가 표식 주석(`<!-- stt:… -->`),
영어 칸 이름, 영어 상태값, ISO 날짜만 읽고 저장소의 값과 대조한다. 이유 같은 자유 문장은 한국어로 쓴다.

## 1. 단위별 제한과 예외

<!-- stt:syscall-table -->

| unit | filter | added | narrowed | reason | observed | mdwe | mdwe_evidence |
|---|---|---|---|---|---|---|---|

## 2. 녹음 읽기 격리

## 3. 작업 실패와 단위 비정상 종료 구별

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
