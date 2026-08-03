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

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, TypeAlias

from stt.transcribe import format_timestamp

#: 문장이 끝났다고 보는 문자.
_SENTENCE_ENDINGS: tuple[str, ...] = (".", "?", "!", "…")

#: pyannote가 붙이는 화자 이름의 접두사.
_RAW_SPEAKER_PREFIX: str = "SPEAKER_"

#: 화자 분리 파이프라인 이름(HuggingFace 게이트 모델).
PIPELINE_NAME: str = "pyannote/speaker-diarization-3.1"

#: 이 길이 미만인 문단은 화자 판정을 신뢰하지 않는다(초).
DEFAULT_MIN_SECONDS: float = 1.5

#: 등록된 목소리와 같은 사람으로 볼 최소 코사인 유사도.
DEFAULT_VOICE_THRESHOLD: float = 0.75

#: 이름 → 화자 임베딩.
VoiceDB: TypeAlias = dict[str, list[float]]


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


def label_for_display(name: str) -> str:
    """표시용 '화자N'을 pyannote 라벨 'SPEAKER_(N-1)'로 되돌린다.

    등록 지도(enroll map)에 사람이 전사문에서 본 '화자1' 그대로 적을 수
    있게 하려고 speaker_label()의 역함수를 둔다.

    Args:
        name: '화자N' 또는 이미 pyannote 라벨인 문자열.

    Returns:
        pyannote 라벨.

    Raises:
        ValueError: '화자0'처럼 1보다 작은 번호일 때.

    Examples:
        >>> label_for_display("화자1")
        'SPEAKER_00'
    """
    if not name.startswith("화자"):
        return name
    index = name[len("화자"):]
    if not index.isdigit():
        return name
    number = int(index)
    if number < 1:
        raise ValueError(
            f"잘못된 화자 번호: '{name}'. 화자 번호는 1부터 셉니다."
        )
    return f"{_RAW_SPEAKER_PREFIX}{number - 1:02d}"


def merge_voice(
    voices: VoiceDB, name: str, vector: Sequence[float]
) -> VoiceDB:
    """목소리 사전에 임베딩을 더한다(같은 이름이면 평균).

    같은 사람을 여러 파일에서 등록할수록 평균이 그 사람의 목소리를 더
    잘 대표한다.

    Args:
        voices: 기존 이름 → 임베딩 사전(변경하지 않는다).
        name: 등록할 이름.
        vector: 화자 임베딩.

    Returns:
        새 사전.

    Raises:
        ValueError: 같은 이름의 기존 임베딩과 차원이 다를 때.

    Examples:
        >>> merge_voice({}, "김", [1.0, 0.0])
        {'김': [1.0, 0.0]}
    """
    new = {key: list(value) for key, value in voices.items()}
    incoming = [float(x) for x in vector]
    existing = new.get(name)
    if existing is None:
        new[name] = incoming
        return new
    if len(existing) != len(incoming):
        raise ValueError(
            f"임베딩 차원 불일치: '{name}'의 기존 값은 {len(existing)}차원, "
            f"새 값은 {len(incoming)}차원입니다."
        )
    new[name] = [(a + b) / 2.0 for a, b in zip(existing, incoming)]
    return new


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """두 벡터의 코사인 유사도를 구한다.

    Args:
        a: 벡터 하나.
        b: 같은 길이의 벡터.

    Returns:
        -1.0 이상 1.0 이하의 유사도. 1에 가까울수록 같은 목소리다.

    Raises:
        ValueError: 길이가 다르거나 한쪽이 영벡터일 때.

    Examples:
        >>> cosine_similarity([1.0, 0.0], [1.0, 0.0])
        1.0
    """
    if len(a) != len(b):
        raise ValueError(
            f"벡터 길이 불일치: {len(a)} != {len(b)}. cosine_similarity()는 "
            f"같은 차원의 벡터 두 개를 받아야 합니다."
        )
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        raise ValueError(
            "영벡터: cosine_similarity()는 크기가 0인 벡터를 비교할 수 "
            "없습니다. 화자 임베딩이 비어 있는지 확인하세요."
        )
    return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)


