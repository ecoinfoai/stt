<!--
Sync Impact Report — 2026-10-22 (amendment)
- Version change: 1.0.0 → 1.1.0 (MINOR — two principles tightened, none removed)
- III: deletion is either an explicit 「다 지우기」 request or the daily 23:58 KST purge; the per-job delete
  timing (「끝나면 바로」/「자정」), result-only delete and the 24 h result clock are gone. The 24 h ceiling stays.
- V: the CLI no longer falls back to CPU (the CPU-fallback warning example is removed); GPU only.
- Source: operator decisions 2026-10-22.
- Templates: no change. Follow-up: spec/plan/data-model/contracts/tasks of 001-server-transcription.

Sync Impact Report — 2026-10-22
- Version change: (unratified template) → 1.0.0
- Ratification: initial adoption. 원칙은 운영자 결정(2026-10-15 bhu-llm 핸드오프 결정 #1~#5,
  2026-10-21~22 서버 전사 아이디어 논의)과 저장소에 이미 적힌 정책(README Notes, idea/*)에서 끌어냈다.
- Principles defined:
  I. 최소 역할 (Minimal Role)
  II. 교내 처리 (Local Processing — Audio Never Leaves)
  III. 삭제 보장과 쌓지 않음 (Guaranteed Deletion, No Accumulation)
  IV. 전사록 원문 무결성 (Transcript Integrity)
  V. 말없는 실패 금지 (No Silent Degradation)
  VI. 계약으로만 만난다 (Contract-Only Integration)
  VII. 테스트 우선 (Test-First) — NON-NEGOTIABLE
- Added sections: 저작권·단어장·개인정보 제약; 개발 환경과 흐름; Governance
- Removed sections: none (all template placeholders replaced)
- Templates requiring updates:
  ✅ .specify/templates/plan-template.md — "Constitution Check" 가 헌법을 동적 참조
     (`[Gates determined based on constitution file]`); 편집 불필요
  ✅ .specify/templates/spec-template.md — Given/When/Then 수용 시나리오·필수 섹션이 원칙과 맞음; 편집 불필요
  ✅ .specify/templates/tasks-template.md — 「Tests are OPTIONAL」이 원칙 VII 과 충돌해 고쳤다:
     시험 과제 의무화, RED → GREEN 표기, 과제마다 닫는 시나리오 명시
  ✅ .claude/skills/speckit-*/SKILL.md — 헌법을 일반적으로 참조, 에이전트 고유 이름 잔재 없음; 편집 불필요
  ✅ README.md · CLAUDE.md — 원칙을 인용하는 문장 없음; 편집 불필요
  ✅ specs/001-server-transcription/spec.md — 원칙 I~VII 과 대조, 충돌 없음(FR-006·FR-012·FR-015·FR-030~036·FR-040)
- Decision (operator, 2026-10-22): 코드는 영어로 쓰고 코드에는 한글을 쓰지 않는다 — 전역 규칙
  (`~/.claude/CLAUDE.md` §2)과 같다. 기존 코드의 한국어 docstring·주석·메시지는 이 결정의 위반 상태로 남아 있다.
- Follow-up TODOs:
  ⚠ 기존 코드의 한글을 영어로 옮기는 변경(별도 feature 또는 과제). `74cb016` 의 새 메시지도 포함된다.
  ⚠ TODO(KOREAN_PRODUCT_TEXT): 계약으로 정해진 한국어 산출물(화자 표시 `화자N`, 전사록 머리글)과 한국어
    시험 자료(용어 목록, 토크나이저 시험의 한국어 용어)를 코드 밖 어디에 둘지는 그 변경에서 정한다.
-->

# stt Constitution

stt 는 녹음·녹화를 교내 기기에서 시각이 붙은 한국어 전사록으로 바꾸는 도구다. 운영자의 명령줄 도구이자, bhu-llm 이
이용자 대신 녹음을 맡기는 교내 서버 전사 단위다. 녹음은 개인정보이고 전사록은 보고서·회의록의 인용 근거이므로, 이
헌법은 교내 처리·삭제·원문 무결성을 다른 편의에 우선해 보장한다. 모든 spec/plan/tasks 산출물과 구현은 여기에 부합해야 한다.

## Core Principles

### I. 최소 역할 (Minimal Role)

stt 가 하는 일은 넷이다: 녹음을 **받는다**, 들은 그대로 **받아 적는다**(화자 구분과 힌트 단어장 반영 포함), 정해진
형식으로 **돌려준다**, 정한 때에 **지운다**. 판단이 필요한 일(요약·화자 역할 추정·힌트 단어장 선별), 언어 모델 호출,
언어 모델 출력의 검증, stt 출력으로 계산할 수 있는 통계·보고는 stt 에 넣지 않는다(MUST NOT). 새 기능을 제안하는
spec·plan 은 「호출하는 쪽이 stt 의 결과로 이 일을 할 수 있는가」를 적어야 하고(MUST), 할 수 있으면 stt 에 넣지 않는다.
이미 있는 명령줄 명령(`fetch`·`transcribe`·`batch`·`enroll`)은 운영자의 도구로 남고, 이 원칙은 새 기능에 적용한다.

Rationale: 운영자 결정(2026-10-22) 「stt의 역할을 간결하고 분명하게. 여러 기능 추가하지 않습니다.」 GPU 한 장의
서버에서 개인정보를 다루는 단위는 작을수록 삭제·감사·시험이 참으로 남는다.

### II. 교내 처리 (Local Processing — Audio Never Leaves)

녹음과 녹음에서 만든 음성은 그것을 처리하는 기기(교내 서버 또는 운영자의 기기) 밖으로 나가지 않는다(MUST). 서버
처리 중 stt 는 외부에 접속하지 않으며(MUST), 필요한 모델은 미리 갖춰 둔다. stt 는 언어 모델 등 외부 서비스를
호출하지 않는다(MUST NOT). 서버 경로는 이용자가 맡긴 파일만 받는다 — 영상 사이트 내려받기(`stt fetch`)는 운영자의
명령줄 전용이다.

Rationale: 녹음에는 다른 참석자의 목소리와 발언이 있다. 밖으로 나가는 것은 호출하는 쪽이 정리를 위해 보내는
전사문뿐이고, 그 경계를 stt 가 흐리면 동의 문안과 안내가 거짓이 된다.

### III. 삭제 보장과 쌓지 않음 (Guaranteed Deletion, No Accumulation)

서버 경로에서 녹음·처리 중 만든 음성·힌트 단어장 사본·결과는 호출하는 쪽이 「다 지우기」를 요청하면 즉시, 아니면
매일 23:58(KST) 정리 때 모든 저장 위치에서 지운다 — 실패·멈춤·강제 종료된 작업도 같고, 어떤 자료도 **처음 받은 때로부터
24시간**을 넘지 않는다(MUST). 매일 정리는 처리 단위가 멈춰 있어도 이루어진다(MUST). 처리 중
원본은 암호화된 저장 위치에만 둔다(MUST). 이용자별 단어장·기록·목소리 특징·통계를 서버에 쌓지 않는다(MUST NOT).
서버 경로에서 화자는 `화자N` 으로만 적고, 목소리 등록·실명 붙이기를 하지 않는다(MUST NOT). 삭제는 정상·실패·강제
종료 경로마다 「남은 파일 0」을 확인하는 시험으로 증명한다(MUST).

Rationale: 운영자 결정(2026-10-15) 「음원 자체가 개인정보다. 전사가 끝나면 반드시 지운다」, 결정 #4(실명 없음),
#5(단어장을 서버에 남기지 않음). 삭제 위치가 한 곳이라도 빠지면 「반드시 삭제」가 거짓이 된다.

### IV. 전사록 원문 무결성 (Transcript Integrity)

전사록은 엔진이 들은 것이다. stt 는 전사록 본문을 고쳐 쓰지 않고, 언어 모델이 전사록 본문을 고치게 하지 않는다
(MUST NOT) — 언어 모델은 힌트 단어장을 고칠 뿐이고, 전사록은 엔진이 다시 만든다. 결정론적 교정 규칙을 도입한다면
모든 치환을 위치·교정 전·교정 후·적용 규칙과 함께 기록하고, 기록만으로 교정 전 전사록을 글자 그대로 복원할 수
있어야 한다(MUST). 화자 판정이 불확실한 문단은 표시한다(MUST). 문단마다 녹음 시각을 붙인다(MUST).

Rationale: 전사록은 강의록·회의록·지식 노트에서 원문 인용의 근거다(`transcript-report` 의 축자 인용 규칙). 한국어
전사에 언어 모델 교정을 얹으면 오류 없는 발화까지 망가진다는 근거가 있다
(`reports/전사 단어장 선별과 오인식 탐지.md` §2).

### V. 말없는 실패 금지 (No Silent Degradation)

엔진 한도 때문에 버려지는 힌트, 장치·모델의 대체, 일부만 된 결과를 알리지 않고 넘어가지 않는다(MUST NOT). 명령줄은
경고하고 계속할 수 있다(지금의 힌트 한도 경고). stt 는 명령줄에서도 GPU 로만 전사·화자 구분을 한다 — GPU 를 쓸 수
없으면 CPU 로 넘어가지 않고 이유를 알리고 멈춘다(MUST). 서버 경로는 품질을 낮춰 대신하지 않고 정해진 이유로
실패한다(MUST). 일부만 된 결과를 완료로 내지 않는다(MUST). 시스템 경계(작업·파일·인자·역직렬화)에서 즉시 검증하고
명시적 오류를 낸다(MUST). 신뢰된 내부 코드에 발생 불가능한 상태를 위한 방어 분기, `except: pass`, 무음 폴백을 두지
않는다(MUST NOT).

Rationale: 818토큰 단어장(137개, `f36b0fd` 2026-08-03)의 앞쪽 27%만 쓰이던 것이 두 달 동안 아무 표시 없이
지나갔다(2026-10-04 발견). 잘린
전사록을 완전한 것으로 믿고 회의록에 쓰면 되돌릴 수 없다.

### VI. 계약으로만 만난다 (Contract-Only Integration)

stt 와 호출하는 쪽(bhu-llm)은 서로의 코드를 가져다 쓰지 않고, stt 가 문서화하고 기계로 검사할 수 있게 제공하는 형식
정의(작업·결과·상태)로만 만난다(MUST). 형식을 바꾸면 판 번호를 올리고, 호환되지 않는 변경은 핸드오프 문서로
알린다(MUST). stt 는 bhu-llm 없이도 운영자의 명령줄 도구로 동작해야 한다(MUST). 다른 프로젝트의 세션은 이 저장소에
쓰지 않는다 — 넘길 일은 핸드오프 문서로 넘기고, 이 저장소의 문서는 이 저장소에서 쓴다(MUST).

Rationale: bhu-llm 방향 §19-7 「새 기능은 별도 프로젝트로, bhu-llm 은 도구로 부른다」와 운영자 결정(2026-10-15)
「bhu-llm 은 더 이상 stt 저장소를 건드리지 않는다」. 약속이 하나뿐이어야 두 저장소가 따로 바뀔 수 있다.

### VII. 테스트 우선 (Test-First) — NON-NEGOTIABLE

모든 기능과 버그 수정은 실패하는 시험을 먼저 쓰고 실패를 확인(RED)한 뒤, 최소 구현으로 통과(GREEN)시키고
리팩터한다(MUST). spec 의 수용 시나리오(Given/When/Then)마다 시험이 하나 이상 있어야 하며, 시험이 없는 시나리오는 끝난
것이 아니다(MUST). tasks 의 과제마다 RED → GREEN 과 자신이 닫는 시나리오를 적는다(MUST). 기본 시험은 GPU·망·모델
없이 돈다(가짜 모델). 실제 모델·토크나이저 시험은 자산이 없으면 이유와 함께 건너뛴다. 행동을 바꿀 수 없는
편집(오타·포맷·문서)만 시험 먼저의 예외다. 시험과 구현이 충돌하면 소스 로직을 먼저 고친다.

Rationale: 서버 전사는 개인정보 삭제와 이용자 결과를 다룬다. 회귀는 신뢰를 직접 깎으므로 검증 없는 코드를 금지한다.

## 저작권·단어장·개인정보 제약 (Copyright, Glossary & Personal Data Constraints)

- stt 는 교재 파일이나 교재 본문을 받지 않는다(MUST NOT). 단어장은 이용자가 시스템 밖에서 만든 용어 목록이다.
  「저작권 문제가 있는 자료를 올리지 마라」는 사용자 교육(DX지원센터)에서 반드시 안내한다.
- 단어장은 둘이다: 참조 단어장 (1)(교수가 교재에서 뽑은 정답 표기, 길이 제한 없음, 힌트로 쓰지 않음)과 힌트 단어장
  (2)(호출하는 쪽의 언어 모델이 1차 전사록과 (1)로 고른 용어). stt 서버 경로에는 (2)만 온다(MUST).
- 단어장·오인식 기록·교정 규칙은 그것을 낸 교수 단위로만 다루고, 같은 과목이라도 다른 교수의 것과 섞지 않는다(MUST).
- 미디어·메타데이터·전사록은 저장소에 커밋하지 않는다(`data/` 는 git 무시 대상). 전사록에는 제3자 강연 내용이 있어
  로컬에만 둔다(README Notes).
- 비밀은 agenix 로만 다룬다(MUST). 게이트 모델용 토큰은 모델을 받아 둘 때만 쓰고 서버 실행 중에는 필요 없게 한다.
  게이트 모델 파일은 공유 바이너리 캐시에 올리지 않는다.
- 사용자 입력이 섞인 값으로 `eval`·`exec`·셸 보간(`shell=True`)을 쓰지 않는다(MUST NOT).

## 개발 환경과 흐름 (Development Environment & Workflow)

- 개발 셸은 Nix flake, Python 패키지는 `uv` 로 다룬다. 서버 배포는 Nix 패키지와 NixOS 모듈로 하며, stt 는 자기
  nixpkgs 를 고정한다(bhu-llm·geo-scout 와 같은 방식).
- GPU 는 한 장이다. 서버 작업과 운영자의 명령줄 전사는 같은 줄에 서서 차례로 쓴다(MUST).
- 개발 위치는 miniecoinfo 다. stt 문서는 이 저장소에서 쓰고 커밋한다.
- 흐름: `idea/` → `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`. 운영자 결정은 날짜와 함께
  idea·spec 의 기록 절에 남긴다. 대화에만 남은 결정은 없는 결정이다.
- 커밋: Conventional Commits, 영어 명령형, 새 커밋만(고치기 금지), 허락 없이 `--no-verify` 금지.
- 최종 검증: 기능 브랜치를 마치기 전에 `project-audit` 을 한 번 돌린다.
- 언어(운영자 결정 2026-10-22): 코드에는 한글을 쓰지 않는다(MUST NOT). 식별자·docstring·주석·사용자 메시지·
  오류와 로그 문자열·시험 이름·커밋 메시지는 모두 영어다. 새 코드는 지금부터 따르고, 기존 코드의 한글은 별도 변경으로
  옮긴다. 계약으로 정해진 한국어 산출물(`화자N`, 전사록 머리글)과 한국어 시험 자료의 자리는 그 변경에서 정한다
  (TODO(KOREAN_PRODUCT_TEXT)). idea·spec 등 저장소 문서는 한국어로 쓸 수 있고, README 는 영어다.

## Governance

이 헌법은 stt 의 다른 관행과 문서보다 우선한다. 개정은 운영자 승인으로만 하고, 개정마다 이 파일 맨 위의 Sync Impact
Report 에 날짜·변경 내용·영향받은 템플릿과 산출물을 남긴다.

- 판 번호: MAJOR = 원칙의 제거나 호환되지 않는 재정의, MINOR = 원칙·절 추가나 실질적 확장, PATCH = 문구·명확화.
- 준수 점검: 모든 plan 의 「Constitution Check」는 원칙 I~VII 을 하나씩 대조해 적는다. 위반이 있으면 plan 의
  「Complexity Tracking」에 이유와 더 단순한 대안을 기각한 이유를 적고 운영자 승인을 받는다.
- 리뷰: 과제 리뷰와 최종 브랜치 리뷰는 원칙 III(삭제)·V(말없는 실패)·VII(시험 우선) 위반을 차단 사유로 본다.
- 런타임 지침: 전역 `~/.claude/CLAUDE.md`, 저장소 `CLAUDE.md`, 현재 plan 을 함께 따른다. 충돌하면 이 헌법이 저장소
  안에서 우선하되, 전역 규칙 §1·§2·§4 보안은 프로젝트 파일이 그 절을 인용하고 이유를 대야만 바꿀 수 있다.

**Version**: 1.1.0 | **Ratified**: 2026-10-22 | **Last Amended**: 2026-10-22
