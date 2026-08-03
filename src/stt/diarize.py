#!/usr/bin/env python3
"""전사문에 화자 구분(speaker diarization)을 입힌다.

pyannote.audio가 찾은 화자 구간(턴)과 faster-whisper의 단어 단위
타임스탬프를 시간으로 맞물려, 화자가 바뀌는 지점에서 끊은 문단
목록을 만든다. 배정은 단어가 아니라 문장 단위로 한다 — 화자 경계에
걸친 문장이 중간에서 잘리는 것을 막기 위해서다.

화자 턴이 짧으면 분리 정확도가 떨어지므로, 일정 길이 미만인 문단은
'(?)'로 표시해 인용 근거로 쓰지 않도록 남긴다.

이 모듈의 무거운 의존성(torch, pyannote.audio)은 optional extra다:
    uv sync --extra diarize
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from stt.transcribe import format_timestamp

#: 문장이 끝났다고 보는 문자.
_SENTENCE_ENDINGS: tuple[str, ...] = (".", "?", "!", "…")

#: pyannote가 붙이는 화자 이름의 접두사.
_RAW_SPEAKER_PREFIX: str = "SPEAKER_"

#: 화자 분리 파이프라인 이름(HuggingFace 게이트 모델).
PIPELINE_NAME: str = "pyannote/speaker-diarization-3.1"

#: 이 길이 미만인 문단은 화자 판정을 신뢰하지 않는다(초).
DEFAULT_MIN_SECONDS: float = 1.5


class WordLike(Protocol):
    """faster-whisper Word가 제공하는 최소 속성 집합."""

    start: float
    end: float
    word: str


@dataclass(frozen=True)
class Turn:
    """한 화자가 이어서 말한 구간.

    Attributes:
        start: 시작 시각(초).
        end: 끝 시각(초).
        speaker: 화자 이름(예: 'SPEAKER_00').
    """

    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class Block:
    """화자 하나가 이어서 말한 문단.

    Attributes:
        start: 시작 시각(초).
        end: 끝 시각(초).
        speaker: 화자 이름(예: 'SPEAKER_00').
        text: 문단 본문.
        uncertain: 너무 짧아 화자 판정을 믿기 어려우면 True.
    """

    start: float
    end: float
    speaker: str
    text: str
    uncertain: bool


def speaker_at(start: float, end: float, turns: Sequence[Turn]) -> str | None:
    """구간 [start, end]와 가장 많이 겹치는 화자를 찾는다.

    Args:
        start: 구간 시작 시각(초).
        end: 구간 끝 시각(초).
        turns: 화자 턴 목록.

    Returns:
        가장 많이 겹치는 화자 이름. 겹치는 턴이 없으면 None.

    Examples:
        >>> speaker_at(0.0, 1.0, [Turn(0.0, 2.0, "SPEAKER_00")])
        'SPEAKER_00'
    """
    best: str | None = None
    best_overlap = 0.0
    for turn in turns:
        overlap = min(end, turn.end) - max(start, turn.start)
        if overlap > best_overlap:
            best, best_overlap = turn.speaker, overlap
    return best


def nearest_speaker(start: float, turns: Sequence[Turn]) -> str:
    """시각 start에서 시간상 가장 가까운 턴의 화자를 찾는다.

    Args:
        start: 기준 시각(초).
        turns: 화자 턴 목록(비어 있으면 안 된다).

    Returns:
        가장 가까운 턴의 화자 이름.

    Raises:
        ValueError: turns가 비어 있을 때.

    Examples:
        >>> nearest_speaker(9.0, [Turn(10.0, 11.0, "SPEAKER_01")])
        'SPEAKER_01'
    """
    if not turns:
        raise ValueError(
            "빈 화자 턴 목록: nearest_speaker()에는 턴이 하나 이상 "
            "있어야 합니다. 화자 분리가 실패했는지 확인하세요."
        )
    return min(
        turns,
        key=lambda t: 0.0 if t.start <= start <= t.end
        else min(abs(start - t.start), abs(start - t.end)),
    ).speaker


def group_sentences(words: Sequence[WordLike]) -> list[list[WordLike]]:
    """단어 목록을 문장 단위로 묶는다.

    문장 종결 문자로 끝나는 단어에서 끊는다. 마지막 문장이 종결되지
    않았으면 그대로 한 묶음으로 남긴다.

    Args:
        words: start/end/word 속성을 가진 단어 목록.

    Returns:
        문장별 단어 묶음 목록.

    Examples:
        >>> group_sentences([])
        []
    """
    sentences: list[list[WordLike]] = []
    current: list[WordLike] = []
    for item in words:
        current.append(item)
        if item.word.strip().endswith(_SENTENCE_ENDINGS):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def assign_blocks(
    words: Sequence[WordLike],
    turns: Sequence[Turn],
    min_seconds: float = DEFAULT_MIN_SECONDS,
    gap_s: float = 2.0,
    max_chars: int = 800,
) -> list[Block]:
    """단어와 화자 턴을 맞물려 화자별 문단 목록을 만든다.

    문장 단위로 화자를 정하고(그 문장 전체와 가장 많이 겹치는 화자),
    같은 화자의 문장이 이어지면 한 문단으로 합친다. 겹치는 턴이 없는
    문장은 시간상 가장 가까운 화자로 배정해 미배정을 남기지 않는다.

    화자가 바뀌지 않아도 말의 쉼(gap_s 이상)이나 길이(max_chars)에서
    문단을 나눈다 — 단독 강연이 통짜 한 덩어리가 되는 것을 막기 위해서다.

    Args:
        words: start/end/word 속성을 가진 단어 목록.
        turns: 화자 턴 목록.
        min_seconds: 이 길이 미만인 문단에 uncertain 표시를 단다.
        gap_s: 같은 화자 안에서 문단을 나누는 무음 길이(초).
        max_chars: 같은 화자 안에서 문단을 나누는 글자 수 기준.

    Returns:
        (시작, 끝, 화자, 본문, 불확실) 문단 목록.

    Raises:
        ValueError: min_seconds가 음수이거나 gap_s·max_chars가 0 이하일 때.

    Examples:
        >>> assign_blocks([], [Turn(0.0, 1.0, "SPEAKER_00")], 1.0)
        []
    """
    if min_seconds < 0:
        raise ValueError(
            f"잘못된 min_seconds: {min_seconds}. assign_blocks()의 "
            f"min_seconds는 0 이상이어야 합니다."
        )
    if gap_s <= 0:
        raise ValueError(
            f"잘못된 gap_s: {gap_s}. assign_blocks()의 gap_s는 "
            f"0보다 큰 초 단위 값이어야 합니다."
        )
    if max_chars <= 0:
        raise ValueError(
            f"잘못된 max_chars: {max_chars}. assign_blocks()의 "
            f"max_chars는 0보다 큰 정수여야 합니다."
        )
    parts: list[dict] = []
    for sentence in group_sentences(words):
        start, end = sentence[0].start, sentence[-1].end
        speaker = speaker_at(start, end, turns)
        if speaker is None:
            speaker = nearest_speaker(start, turns)
        text = "".join(item.word for item in sentence)
        same_speaker = bool(parts) and parts[-1]["speaker"] == speaker
        if same_speaker:
            paused = (start - parts[-1]["end"]) >= gap_s
            too_long = len(parts[-1]["text"]) >= max_chars
        if same_speaker and not paused and not too_long:
            parts[-1]["text"] += text
            parts[-1]["end"] = end
        else:
            parts.append(
                {"start": start, "end": end, "speaker": speaker, "text": text}
            )
    return [
        Block(
            start=p["start"],
            end=p["end"],
            speaker=p["speaker"],
            text=p["text"].strip(),
            uncertain=(p["end"] - p["start"]) < min_seconds,
        )
        for p in parts
        if p["text"].strip()
    ]


def speaker_label(speaker: str) -> str:
    """pyannote의 화자 이름을 사람이 읽을 이름으로 바꾼다.

    'SPEAKER_00' 형태는 1부터 세는 '화자N'으로 바꾸고, 그 밖의 값은
    사람이 이미 이름을 적어 넣은 것으로 보고 그대로 둔다.

    Args:
        speaker: 화자 이름.

    Returns:
        표시용 화자 이름.

    Examples:
        >>> speaker_label("SPEAKER_00")
        '화자1'
        >>> speaker_label("차인표")
        '차인표'
    """
    if not speaker.startswith(_RAW_SPEAKER_PREFIX):
        return speaker
    index = speaker[len(_RAW_SPEAKER_PREFIX):]
    if not index.isdigit():
        return speaker
    return f"화자{int(index) + 1}"


def render_diarized(blocks: Sequence[Block], timestamps: bool) -> str:
    """화자 문단 목록을 TXT 본문 문자열로 만든다.

    Args:
        blocks: 화자 문단 목록.
        timestamps: True면 문단 앞에 '[MM:SS] '를 붙인다.

    Returns:
        문단 사이가 빈 줄로 구분된 본문 문자열(끝에 개행 포함).

    Raises:
        ValueError: blocks가 비어 있을 때.

    Examples:
        >>> render_diarized([Block(0.0, 1.0, "SPEAKER_00", "네.", False)], True)
        '[00:00] 화자1: 네.\\n'
    """
    if not blocks:
        raise ValueError(
            "빈 화자 분리 결과: 문단이 하나도 없습니다. 음성이 없는 "
            "파일이거나 화자 분리가 아무 구간도 찾지 못했을 수 있습니다."
        )
    lines = []
    for block in blocks:
        mark = "(?)" if block.uncertain else ""
        head = f"[{format_timestamp(block.start)}] " if timestamps else ""
        lines.append(f"{head}{speaker_label(block.speaker)}{mark}: {block.text}")
    return "\n\n".join(lines) + "\n"


def load_pipeline(device: str):
    """pyannote 화자 분리 파이프라인을 적재한다.

    HuggingFace 게이트 모델이라 HF_TOKEN 환경변수가 있어야 한다.

    Args:
        device: 'cuda' 또는 'cpu'.

    Returns:
        적재된 pyannote Pipeline 인스턴스.

    Raises:
        RuntimeError: HF_TOKEN이 없거나 pyannote.audio가 설치되지 않았을 때.

    Examples:
        >>> load_pipeline("cpu")  # doctest: +SKIP
    """
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN 없음: 화자 분리에는 HuggingFace 토큰이 필요합니다. "
            "https://huggingface.co/settings/tokens 에서 발급하고 "
            "pyannote/segmentation-3.0, pyannote/speaker-diarization-3.1, "
            "pyannote/speaker-diarization-community-1 세 모델에 동의한 뒤 "
            "HF_TOKEN 환경변수로 넘기세요."
        )
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as error:
        raise RuntimeError(
            f"pyannote.audio 없음: {error}. --diarize를 쓰려면 "
            f"'uv sync --extra diarize'로 화자 분리 의존성을 설치하세요."
        ) from error
    pipeline = Pipeline.from_pretrained(PIPELINE_NAME, token=token)
    if device == "cuda" and torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
    return pipeline


def ffmpeg_wav_command(src: Path, dst: Path) -> list[str]:
    """미디어를 16kHz 모노 WAV으로 바꾸는 ffmpeg 인자 목록을 만든다.

    셸을 거치지 않도록 인자 목록으로 돌려준다 — 파일 이름에 든 공백이나
    따옴표가 명령으로 해석될 여지를 없애기 위해서다.

    Args:
        src: 원본 미디어 경로.
        dst: 만들 WAV 경로.

    Returns:
        subprocess에 그대로 넘길 인자 목록.

    Examples:
        >>> ffmpeg_wav_command(Path("a.m4a"), Path("b.wav"))[0]
        'ffmpeg'
    """
    return [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(src), "-ac", "1", "-ar", "16000", str(dst),
    ]


def to_wav(src: Path, dst: Path) -> Path:
    """미디어를 화자 분리용 16kHz 모노 WAV으로 변환한다.

    Args:
        src: 원본 미디어 경로.
        dst: 만들 WAV 경로.

    Returns:
        만들어진 WAV 경로.

    Raises:
        RuntimeError: ffmpeg이 없거나 변환에 실패했을 때.

    Examples:
        >>> to_wav(Path("a.m4a"), Path("b.wav"))  # doctest: +SKIP
    """
    import subprocess

    try:
        subprocess.run(ffmpeg_wav_command(src, dst), check=True)
    except FileNotFoundError as error:
        raise RuntimeError(
            "ffmpeg 없음: 화자 분리는 오디오를 WAV으로 바꾼 뒤 진행합니다. "
            "'nix develop' 안에서 실행하거나 ffmpeg을 설치하세요."
        ) from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"WAV 변환 실패: '{src.name}'를 16kHz 모노로 바꾸지 못했습니다 "
            f"(ffmpeg 종료 코드 {error.returncode})."
        ) from error
    return dst


def read_wav(path: Path) -> tuple["object", int]:
    """16-bit PCM WAV을 (채널, 샘플) 파형 텐서로 읽는다.

    이 호스트의 torchcodec은 ffmpeg 4의 libavutil.so.56을 찾다 실패하므로
    pyannote에 파일 경로 대신 파형을 직접 넘기려고 여기서 디코딩한다.

    Args:
        path: 16-bit PCM WAV 파일 경로.

    Returns:
        (파형 텐서, 샘플레이트) 튜플.

    Raises:
        ValueError: 16-bit PCM WAV이 아닐 때.

    Examples:
        >>> read_wav(Path("a.wav"))  # doctest: +SKIP
    """
    import wave

    import numpy as np
    import torch

    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError(
                f"지원하지 않는 WAV: '{path.name}'는 16-bit PCM이 아닙니다. "
                f"ffmpeg로 '-ac 1 -ar 16000' 변환을 거쳐야 합니다."
            )
        sample_rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return torch.from_numpy(pcm.reshape(-1, channels).T.copy()), sample_rate


def diarize_wav(pipeline, wav: Path, num_speakers: int | None = None) -> list[Turn]:
    """WAV 파일에서 화자 턴 목록을 뽑는다.

    Args:
        pipeline: load_pipeline()이 적재한 파이프라인.
        wav: 16-bit PCM WAV 파일 경로.
        num_speakers: 화자 수를 아는 경우의 힌트(모르면 None).

    Returns:
        시작 시각 순으로 정렬된 화자 턴 목록.

    Raises:
        RuntimeError: 화자 구간을 하나도 찾지 못했을 때.

    Examples:
        >>> diarize_wav(p, Path("a.wav"))  # doctest: +SKIP
    """
    waveform, sample_rate = read_wav(wav)
    kwargs = {} if num_speakers is None else {"num_speakers": num_speakers}
    output = pipeline(
        {"waveform": waveform, "sample_rate": sample_rate}, **kwargs
    )
    annotation = output.speaker_diarization
    turns = [
        Turn(start=segment.start, end=segment.end, speaker=label)
        for segment, _, label in annotation.itertracks(yield_label=True)
    ]
    if not turns:
        raise RuntimeError(
            f"빈 화자 분리 결과: '{wav.name}'에서 화자 구간을 찾지 "
            f"못했습니다. 무음 파일이 아닌지 확인하세요."
        )
    return sorted(turns, key=lambda t: t.start)
