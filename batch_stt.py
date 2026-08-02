#!/usr/bin/env python3
"""목록 파일에 적어 둔 여러 미디어를 순서대로 전사하는 배치 러너.

`.txt`(한 줄에 제목 하나) 또는 `.yaml`(항목별 옵션 지정 가능)
목록을 읽어 미디어 폴더의 실제 파일로 해석한 뒤 transcribe.py를
호출한다. 확장자를 적지 않은 '제목'만으로도 파일을 찾으며, 같은
옵션을 쓰는 항목끼리 묶어 처리하므로 모델은 옵션 묶음당 한 번만
적재된다. 목록에 없는 파일이 하나라도 있으면 전사를 시작하기
전에 전부 모아 보고하고 멈춘다.

사용 예:
    python batch_stt.py --list list.txt
    python batch_stt.py --list list.yaml --dry-run
    python batch_stt.py --list list.txt --base-dir data --keep-going
"""
from __future__ import annotations

import argparse
import difflib
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, TypeAlias

import transcribe

Options: TypeAlias = dict[str, Any]
Resolved: TypeAlias = tuple[Path, Options]
Group: TypeAlias = tuple[Options, list[Path]]

#: 목록 항목이 개별로 덮어쓸 수 있는 옵션과 그 기본값.
OPTION_DEFAULTS: Options = {
    "model": "auto",
    "device": "auto",
    "language": "ko",
    "terms": None,
    "srt": False,
    "no_timestamps": False,
    "beam": 5,
    "gap": 2.0,
    "max_chars": 800,
}

#: 제목이 겹칠 때 먼저 고르는 확장자(용량이 작은 음원 우선).
AUDIO_EXTS: frozenset[str] = frozenset({
    ".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma",
})

#: YAML에서 파일 이름을 담을 수 있는 키 이름들.
_NAME_KEYS: tuple[str, ...] = ("title", "path", "name", "file")

_LIST_SUFFIXES: frozenset[str] = frozenset({".txt", ".yaml", ".yml"})


@dataclass(frozen=True)
class ListItem:
    """목록 한 줄이 뜻하는 대상 하나.

    Attributes:
        name: 목록에 적힌 제목 또는 경로 문자열.
        options: 이 항목에 적용할 전사 옵션(OPTION_DEFAULTS 형태).
    """

    name: str
    options: Options


def _nfc(text: str) -> str:
    """한글 파일명 비교를 위해 유니코드를 NFC로 정규화한다.

    Args:
        text: 정규화할 문자열.

    Returns:
        NFC로 정규화된 문자열.

    Examples:
        >>> _nfc("강연") == "강연"
        True
    """
    return unicodedata.normalize("NFC", text)


def _merge_options(overrides: dict[str, Any], base: Options) -> Options:
    """기본 옵션 위에 덮어쓸 옵션을 얹는다.

    Args:
        overrides: 덮어쓸 옵션(알 수 없는 키가 있으면 오류).
        base: 바탕이 되는 옵션 사전.

    Returns:
        병합된 새 옵션 사전.

    Raises:
        ValueError: OPTION_DEFAULTS에 없는 키가 들어왔을 때.

    Examples:
        >>> _merge_options({"beam": 1}, OPTION_DEFAULTS)["beam"]
        1
    """
    unknown = sorted(set(overrides) - set(OPTION_DEFAULTS))
    if unknown:
        allowed = ", ".join(sorted(OPTION_DEFAULTS))
        raise ValueError(
            f"알 수 없는 옵션: {', '.join(unknown)}. "
            f"목록 파일에서 쓸 수 있는 옵션은 {allowed} 입니다."
        )
    return {**base, **overrides}


