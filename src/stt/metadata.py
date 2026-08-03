#!/usr/bin/env python3
"""전사 결과와 yt-dlp 메타데이터를 노트용 YAML로 정리한다.

미디어 옆의 '제목 [ID].info.json'과 전사 실행 정보를 합쳐
'제목 [ID].meta.yaml'을 만든다. 항목 이름은 transcript-report
스킬의 보고서 템플릿(template_version 1.3) frontmatter와 같게
맞춰, 노트를 만들 때 그대로 옮겨 적을 수 있게 한다.

기계로 확정되는 값만 채우고, 추정한 값은 _meta.candidates에,
값이 어디서 왔는지는 _meta.provenance에 따로 적는다. 사람이
판단해야 하는 칸은 빈 문자열로 남긴다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from stt import transcribe

Meta: TypeAlias = dict[str, Any]

#: 이 파일이 맞추는 보고서 템플릿 버전.
TEMPLATE_VERSION: str = "1.3"

#: 사람이 판단해야 해서 자동으로 채우지 않는 항목.
HUMAN_FIELDS: tuple[str, ...] = (
    "title", "speaker", "institution", "source_type", "recorded",
    "course",
)

#: 제목에서 연사 이름을 추정할 때 쓰는 직함.
_TITLES: tuple[str, ...] = (
    "교수", "박사", "대표", "원장", "소장", "회장", "이사장", "장관",
)

_SPEAKER_RE = re.compile(
    r"([가-힣]{2,4})\s*(?:" + "|".join(_TITLES) + r")"
)

_GUIDE = (
    "# stt가 자동으로 만든 메타데이터입니다.\n"
    "# 빈 칸은 사람이 채우거나 '미상'으로 두고, 후보값은\n"
    "# _meta.candidates에서 골라 옮겨 적으세요.\n"
    "# _meta.provenance는 보고서의 '메타데이터 근거'에 씁니다.\n"
)


@dataclass(frozen=True)
class RunInfo:
    """한 번의 전사 실행에서 확정되는 정보.

    Attributes:
        model: 사용한 Whisper 모델 이름.
        language: 전사 언어 코드(예: 'ko').
        duration_s: 원본 길이(초).
        transcribed: 전사 실행일(YYYY-MM-DD).
        diarized: 화자 분리를 거쳤으면 True.
    """

    model: str
    language: str
    duration_s: float
    transcribed: str
    diarized: bool = False


def format_upload_date(value: str | None) -> str:
    """yt-dlp의 8자리 게시일을 YYYY-MM-DD로 바꾼다.

    Args:
        value: 'YYYYMMDD' 문자열 또는 None.

    Returns:
        'YYYY-MM-DD' 문자열. 값이 없으면 빈 문자열, 형식이 다르면
        받은 값을 그대로 돌려준다.

    Examples:
        >>> format_upload_date("20160512")
        '2016-05-12'
    """
    if not value:
        return ""
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        return text
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def format_chapters(chapters: list[dict] | None) -> list[dict[str, str]]:
    """유튜브 챕터 목록을 시각·제목 쌍으로 바꾼다.

    Args:
        chapters: yt-dlp의 chapters 목록 또는 None.

    Returns:
        {'time': 'MM:SS', 'title': ...} 사전 목록.

    Examples:
        >>> format_chapters([{"start_time": 0.0, "title": "도입"}])
        [{'time': '00:00', 'title': '도입'}]
    """
    if not chapters:
        return []
    result: list[dict[str, str]] = []
    for chapter in chapters:
        title = str(chapter.get("title") or "").strip()
        if not title:
            continue
        start = float(chapter.get("start_time") or 0.0)
        result.append({
            "time": transcribe.format_timestamp(start),
            "title": title,
        })
    return result


def speaker_candidates(title: str, channel: str) -> list[str]:
    """제목과 채널 이름에서 연사 이름 후보를 뽑는다.

    제목에 '이름 + 직함'(교수·박사 등) 형태가 있으면 그 이름을
    쓰고, 없으면 채널 이름을 후보로 남긴다. 추정일 뿐이므로
    확정값으로 쓰지 않는다.

    Args:
        title: 영상 제목.
        channel: 채널 이름(없으면 빈 문자열).

    Returns:
        중복 없는 후보 이름 목록.

    Examples:
        >>> speaker_candidates("홍길동 교수 특강", "채널")
        ['홍길동']
    """
    found = _SPEAKER_RE.findall(title or "")
    if found:
        return list(dict.fromkeys(found))
    return [channel] if channel else []


def _candidates_from(info: dict) -> dict[str, list[str]]:
    """info.json에서 사람이 고를 후보값들을 모은다.

    Args:
        info: yt-dlp가 만든 info.json 내용.

    Returns:
        speaker/institution/topics 후보를 담은 사전.

    Examples:
        >>> _candidates_from({"title": "홍길동 교수"})["speaker"]
        ['홍길동']
    """
    channel = str(info.get("channel") or info.get("uploader") or "")
    topics = [
        str(item) for item in
        list(info.get("tags") or [])[:8]
        + list(info.get("categories") or [])
    ]
    candidates = {
        "speaker": speaker_candidates(str(info.get("title") or ""),
                                      channel),
        "institution": [channel] if channel else [],
        "topics": list(dict.fromkeys(topics)),
    }
    return {key: value for key, value in candidates.items() if value}


def _provenance_for(info: dict | None) -> dict[str, str]:
    """각 값이 어디서 왔는지 기록을 만든다.

    Args:
        info: info.json 내용 또는 None.

    Returns:
        항목 이름과 출처 설명을 담은 사전.

    Examples:
        >>> _provenance_for(None)["duration"]
        'faster-whisper'
    """
    provenance = {
        "language": "faster-whisper 전사 설정·감지",
        "duration": "faster-whisper",
        "model": "stt 실행 설정",
        "transcribed": "stt 실행일",
    }
    if info:
        provenance.update({
            "source_url": "info.json (webpage_url)",
            "published": "info.json (upload_date)",
            "title_raw": "info.json (title)",
            "channel": "info.json (channel)",
            "chapters": "info.json (chapters)",
            "candidates.speaker": "영상 제목에서 추정",
            "candidates.topics": "info.json (tags, categories)",
        })
    return provenance


def build_meta(info: dict | None, run: RunInfo) -> Meta:
    """노트용 메타데이터 사전을 만든다.

    Args:
        info: info.json 내용(없으면 None).
        run: 이번 전사 실행에서 확정된 정보.

    Returns:
        보고서 템플릿 항목 이름을 그대로 쓴 사전.

    Examples:
        >>> run = RunInfo("small", "ko", 60.0, "2026-08-02")
        >>> build_meta(None, run)["duration"]
        '01:00'
    """
    source = info or {}
    meta: Meta = {key: "" for key in HUMAN_FIELDS}
    meta.update({
        "source_url": str(source.get("webpage_url") or ""),
        "language": run.language,
        "transcribed": run.transcribed,
        "topics": [],
        "model": f"faster-whisper {run.model}",
        "duration": transcribe.format_timestamp(run.duration_s),
        "diarized": run.diarized,
    })
    inner: Meta = {
        "template_version": TEMPLATE_VERSION,
        "candidates": _candidates_from(source) if info else {},
        "provenance": _provenance_for(info),
    }
    if info:
        inner.update({
            "video_id": str(source.get("id") or ""),
            "title_raw": str(source.get("title") or ""),
            "channel": str(
                source.get("channel") or source.get("uploader") or ""
            ),
            "channel_url": str(source.get("channel_url") or ""),
            "published": format_upload_date(source.get("upload_date")),
            "chapters": format_chapters(source.get("chapters")),
        })
    meta["_meta"] = inner
    return meta


def load_info_json(media: Path) -> dict | None:
    """미디어 옆의 info.json을 읽는다.

    Args:
        media: 미디어 파일 경로.

    Returns:
        info.json 내용. 파일이 없으면 None.

    Raises:
        ValueError: 파일은 있는데 내용이 깨졌을 때.

    Examples:
        >>> load_info_json(Path("data/강연.m4a"))  # doctest: +SKIP
    """
    path = media.with_suffix(".info.json")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"메타데이터 파일이 깨졌습니다: '{path.name}'을 읽지 못했습니다 "
            f"({error}). 해당 영상을 다시 내려받으세요."
        ) from error


def meta_path_for(media: Path, out_dir: Path | None) -> Path:
    """미디어에 대응하는 meta.yaml 경로를 만든다.

    Args:
        media: 원본 미디어 경로.
        out_dir: 출력 폴더(None이면 원본 옆).

    Returns:
        '.meta.yaml'로 끝나는 경로.

    Examples:
        >>> meta_path_for(Path("a/b.m4a"), None)
        PosixPath('a/b.meta.yaml')
    """
    base = out_dir if out_dir is not None else media.parent
    return base / (media.stem + ".meta.yaml")


def write_for_media(
    media: Path, out_dir: Path | None, run: RunInfo
) -> Path:
    """미디어 옆 info.json을 읽어 meta.yaml을 만들어 저장한다.

    Args:
        media: 전사한 미디어 파일 경로.
        out_dir: 출력 폴더(None이면 원본 옆).
        run: 이번 전사 실행에서 확정된 정보.

    Returns:
        저장한 .meta.yaml 경로.

    Raises:
        ValueError: info.json 내용이 깨졌을 때.
        ImportError: PyYAML이 설치돼 있지 않을 때.

    Examples:
        >>> write_for_media(Path("a.m4a"), None, run)  # doctest: +SKIP
    """
    info = load_info_json(media)
    path = meta_path_for(media, out_dir)
    write_meta_yaml(path, build_meta(info, run))
    return path


def write_meta_yaml(path: Path, meta: Meta) -> None:
    """메타데이터 사전을 YAML 파일로 저장한다.

    Args:
        path: 저장할 .meta.yaml 경로.
        meta: build_meta()가 만든 사전.

    Raises:
        ImportError: PyYAML이 설치돼 있지 않을 때.

    Examples:
        >>> write_meta_yaml(Path("m.yaml"), {})  # doctest: +SKIP
    """
    try:
        import yaml
    except ImportError as error:
        raise ImportError(
            "PyYAML이 없습니다: 메타데이터 파일을 만들려면 "
            "'uv sync'로 의존성을 설치하거나 --no-meta로 끄세요."
        ) from error
    body = yaml.safe_dump(
        meta, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    path.write_text(_GUIDE + body, encoding="utf-8")
