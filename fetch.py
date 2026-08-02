#!/usr/bin/env python3
"""URL 목록에 적힌 유튜브 영상을 음원과 메타데이터로 내려받는다.

yt-dlp를 감싸 파일 이름을 '제목 [영상ID].확장자'로 통일하고, 옆에
'제목 [영상ID].info.json'을 함께 남긴다. 이미 받은 영상은
archive.txt에 기록해 두고 건너뛰므로 목록에 URL을 더한 뒤 다시
돌리면 새로 추가된 것만 내려받는다. 요청 간격을 벌려 유튜브의
일시 제한에 걸릴 가능성을 줄인다.

사용 예:
    python fetch.py --urls urls.txt
    python fetch.py --urls urls.txt --dry-run
    python fetch.py --urls urls.txt --auto-subs --video
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

#: 파일 이름 틀 — 영상 ID를 넣어야 전사문·메타데이터와 짝이 맞는다.
OUTPUT_TEMPLATE: str = "%(title)s [%(id)s].%(ext)s"

#: 이미 받은 영상을 기록하는 파일 이름(출력 폴더 안).
ARCHIVE_NAME: str = "archive.txt"

#: 유튜브 요청 제한을 피하기 위한 대기 설정(초).
SLEEP_OPTIONS: tuple[str, ...] = (
    "--sleep-requests", "1",
    "--sleep-interval", "3",
    "--max-sleep-interval", "8",
)

#: ffmpeg 실행 파일 이름(운영체제별).
_FFMPEG_NAMES: tuple[str, ...] = ("ffmpeg.exe", "ffmpeg")


def parse_url_list(text: str) -> list[str]:
    """줄 단위 URL 목록을 중복 없는 URL 목록으로 바꾼다.

    '#'로 시작하는 줄과 빈 줄은 무시하고, 앞뒤 공백은 지운다.
    같은 URL이 여러 번 나오면 처음 것만 남긴다.

    Args:
        text: 목록 파일 내용(UTF-8 텍스트).

    Returns:
        등장 순서를 지킨 URL 목록.

    Raises:
        ValueError: http로 시작하지 않는 줄이 있거나 결과가 빌 때.

    Examples:
        >>> parse_url_list("# 주석\\nhttps://youtu.be/a\\n")
        ['https://youtu.be/a']
    """
    urls: list[str] = []
    bad: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if not value.startswith("http"):
            bad.append(f"{number}번 줄: {value}")
            continue
        if value not in urls:
            urls.append(value)
    if bad:
        joined = "\n".join(bad)
        raise ValueError(
            f"URL이 아닌 줄이 있습니다:\n{joined}\n"
            f"한 줄에 영상 주소 하나씩 적어 주세요('#' 줄은 주석)."
        )
    if not urls:
        raise ValueError(
            "빈 목록 파일: 내려받을 URL이 없습니다. "
            "한 줄에 영상 주소 하나씩 적어 주세요."
        )
    return urls


def load_urls(path: Path) -> list[str]:
    """URL 목록 파일을 읽는다.

    Args:
        path: URL 목록 텍스트 파일 경로.

    Returns:
        URL 목록.

    Raises:
        FileNotFoundError: 목록 파일이 없을 때.
        ValueError: 목록 내용이 잘못됐을 때.

    Examples:
        >>> load_urls(Path("urls.txt"))  # doctest: +SKIP
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"목록 파일 없음: '{path}'를 찾을 수 없습니다. "
            f"--urls 경로를 확인하세요."
        )
    return parse_url_list(path.read_text(encoding="utf-8"))


