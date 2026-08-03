#!/usr/bin/env python3
"""화자 분리 재전사를 중단된 지점부터 이어서 돌린다.

어느 파일이 끝났는지는 로그가 아니라 결과물에서 읽는다 — `--diarize`로
다시 전사한 파일은 사이드카 `.meta.yaml`에 `diarized: true`가 찍히기
때문이다. 로그가 사라져도, 세션이 끊겨도 상태는 남는다.

`stt transcribe`에 `--overwrite` 없이 폴더를 통째로 넘기면 옛 전사문이
이미 있다는 이유로 건너뛰므로, 아직 안 끝난 파일만 골라 넘겨야 한다.
이 스크립트가 하는 일이 그것이다.

`stt`를 PATH에서 찾으므로 venv 안에서 돌려야 하고, 화자 분리에는
HF_TOKEN이 필요하다:

    nix develop
    set -a; . ~/.config/keys/shared-api.env; set +a
    ./.venv/bin/python resume_retranscribe.py --dry-run
    ./.venv/bin/python resume_retranscribe.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

#: 전사 대상 미디어 확장자(stt.transcribe.SUPPORTED_EXTS의 부분집합).
MEDIA_EXTS: tuple[str, ...] = (".m4a", ".mp3", ".wav", ".mp4", ".mkv", ".webm")


def is_done(media: Path) -> bool:
    """미디어가 이미 화자 분리 재전사를 마쳤는지 본다.

    Args:
        media: 미디어 파일 경로.

    Returns:
        사이드카에 `diarized: true`가 있으면 True.

    Examples:
        >>> is_done(Path("없는파일.m4a"))
        False
    """
    sidecar = media.with_suffix(media.suffix + ".meta.yaml")
    if not sidecar.is_file():
        sidecar = media.parent / f"{media.stem}.meta.yaml"
    if not sidecar.is_file():
        return False
    for line in sidecar.read_text(encoding="utf-8").splitlines():
        if line.strip().lower() in ("diarized: true", "diarized: yes"):
            return True
    return False


def pending_media(data_dir: Path) -> list[Path]:
    """아직 재전사하지 않은 미디어 목록을 만든다.

    Args:
        data_dir: 미디어가 든 폴더.

    Returns:
        이름순으로 정렬된 미디어 경로 목록.

    Raises:
        FileNotFoundError: 폴더가 없을 때.

    Examples:
        >>> pending_media(Path("data"))  # doctest: +SKIP
    """
    if not data_dir.is_dir():
        raise FileNotFoundError(
            f"폴더 없음: '{data_dir}'를 찾을 수 없습니다."
        )
    media = sorted(
        p for p in data_dir.iterdir()
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS
    )
    return [p for p in media if not is_done(p)]


def build_command(media: Sequence[Path], terms: Path) -> list[str]:
    """남은 파일을 재전사하는 stt 명령을 만든다.

    Args:
        media: 재전사할 미디어 목록.
        terms: 용어 파일 경로.

    Returns:
        subprocess에 넘길 인자 목록.

    Examples:
        >>> build_command([Path("a.m4a")], Path("terms.txt"))[:2]
        ['stt', 'transcribe']
    """
    return [
        "stt", "transcribe", *[str(p) for p in media],
        "--terms", str(terms), "--diarize", "--overwrite",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """남은 파일을 찾아 재전사를 이어서 돌린다.

    Args:
        argv: 테스트용 인자 목록(None이면 sys.argv 사용).

    Returns:
        종료 코드(성공 0, 남은 파일이 없으면 0).

    Examples:
        >>> main(["--dry-run"])  # doctest: +SKIP
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--terms", type=Path, default=Path("terms.txt"))
    parser.add_argument(
        "--dry-run", action="store_true", help="목록만 보고 실행하지 않는다"
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    pending = pending_media(args.data_dir)
    total = sum(
        1 for p in args.data_dir.iterdir()
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS
    )
    print(f"전체 {total}건 | 완료 {total - len(pending)}건 | 남음 {len(pending)}건")
    for path in pending:
        print(f"  - {path.name}")
    if not pending:
        print("모두 재전사되었습니다.")
        return 0
    command = build_command(pending, args.terms)
    if args.dry_run:
        print("\n실행할 명령:")
        print(" ", " ".join(command))
        return 0
    return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
