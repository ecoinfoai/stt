#!/usr/bin/env python3
"""이미 전사한 파일에서 화자의 목소리를 등록한다.

`--diarize` 전사문에서 '화자1이 누구인지' 사람이 확인한 결과를 등록
지도(YAML)로 적어 주면, 그 파일들을 다시 화자 분리해 화자별 임베딩을
뽑아 이름과 함께 저장한다. 저장한 사전을 `stt transcribe --voice-db`로
넘기면 다음부터는 '화자1' 대신 실명이 붙고, 여러 편에 걸쳐 나오는
사람이 편마다 다른 번호를 받는 일도 없어진다.

등록 지도 형식:
    - media: "data/강연.m4a"
      speakers:
        화자1: 한석준
        화자2: 차인표

사용 예:
    stt enroll --map enroll.yaml --db voices.json
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class Entry:
    """등록 지도의 한 줄.

    Attributes:
        media: 목소리를 뽑아낼 미디어 파일 경로.
        speakers: pyannote 라벨 → 사람 이름.
    """

    media: Path
    speakers: dict[str, str]


def parse_enroll_map(data: Any) -> list[Entry]:
    """등록 지도 자료구조를 검사해 Entry 목록으로 바꾼다.

    '화자1'처럼 전사문에 보이는 표시용 이름과 'SPEAKER_00' 같은 원래
    라벨을 모두 받는다.

    Args:
        data: YAML에서 읽은 목록.

    Returns:
        Entry 목록.

    Raises:
        ValueError: 목록이 아니거나, media·speakers가 빠졌거나,
            이름이 비어 있을 때.

    Examples:
        >>> parse_enroll_map([{"media": "a.m4a", "speakers": {"화자1": "김"}}])
        [Entry(media=PosixPath('a.m4a'), speakers={'SPEAKER_00': '김'})]
    """
    from stt.diarize import label_for_display

    if not isinstance(data, list):
        raise ValueError(
            "잘못된 등록 지도: 최상위는 '- media: ...' 형태의 목록이어야 "
            "합니다."
        )
    if not data:
        raise ValueError(
            "빈 등록 지도: 등록할 항목이 하나도 없습니다."
        )
    entries: list[Entry] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict) or not item.get("media"):
            raise ValueError(
                f"등록 지도 {index}번째 항목에 'media'가 없습니다."
            )
        speakers = item.get("speakers")
        if not isinstance(speakers, dict) or not speakers:
            raise ValueError(
                f"등록 지도 {index}번째 항목('{item['media']}')에 "
                f"'speakers'가 없거나 비어 있습니다."
            )
        mapped: dict[str, str] = {}
        for label, name in speakers.items():
            if not str(name).strip():
                raise ValueError(
                    f"등록 지도 {index}번째 항목의 '{label}'에 이름이 "
                    f"비어 있습니다."
                )
            mapped[label_for_display(str(label))] = str(name).strip()
        entries.append(Entry(media=Path(str(item["media"])), speakers=mapped))
    return entries


def load_enroll_map(path: Path) -> list[Entry]:
    """등록 지도 YAML을 읽어 Entry 목록으로 바꾼다.

    Args:
        path: 등록 지도 YAML 경로.

    Returns:
        Entry 목록.

    Raises:
        FileNotFoundError: 파일이 없을 때.
        ValueError: 형식이 어긋날 때.

    Examples:
        >>> load_enroll_map(Path("enroll.yaml"))  # doctest: +SKIP
    """
    import yaml

    if not path.is_file():
        raise FileNotFoundError(
            f"등록 지도 없음: '{path}'를 찾을 수 없습니다. "
            f"--map 경로를 확인하세요."
        )
    return parse_enroll_map(yaml.safe_load(path.read_text(encoding="utf-8")))


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """CLI 인자를 파싱한다.

    Args:
        argv: sys.argv[1:] 형태의 인자 목록.

    Returns:
        파싱된 argparse.Namespace.

    Examples:
        >>> parse_args(["--map", "enroll.yaml"]).device
        'auto'
    """
    parser = argparse.ArgumentParser(
        prog="stt enroll",
        description="전사한 파일에서 화자 목소리를 등록해 사전에 쌓는다.",
    )
    parser.add_argument(
        "--map", type=Path, required=True,
        help="등록 지도 YAML (media/speakers 목록)",
    )
    parser.add_argument(
        "--db", type=Path, default=Path("voices.json"),
        help="목소리 사전 JSON (기본: voices.json, 있으면 이어서 쌓는다)",
    )
    parser.add_argument(
        "--device", default="auto", choices=["auto", "cuda", "cpu"],
        help="실행 장치 (기본: auto)",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점: 등록 지도를 따라 목소리를 뽑아 사전에 쌓는다.

    Args:
        argv: 테스트용 인자 목록(None이면 sys.argv 사용).

    Returns:
        종료 코드(성공 0).

    Examples:
        >>> main(["--help"])  # doctest: +SKIP
    """
    import sys
    import tempfile

    from stt import diarize as dz
    from stt.transcribe import detect_device

    args = parse_args(sys.argv[1:] if argv is None else argv)
    entries = load_enroll_map(args.map)
    device = detect_device(args.device)
    voices = dz.load_voice_db(args.db)
    print(f"등록 대상 {len(entries)}개 | 장치: {device}")
    pipeline = dz.load_pipeline(device)
    added = 0
    for index, entry in enumerate(entries, start=1):
        if not entry.media.is_file():
            raise FileNotFoundError(
                f"미디어 없음: '{entry.media}'를 찾을 수 없습니다."
            )
        print(f"[{index}/{len(entries)}] {entry.media.name}")
        with tempfile.TemporaryDirectory() as tmp:
            wav = dz.to_wav(entry.media, Path(tmp) / "audio.wav")
            labels, embeddings = dz.diarize_embeddings(pipeline, wav)
        found = dict(zip(labels, embeddings))
        for label, name in entry.speakers.items():
            if label not in found:
                raise ValueError(
                    f"'{entry.media.name}'에 화자 '{label}'이 없습니다. "
                    f"이 파일의 화자는 {sorted(found)}입니다."
                )
            voices = dz.merge_voice(voices, name, found[label])
            added += 1
            print(f"  {dz.speaker_label(label)} → {name}")
    dz.save_voice_db(voices, args.db)
    print(f"등록 완료: {added}건 → {args.db} (이름 {len(voices)}명)")
    return 0