def find_ffmpeg() -> str | None:
    """ffmpeg 실행 파일을 PATH와 venv 폴더에서 찾는다.

    PATH를 먼저 보고, 없으면 지금 파이썬이 들어 있는 폴더(venv의
    Scripts 또는 bin)를 본다. venv 안에만 두고 쓰는 설치 방식도
    인식하기 위함이다.

    Returns:
        찾은 ffmpeg 경로. 없으면 None.

    Examples:
        >>> find_ffmpeg()  # doctest: +SKIP
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    exe_dir = Path(sys.executable).parent
    for name in _FFMPEG_NAMES:
        candidate = exe_dir / name
        if candidate.is_file():
            return str(candidate)
    return None


def require_tools() -> None:
    """yt-dlp 모듈과 ffmpeg 실행 파일이 있는지 확인한다.

    yt-dlp는 PATH가 아니라 지금 파이썬에 설치된 모듈로 확인한다.
    venv 안에 설치돼 있어도 PATH에는 없을 수 있기 때문이다.

    Raises:
        RuntimeError: yt-dlp 모듈이나 ffmpeg를 찾지 못했을 때.

    Examples:
        >>> require_tools()  # doctest: +SKIP
    """
    if importlib.util.find_spec("yt_dlp") is None:
        raise RuntimeError(
            f"yt-dlp를 찾지 못했습니다. 이 파이썬에 설치돼 있지 "
            f"않습니다: {sys.executable}. 저장소 폴더에서 "
            f"'uv sync'를 실행하세요."
        )
    if find_ffmpeg() is None:
        raise RuntimeError(
            "ffmpeg를 찾지 못했습니다. 내려받은 음원을 변환하는 데 "
            "필요합니다. Windows는 'winget install Gyan.FFmpeg', "
            "리눅스는 'sudo apt install ffmpeg'로 설치한 뒤 "
            "새 터미널에서 다시 실행하세요."
        )


def build_command(
    urls: Sequence[str], args: argparse.Namespace
) -> list[str]:
    """yt-dlp에 넘길 명령 인자 목록을 만든다.

    Args:
        urls: 내려받을 영상 주소 목록.
        args: 파싱된 CLI 인자.

    Returns:
        subprocess에 그대로 넘길 수 있는 인자 목록.

    Examples:
        >>> ns = parse_args(["--urls", "urls.txt"])
        >>> build_command(["https://youtu.be/a"], ns)[1:3]
        ['-m', 'yt_dlp']
    """
    archive = args.out_dir / ARCHIVE_NAME
    command = [
        sys.executable, "-m", "yt_dlp",
        "-P", str(args.out_dir),
        "-o", OUTPUT_TEMPLATE,
        "--write-info-json",
        "--download-archive", str(archive),
        "--no-overwrites",
        "--ignore-errors",
        *SLEEP_OPTIONS,
    ]
    if not args.video:
        command += ["-x", "--audio-format", args.audio_format]
    if args.auto_subs:
        command += [
            "--write-auto-subs",
            "--sub-langs", args.sub_langs,
            "--convert-subs", "srt",
        ]
    if args.cookies_from_browser:
        command += ["--cookies-from-browser", args.cookies_from_browser]
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        command += ["--ffmpeg-location", ffmpeg]
    return command + list(urls)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """CLI 인자를 파싱한다.

    Args:
        argv: sys.argv[1:] 형태의 인자 목록.

    Returns:
        파싱된 argparse.Namespace.

    Examples:
        >>> parse_args(["--urls", "urls.txt"]).audio_format
        'm4a'
    """
    parser = argparse.ArgumentParser(
        description=(
            "URL 목록의 유튜브 영상을 음원과 메타데이터로 내려받는다."
        ),
    )
    parser.add_argument(
        "--urls", dest="urls_path", type=Path, required=True,
        help="영상 주소 목록 파일(한 줄에 하나, '#' 주석)",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("data"),
        help="내려받을 폴더 (기본: data)",
    )
    parser.add_argument(
        "--video", action="store_true",
        help="음원 대신 영상 그대로 받는다",
    )
    parser.add_argument("--audio-format", default="m4a",
                        help="음원 형식 (기본: m4a)")
    parser.add_argument(
        "--auto-subs", action="store_true",
        help="유튜브 자동자막도 함께 받는다(전사 정확도 대조용)",
    )
    parser.add_argument(
        "--sub-langs", default="ko",
        help="자동자막 언어 코드 (기본: ko)",
    )
    parser.add_argument(
        "--cookies-from-browser", default=None,
        help="회원 전용·연령 제한 영상용 (예: chrome, edge)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="실행할 명령만 보여주고 내려받지 않는다",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점: 목록 읽기 → 명령 조립 → yt-dlp 실행.

    Args:
        argv: 테스트용 인자 목록(None이면 sys.argv 사용).

    Returns:
        yt-dlp의 종료 코드(내려받지 않았으면 0).

    Examples:
        >>> main(["--help"])  # doctest: +SKIP
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    urls = load_urls(args.urls_path)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    require_tools()
    command = build_command(urls, args)
    print(f"대상 {len(urls)}개 | 저장 폴더: {args.out_dir}")
    if args.dry_run:
        print("\n실행할 명령:")
        print(" ".join(command))
        print("\n--dry-run: 실제로 내려받지 않았습니다.")
        return 0
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        print(
            f"\n일부 영상을 받지 못했습니다(yt-dlp 종료 코드 "
            f"{result.returncode}). 받은 것은 그대로 두고 다시 "
            f"실행하면 나머지만 시도합니다."
        )
    else:
        print("\n내려받기 완료.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
