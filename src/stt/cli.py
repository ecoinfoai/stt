"""``stt`` 명령의 진입점: 서브커맨드를 골라 해당 모듈로 넘긴다.

각 서브커맨드는 자기 인자를 스스로 파싱하므로 여기서는 첫 낱말만
보고 나머지를 그대로 전달한다.

사용 예:
    stt fetch --urls urls.txt
    stt transcribe data --terms terms.txt
    stt batch --list list.yaml --base-dir data
"""
from __future__ import annotations

import sys
from typing import Callable, Sequence

from stt import batch, enroll, fetch, transcribe

Command = Callable[[Sequence[str]], int]

#: 서브커맨드 이름과 실행할 함수(테스트에서 통째로 갈아 끼운다).
COMMANDS: dict[str, Command] = {
    "fetch": fetch.main,
    "transcribe": transcribe.main,
    "batch": batch.main,
    "enroll": enroll.main,
}

_SUMMARIES: dict[str, str] = {
    "fetch": "URL 목록의 영상을 음원과 메타데이터로 내려받는다",
    "transcribe": "미디어 파일·폴더를 한국어 전사문(TXT)으로 바꾼다",
    "batch": "목록 파일(.txt/.yaml)에 적은 여러 미디어를 전사한다",
    "enroll": "전사한 파일에서 화자 목소리를 등록해 사전에 쌓는다",
}


def usage() -> str:
    """서브커맨드 목록을 담은 사용법 문자열을 만든다.

    Returns:
        여러 줄짜리 사용법 문자열(끝에 개행 없음).

    Examples:
        >>> "stt transcribe" in usage()
        True
    """
    lines = [
        "사용법: stt <명령> [옵션]",
        "",
        "명령:",
    ]
    width = max(len(name) for name in COMMANDS)
    lines += [
        f"  {name:<{width}}  {_SUMMARIES[name]}"
        for name in COMMANDS
    ]
    lines += [
        "",
        "각 명령의 옵션은 'stt <명령> --help'로 볼 수 있습니다.",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점: 첫 인자를 서브커맨드로 보고 나머지를 넘긴다.

    Args:
        argv: 테스트용 인자 목록(None이면 sys.argv 사용).

    Returns:
        서브커맨드의 종료 코드. 사용법만 출력했으면 0, 인자가
        없거나 모르는 명령이면 2.

    Examples:
        >>> main(["--help"])
        0
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(usage(), file=sys.stderr)
        return 2
    name, rest = args[0], args[1:]
    if name in ("-h", "--help", "help"):
        print(usage())
        return 0
    if name not in COMMANDS:
        choices = ", ".join(COMMANDS)
        print(
            f"알 수 없는 명령: '{name}'. 쓸 수 있는 명령은 "
            f"{choices} 입니다.",
            file=sys.stderr,
        )
        return 2
    return COMMANDS[name](rest)