def parse_txt_list(text: str) -> list[ListItem]:
    """줄 단위 텍스트 목록을 항목 목록으로 바꾼다.

    '#'로 시작하는 줄과 빈 줄은 무시하고, 앞뒤 공백은 지운다.

    Args:
        text: 목록 파일 내용(UTF-8 텍스트).

    Returns:
        기본 옵션이 적용된 ListItem 목록.

    Raises:
        ValueError: 유효한 줄이 하나도 없을 때.

    Examples:
        >>> parse_txt_list("# 주석\\n강연 A\\n")[0].name
        '강연 A'
    """
    names = [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not names:
        raise ValueError(
            "빈 목록 파일: 전사할 항목이 없습니다. "
            "한 줄에 파일 제목 하나씩 적어 주세요('#' 줄은 주석)."
        )
    return [ListItem(name, dict(OPTION_DEFAULTS)) for name in names]


def _item_from_mapping(raw: dict[str, Any], base: Options) -> ListItem:
    """YAML 매핑 항목 하나를 ListItem으로 바꾼다.

    Args:
        raw: title/path 등 이름 키와 옵션 키를 담은 매핑.
        base: 이 항목에 적용할 기본 옵션.

    Returns:
        이름과 병합된 옵션을 담은 ListItem.

    Raises:
        ValueError: 이름 키가 없거나 옵션 키가 잘못됐을 때.

    Examples:
        >>> _item_from_mapping({"title": "A"}, OPTION_DEFAULTS).name
        'A'
    """
    found = [key for key in _NAME_KEYS if key in raw]
    if not found:
        keys = ", ".join(_NAME_KEYS)
        raise ValueError(
            f"이름 없는 항목: {raw}. "
            f"목록의 각 항목에는 {keys} 중 하나가 있어야 합니다."
        )
    name = str(raw[found[0]])
    overrides = {k: v for k, v in raw.items() if k not in _NAME_KEYS}
    return ListItem(name, _merge_options(overrides, base))


def parse_yaml_list(text: str) -> list[ListItem]:
    """YAML 목록을 항목 목록으로 바꾼다.

    최상위가 매핑이면 defaults(전체 기본 옵션)와 items(항목 목록)를
    읽고, 최상위가 그냥 목록이면 항목 목록으로 본다. 각 항목은
    문자열이거나 title/path와 옵션을 담은 매핑이다.

    Args:
        text: YAML 파일 내용.

    Returns:
        옵션이 병합된 ListItem 목록.

    Raises:
        ImportError: PyYAML이 설치돼 있지 않을 때.
        ValueError: 구조가 잘못됐거나 항목이 하나도 없을 때.

    Examples:
        >>> parse_yaml_list("items:\\n  - 강연 A\\n")[0].name
        '강연 A'
    """
    try:
        import yaml
    except ImportError as error:
        raise ImportError(
            "PyYAML이 없습니다: .yaml 목록을 읽으려면 "
            "'uv add pyyaml'(또는 pip install pyyaml)로 설치하거나 "
            "--list를 .txt 목록으로 바꿔 주세요."
        ) from error
    data = yaml.safe_load(text)
    if isinstance(data, dict):
        base = _merge_options(data.get("defaults") or {}, OPTION_DEFAULTS)
        raw_items = data.get("items")
    else:
        base, raw_items = dict(OPTION_DEFAULTS), data
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError(
            "빈 목록 파일: items 아래에 전사할 항목이 없습니다. "
            "문자열 제목이나 title/path 매핑을 나열해 주세요."
        )
    return [
        ListItem(str(raw), dict(base)) if not isinstance(raw, dict)
        else _item_from_mapping(raw, base)
        for raw in raw_items
    ]


def load_list(path: Path) -> list[ListItem]:
    """목록 파일을 확장자에 맞는 파서로 읽는다.

    Args:
        path: .txt 또는 .yaml/.yml 목록 파일 경로.

    Returns:
        ListItem 목록.

    Raises:
        FileNotFoundError: 목록 파일이 없을 때.
        ValueError: 지원하지 않는 확장자일 때.

    Examples:
        >>> load_list(Path("list.txt"))  # doctest: +SKIP
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"목록 파일 없음: '{path}'를 찾을 수 없습니다. "
            f"--list 경로를 확인하세요."
        )
    suffix = path.suffix.lower()
    if suffix not in _LIST_SUFFIXES:
        raise ValueError(
            f"지원하지 않는 목록 형식: '{path.name}'. "
            f"확장자는 .txt, .yaml, .yml 중 하나여야 합니다."
        )
    text = path.read_text(encoding="utf-8")
    if suffix == ".txt":
        return parse_txt_list(text)
    return parse_yaml_list(text)


def _sort_key(path: Path) -> tuple[int, str]:
    """후보 파일 정렬 기준(음원 먼저, 그다음 이름순)을 만든다.

    Args:
        path: 후보 미디어 경로.

    Returns:
        (0=음원/1=영상, 파일 이름) 튜플.

    Examples:
        >>> _sort_key(Path("a.m4a"))[0]
        0
    """
    return (0 if path.suffix.lower() in AUDIO_EXTS else 1, path.name)


def _media_in(directory: Path) -> list[Path]:
    """폴더 바로 아래의 지원 미디어 파일을 모은다.

    Args:
        directory: 찾아볼 폴더.

    Returns:
        지원 확장자를 가진 파일 경로 목록.

    Examples:
        >>> _media_in(Path("data"))  # doctest: +SKIP
    """
    return [
        item for item in directory.iterdir()
        if item.is_file()
        and item.suffix.lower() in transcribe.SUPPORTED_EXTS
    ]


def resolve_name(name: str, base_dir: Path) -> Path:
    """목록에 적힌 제목·경로를 실제 미디어 파일 경로로 바꾼다.

    확장자까지 적었으면 그 파일을 그대로 쓰고, 제목만 적었으면
    같은 폴더에서 이름이 일치하는 미디어를 찾는다. 같은 제목의
    영상과 음원이 함께 있으면 음원을 고른다.

    Args:
        name: 목록에 적힌 제목 또는 상대·절대 경로.
        base_dir: 상대 경로의 기준이 되는 미디어 폴더.

    Returns:
        존재하는 미디어 파일 경로.

    Raises:
        FileNotFoundError: 폴더나 해당 파일을 찾지 못했을 때.

    Examples:
        >>> resolve_name("강연 A", Path("data"))  # doctest: +SKIP
    """
    raw = Path(name)
    search_dir = raw.parent if raw.is_absolute() else (base_dir / raw).parent
    if not search_dir.is_dir():
        raise FileNotFoundError(
            f"폴더 없음: '{name}'의 상위 폴더 '{search_dir}'가 "
            f"없습니다. --base-dir 값과 목록의 경로를 확인하세요."
        )
    exact = search_dir / raw.name
    if exact.is_file() and exact.suffix.lower() in transcribe.SUPPORTED_EXTS:
        return exact
    target = _nfc(raw.name)
    matches = [p for p in _media_in(search_dir) if _nfc(p.stem) == target]
    if matches:
        return sorted(matches, key=_sort_key)[0]
    stems = sorted({_nfc(p.stem) for p in _media_in(search_dir)})
    near = difflib.get_close_matches(target, stems, n=3, cutoff=0.5)
    hint = f" 비슷한 이름: {', '.join(near)}" if near else ""
    raise FileNotFoundError(
        f"미디어 없음: '{search_dir}' 안에서 '{raw.name}'에 해당하는 "
        f"파일을 찾지 못했습니다.{hint}"
    )


def resolve_items(
    items: Sequence[ListItem], base_dir: Path
) -> list[Resolved]:
    """모든 항목을 실제 경로로 바꾼다(실패는 한 번에 모아 보고).

    전사를 시작하기 전에 목록 전체가 유효한지 확인하기 위한
    단계이므로, 실패한 항목이 여럿이면 모두 모아서 알린다.

    Args:
        items: 해석할 ListItem 목록.
        base_dir: 상대 경로의 기준 폴더.

    Returns:
        (미디어 경로, 옵션) 쌍 목록(목록 순서 유지).

    Raises:
        FileNotFoundError: 찾지 못한 항목이 하나라도 있을 때.
        ValueError: 같은 파일이 두 번 이상 나올 때.

    Examples:
        >>> resolve_items([], Path("data"))
        []
    """
    resolved: list[Resolved] = []
    problems: list[str] = []
    seen: dict[Path, str] = {}
    for item in items:
        try:
            media = resolve_name(item.name, base_dir).resolve()
        except FileNotFoundError as error:
            problems.append(f"- {item.name}: {error}")
            continue
        if media in seen:
            raise ValueError(
                f"중복 항목: '{item.name}'과 '{seen[media]}'이(가) 같은 "
                f"파일 '{media.name}'을 가리킵니다. 목록에서 하나를 "
                f"지워 주세요."
            )
        seen[media] = item.name
        resolved.append((media, item.options))
    if problems:
        joined = "\n".join(problems)
        raise FileNotFoundError(
            f"목록에서 {len(problems)}개 항목을 찾지 못했습니다:\n"
            f"{joined}\n"
            f"제목 철자와 --base-dir 값을 확인한 뒤 다시 실행하세요."
        )
    return resolved


def group_by_options(pairs: Sequence[Resolved]) -> list[Group]:
    """옵션이 같은 항목끼리 묶는다(모델 재적재를 줄이기 위함).

    Args:
        pairs: (미디어 경로, 옵션) 쌍 목록.

    Returns:
        (옵션, 경로 목록) 묶음 목록(처음 등장 순서 유지).

    Examples:
        >>> group_by_options([])
        []
    """
    groups: dict[tuple, Group] = {}
    for media, options in pairs:
        key = tuple(sorted((k, str(v)) for k, v in options.items()))
        if key not in groups:
            groups[key] = (options, [])
        groups[key][1].append(media)
    return list(groups.values())


def build_argv(
    paths: Sequence[Path],
    options: Options,
    out_dir: Path | None,
    overwrite: bool,
) -> list[str]:
    """transcribe.py에 넘길 인자 목록을 만든다.

    Args:
        paths: 이 묶음에서 전사할 미디어 경로들.
        options: 이 묶음에 적용할 옵션.
        out_dir: 출력 폴더(None이면 원본 옆에 저장).
        overwrite: 이미 있는 출력 파일을 다시 만들지 여부.

    Returns:
        transcribe.main()에 넘길 문자열 인자 목록.

    Examples:
        >>> build_argv([Path("a.m4a")], OPTION_DEFAULTS, None, False)[0]
        'a.m4a'
    """
    argv = [str(path) for path in paths]
    argv += [
        "--model", str(options["model"]),
        "--device", str(options["device"]),
        "--language", str(options["language"]),
        "--beam", str(options["beam"]),
        "--gap", str(options["gap"]),
        "--max-chars", str(options["max_chars"]),
    ]
    if options["terms"]:
        argv += ["--terms", str(options["terms"])]
    if options["srt"]:
        argv.append("--srt")
    if options["no_timestamps"]:
        argv.append("--no-timestamps")
    if out_dir is not None:
        argv += ["--output-dir", str(out_dir)]
    if overwrite:
        argv.append("--overwrite")
    return argv


def print_plan(pairs: Sequence[Resolved], groups: Sequence[Group]) -> None:
    """해석 결과와 묶음 구성을 화면에 보여준다.

    Args:
        pairs: (미디어 경로, 옵션) 쌍 목록.
        groups: group_by_options()가 만든 묶음 목록.

    Examples:
        >>> print_plan([], [])
        전사 대상 0개 / 옵션 묶음 0개
    """
    print(f"전사 대상 {len(pairs)}개 / 옵션 묶음 {len(groups)}개")
    for index, (media, _) in enumerate(pairs, start=1):
        print(f"  {index:>3}. {media.name}")


def run_groups(
    groups: Sequence[Group],
    out_dir: Path | None,
    overwrite: bool,
    keep_going: bool,
) -> list[str]:
    """묶음을 차례로 전사하고 실패한 묶음을 돌려준다.

    Args:
        groups: (옵션, 경로 목록) 묶음 목록.
        out_dir: 출력 폴더(None이면 원본 옆).
        overwrite: 기존 출력 파일 재생성 여부.
        keep_going: True면 묶음이 실패해도 다음 묶음을 계속한다.

    Returns:
        실패한 묶음의 오류 설명 목록(모두 성공하면 빈 목록).

    Raises:
        Exception: keep_going이 False일 때 전사 중 발생한 예외.

    Examples:
        >>> run_groups([], None, False, False)
        []
    """
    failures: list[str] = []
    for index, (options, paths) in enumerate(groups, start=1):
        print(
            f"\n=== 묶음 {index}/{len(groups)} "
            f"({len(paths)}개, 모델 {options['model']}) ==="
        )
        argv = build_argv(paths, options, out_dir, overwrite)
        try:
            transcribe.main(argv)
        except Exception as error:  # noqa: BLE001 - 요약 후 재보고
            if not keep_going:
                raise
            names = ", ".join(path.name for path in paths)
            failures.append(f"- 묶음 {index}({names}): {error}")
            print(f"  실패: {error}")
    return failures


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """CLI 인자를 파싱한다.

    Args:
        argv: sys.argv[1:] 형태의 인자 목록.

    Returns:
        파싱된 argparse.Namespace.

    Examples:
        >>> parse_args(["--list", "list.txt"]).base_dir
        PosixPath('data')
    """
    parser = argparse.ArgumentParser(
        description=(
            "목록 파일(.txt/.yaml)에 적은 여러 미디어를 "
            "순서대로 전사한다."
        ),
    )
    parser.add_argument(
        "--list", dest="list_path", type=Path, required=True,
        help="전사할 제목·경로 목록 파일(.txt 또는 .yaml)",
    )
    parser.add_argument(
        "--base-dir", type=Path, default=Path("data"),
        help="목록의 상대 경로 기준 폴더 (기본: data)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="출력 폴더 (기본: 원본 파일 옆)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="이미 있는 전사문을 다시 만든다",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="해석 결과만 확인하고 전사는 하지 않는다",
    )
    parser.add_argument(
        "--keep-going", action="store_true",
        help="한 묶음이 실패해도 나머지를 계속 전사한다",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점: 목록 읽기 → 경로 해석 → 묶음 전사.

    Args:
        argv: 테스트용 인자 목록(None이면 sys.argv 사용).

    Returns:
        종료 코드(모두 성공하면 0, 실패한 묶음이 있으면 1).

    Examples:
        >>> main(["--help"])  # doctest: +SKIP
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    items = load_list(args.list_path)
    pairs = resolve_items(items, args.base_dir)
    groups = group_by_options(pairs)
    print_plan(pairs, groups)
    if args.dry_run:
        print("\n--dry-run: 실제 전사는 하지 않았습니다.")
        return 0
    failures = run_groups(
        groups, args.output_dir, args.overwrite, args.keep_going
    )
    if failures:
        print(f"\n실패한 묶음 {len(failures)}개:")
        print("\n".join(failures))
        return 1
    print(f"\n목록 전체 완료: {len(pairs)}개 파일.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