def resolve_speaker_names(
    labels: Sequence[str],
    embeddings: Sequence[Sequence[float]],
    voices: VoiceDB,
    threshold: float = DEFAULT_VOICE_THRESHOLD,
) -> dict[str, str]:
    """화자 임베딩을 등록된 목소리와 대조해 실명을 붙인다.

    유사도가 높은 쌍부터 차례로 배정하며, 등록된 이름 하나는 한 화자에게만
    붙는다 — 같은 사람이 두 화자로 갈리는 일을 막기 위해서다. 문턱을 넘는
    짝이 없으면 원래 라벨(SPEAKER_00 등)을 그대로 둔다.

    Args:
        labels: 화자 라벨 목록(pyannote 순서).
        embeddings: labels와 같은 순서·개수의 임베딩 목록.
        voices: 이름 → 임베딩 사전.
        threshold: 같은 사람으로 볼 최소 코사인 유사도.

    Returns:
        라벨 → 표시할 이름 사전(대조 실패 시 라벨 그대로).

    Raises:
        ValueError: labels와 embeddings의 개수가 다를 때.

    Examples:
        >>> resolve_speaker_names(["SPEAKER_00"], [[1.0, 0.0]], {}, 0.8)
        {'SPEAKER_00': 'SPEAKER_00'}
    """
    if len(labels) != len(embeddings):
        raise ValueError(
            f"개수 불일치: 화자 {len(labels)}명인데 임베딩은 "
            f"{len(embeddings)}개입니다. pyannote 출력이 온전한지 "
            f"확인하세요."
        )
    pairs = [
        (cosine_similarity(embedding, vector), label, name)
        for label, embedding in zip(labels, embeddings)
        for name, vector in voices.items()
        if cosine_similarity(embedding, vector) >= threshold
    ]
    pairs.sort(key=lambda p: -p[0])
    resolved: dict[str, str] = {}
    taken: set[str] = set()
    for _, label, name in pairs:
        if label in resolved or name in taken:
            continue
        resolved[label] = name
        taken.add(name)
    return {label: resolved.get(label, label) for label in labels}


def rename_turns(
    turns: Sequence[Turn], mapping: dict[str, str]
) -> list[Turn]:
    """화자 턴의 라벨을 사전에 따라 바꾼다.

    Args:
        turns: 화자 턴 목록.
        mapping: 바꿀 라벨 → 새 이름 사전.

    Returns:
        라벨만 바뀐 새 턴 목록(시각은 그대로).

    Examples:
        >>> rename_turns([Turn(0.0, 1.0, "SPEAKER_00")], {"SPEAKER_00": "김"})
        [Turn(start=0.0, end=1.0, speaker='김')]
    """
    return [
        Turn(start=t.start, end=t.end, speaker=mapping.get(t.speaker, t.speaker))
        for t in turns
    ]


def load_voice_db(path: Path) -> VoiceDB:
    """등록된 목소리 사전을 읽는다.

    Args:
        path: JSON 파일 경로(이름 → 임베딩 배열).

    Returns:
        이름 → 임베딩 사전. 파일이 없으면 빈 사전.

    Raises:
        ValueError: JSON 형식이 이름 → 숫자 배열이 아닐 때.

    Examples:
        >>> load_voice_db(Path("없는파일.json"))
        {}
    """
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(v, list) and all(isinstance(x, (int, float)) for x in v)
        for v in data.values()
    ):
        raise ValueError(
            f"잘못된 목소리 사전: '{path}'는 이름 → 숫자 배열 형태의 "
            f"JSON이어야 합니다."
        )
    return {name: [float(x) for x in vector] for name, vector in data.items()}


def save_voice_db(voices: VoiceDB, path: Path) -> Path:
    """등록된 목소리 사전을 JSON으로 저장한다.

    Args:
        voices: 이름 → 임베딩 사전.
        path: 저장할 JSON 경로.

    Returns:
        저장한 경로.

    Examples:
        >>> save_voice_db({}, Path("voices.json"))  # doctest: +SKIP
    """
    path.write_text(
        json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


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


def diarize_embeddings(
    pipeline, wav: Path, num_speakers: int | None = None
) -> tuple[list[str], list[list[float]]]:
    """WAV에서 화자 라벨과 화자별 임베딩을 함께 뽑는다.

    pyannote는 임베딩을 `speaker_diarization.labels()` 순서로 돌려주므로
    두 목록의 i번째가 서로 짝이다.

    Args:
        pipeline: load_pipeline()이 적재한 파이프라인.
        wav: 16-bit PCM WAV 파일 경로.
        num_speakers: 화자 수를 아는 경우의 힌트(모르면 None).

    Returns:
        (라벨 목록, 임베딩 목록) 튜플.

    Raises:
        RuntimeError: 임베딩을 받지 못했을 때.

    Examples:
        >>> diarize_embeddings(p, Path("a.wav"))  # doctest: +SKIP
    """
    output = _run_pipeline(pipeline, wav, num_speakers)
    labels = list(output.speaker_diarization.labels())
    if output.speaker_embeddings is None:
        raise RuntimeError(
            f"화자 임베딩 없음: '{wav.name}'에서 목소리 벡터를 받지 "
            f"못했습니다. pyannote 파이프라인 버전을 확인하세요."
        )
    embeddings = [
        [float(x) for x in row] for row in output.speaker_embeddings
    ]
    return labels, embeddings


def _run_pipeline(pipeline, wav: Path, num_speakers: int | None):
    """WAV을 파형으로 읽어 파이프라인에 넘긴다.

    Args:
        pipeline: load_pipeline()이 적재한 파이프라인.
        wav: 16-bit PCM WAV 파일 경로.
        num_speakers: 화자 수 힌트 또는 None.

    Returns:
        pyannote DiarizeOutput.

    Examples:
        >>> _run_pipeline(p, Path("a.wav"), None)  # doctest: +SKIP
    """
    waveform, sample_rate = read_wav(wav)
    kwargs = {} if num_speakers is None else {"num_speakers": num_speakers}
    return pipeline(
        {"waveform": waveform, "sample_rate": sample_rate}, **kwargs
    )


def diarize_wav(
    pipeline,
    wav: Path,
    num_speakers: int | None = None,
    voices: VoiceDB | None = None,
    threshold: float = DEFAULT_VOICE_THRESHOLD,
) -> list[Turn]:
    """WAV 파일에서 화자 턴 목록을 뽑는다.

    Args:
        pipeline: load_pipeline()이 적재한 파이프라인.
        wav: 16-bit PCM WAV 파일 경로.
        num_speakers: 화자 수를 아는 경우의 힌트(모르면 None).
        voices: 등록된 목소리 사전(주면 화자 라벨을 실명으로 바꾼다).
        threshold: 같은 사람으로 볼 최소 코사인 유사도.

    Returns:
        시작 시각 순으로 정렬된 화자 턴 목록.

    Raises:
        RuntimeError: 화자 구간을 하나도 찾지 못했을 때.

    Examples:
        >>> diarize_wav(p, Path("a.wav"))  # doctest: +SKIP
    """
    output = _run_pipeline(pipeline, wav, num_speakers)
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
    if voices and output.speaker_embeddings is not None:
        labels = list(annotation.labels())
        embeddings = [
            [float(x) for x in row] for row in output.speaker_embeddings
        ]
        turns = rename_turns(
            turns, resolve_speaker_names(labels, embeddings, voices, threshold)
        )
    return sorted(turns, key=lambda t: t.start)
