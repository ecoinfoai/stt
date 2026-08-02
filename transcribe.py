#!/usr/bin/env python3
"""강연·강의 영상/녹음을 한국어 전사문(TXT)으로 바꾸는 CLI 도구.

faster-whisper(Whisper 계열 음성인식)로 받아쓰고, 짧은 세그먼트를
문장·문단으로 복원해 읽기 좋은 .txt로 저장한다. NVIDIA GPU가 있으면
자동으로 사용하고, VRAM이 부족하면 더 작은 모델로 자동 폴백한다.
영상(.mp4 .mkv ...)과 녹음(.m4a .mp3 .wav ...)을 구분 없이 받는다.

사용 예:
    python transcribe.py 강연.mp4
    python transcribe.py 녹음.m4a 강의폴더 --srt
    python transcribe.py 강연.mp4 --terms terms.txt --no-timestamps
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Protocol, Sequence, TypeAlias

if TYPE_CHECKING:  # 무거운 import는 실행 시점으로 미룬다.
    from faster_whisper import WhisperModel

Paragraph: TypeAlias = tuple[float, str]

SUPPORTED_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts",
    ".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma",
})

#: 자동 모드에서 시도하는 모델 순서(앞이 실패하면 뒤로 폴백).
GPU_MODEL_CHAIN: tuple[str, ...] = ("large-v3", "large-v3-turbo", "small")
CPU_DEFAULT_MODEL: str = "small"

_SENTENCE_ENDINGS: tuple[str, ...] = (".", "?", "!", "…")
_OOM_MARKERS: tuple[str, ...] = ("out of memory", "failed to allocate")
_DLL_MARKERS: tuple[str, ...] = ("cublas", "cudnn", "cannot be loaded")


class SegmentLike(Protocol):
    """faster-whisper Segment가 제공하는 최소 속성 집합."""

    start: float
    end: float
    text: str


def prepare_dll_search_path() -> None:
    """Windows에서 CUDA DLL 탐색 경로에 python.exe 폴더를 더한다.

    cuBLAS·cuDNN DLL을 venv의 Scripts 폴더(python.exe 옆)에 두는
    설치 방식을 어떤 DLL 탐색 모드에서도 인식되게 하는 안전장치.
    Windows가 아니면 아무것도 하지 않는다.

    Examples:
        >>> prepare_dll_search_path()  # 어느 OS에서든 호출 안전
    """
    if sys.platform != "win32":
        return
    exe_dir = str(Path(sys.executable).parent)
    os.add_dll_directory(exe_dir)
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = exe_dir + os.pathsep + current


def format_timestamp(seconds: float) -> str:
    """초 단위 시간을 'MM:SS' 또는 'H:MM:SS' 문자열로 바꾼다.

    Args:
        seconds: 0 이상인 초 단위 시간.

    Returns:
        1시간 미만이면 'MM:SS', 이상이면 'H:MM:SS' 형식 문자열.

    Raises:
        ValueError: seconds가 음수일 때.

    Examples:
        >>> format_timestamp(75.4)
        '01:15'
        >>> format_timestamp(3725.0)
        '1:02:05'
    """
    if seconds < 0:
        raise ValueError(
            f"잘못된 시간: {seconds}는 음수입니다. "
            f"format_timestamp()에는 0 이상의 초를 넘겨야 합니다."
        )
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def srt_timestamp(seconds: float) -> str:
    """초 단위 시간을 SRT 형식 'HH:MM:SS,mmm'으로 바꾼다.

    Args:
        seconds: 0 이상인 초 단위 시간.

    Returns:
        'HH:MM:SS,mmm' 형식 문자열.

    Raises:
        ValueError: seconds가 음수일 때.

    Examples:
        >>> srt_timestamp(75.5)
        '00:01:15,500'
    """
    if seconds < 0:
        raise ValueError(
            f"잘못된 시간: {seconds}는 음수입니다. "
            f"srt_timestamp()에는 0 이상의 초를 넘겨야 합니다."
        )
    total_ms = round(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def discover_media(paths: Sequence[Path]) -> list[Path]:
    """입력 경로들에서 처리할 미디어 파일 목록을 만든다.

    파일이면 확장자를 검사해 그대로 쓰고, 폴더면 바로 아래의
    지원 확장자 파일들을 이름순으로 모은다(하위 폴더 제외).

    Args:
        paths: 파일 또는 폴더 경로 목록.

    Returns:
        처리할 미디어 파일 경로 목록(입력 순서, 폴더 안은 이름순).

    Raises:
        FileNotFoundError: 존재하지 않는 경로가 있을 때.
        ValueError: 지원하지 않는 파일이거나 결과가 비었을 때.

    Examples:
        >>> discover_media([Path("강연.mp4")])  # doctest: +SKIP
        [PosixPath('강연.mp4')]
    """
    found: list[Path] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(
                f"경로 없음: '{path}'를 찾을 수 없습니다. "
                f"파일/폴더 경로를 확인하세요."
            )
        if path.is_file():
            if path.suffix.lower() not in SUPPORTED_EXTS:
                supported = ", ".join(sorted(SUPPORTED_EXTS))
                raise ValueError(
                    f"지원하지 않는 파일: '{path.name}'. "
                    f"지원 확장자: {supported}"
                )
            found.append(path)
        else:
            found.extend(sorted(
                p for p in path.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
            ))
    if not found:
        raise ValueError(
            f"처리할 미디어가 없습니다: {[str(p) for p in paths]} 안에 "
            f"지원 확장자 파일이 없습니다."
        )
    return found


def load_terms(path: Path) -> str:
    """용어 파일을 읽어 쉼표로 이은 용어 문자열을 만든다.

    한 줄에 용어 하나를 쓰며, '#'로 시작하는 줄과 빈 줄은 무시한다.
    결과는 Whisper의 hotwords로 주입되어 전문용어 표기를 유도한다.

    Args:
        path: 용어 파일 경로(UTF-8 텍스트).

    Returns:
        '용어1, 용어2, ...' 형태의 문자열.

    Raises:
        FileNotFoundError: 파일이 없을 때.
        ValueError: 유효한 용어가 한 줄도 없을 때.

    Examples:
        >>> load_terms(Path("terms.txt"))  # doctest: +SKIP
        '기업가정신, 비즈니스 모델 캔버스'
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"용어 파일 없음: '{path}'를 찾을 수 없습니다. "
            f"--terms 경로를 확인하세요."
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    terms = [
        ln.strip() for ln in lines
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not terms:
        raise ValueError(
            f"빈 용어 파일: '{path}'에 유효한 용어가 없습니다. "
            f"한 줄에 용어 하나씩 적어 주세요('#' 줄은 주석)."
        )
    return ", ".join(terms)


def build_paragraphs(
    segments: Iterable[SegmentLike],
    gap_s: float = 2.0,
    max_chars: int = 800,
) -> list[Paragraph]:
    """세그먼트들을 읽기 좋은 문단 목록으로 합친다.

    말의 쉼(gap_s 이상)에서 문단을 나누고, 한 문단이 max_chars를
    넘으면 문장이 끝나는 지점에서 나눈다.

    Args:
        segments: start/end/text 속성을 가진 세그먼트 목록.
        gap_s: 문단을 나누는 무음 길이(초). 0보다 커야 한다.
        max_chars: 문단 최대 글자 수 기준. 0보다 커야 한다.

    Returns:
        (시작 시각, 문단 텍스트) 튜플 목록.

    Raises:
        ValueError: gap_s 또는 max_chars가 0 이하일 때.

    Examples:
        >>> build_paragraphs([], gap_s=2.0, max_chars=800)
        []
    """
    if gap_s <= 0:
        raise ValueError(
            f"잘못된 gap_s: {gap_s}. build_paragraphs()의 gap_s는 "
            f"0보다 큰 초 단위 값이어야 합니다."
        )
    if max_chars <= 0:
        raise ValueError(
            f"잘못된 max_chars: {max_chars}. build_paragraphs()의 "
            f"max_chars는 0보다 큰 정수여야 합니다."
        )
    paragraphs: list[Paragraph] = []
    parts: list[str] = []
    start = 0.0
    prev_end = 0.0
    length = 0
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if parts:
            gap_break = (segment.start - prev_end) >= gap_s
            length_break = (
                length >= max_chars
                and parts[-1].endswith(_SENTENCE_ENDINGS)
            )
            if gap_break or length_break:
                paragraphs.append((start, " ".join(parts)))
                parts, length = [], 0
        if not parts:
            start = segment.start
        parts.append(text)
        length += len(text) + 1
        prev_end = segment.end
    if parts:
        paragraphs.append((start, " ".join(parts)))
    return paragraphs


def render_txt(paragraphs: Sequence[Paragraph], timestamps: bool) -> str:
    """문단 목록을 TXT 본문 문자열로 만든다.

    Args:
        paragraphs: (시작 시각, 텍스트) 문단 목록.
        timestamps: True면 문단 앞에 '[MM:SS] '를 붙인다.

    Returns:
        문단 사이가 빈 줄로 구분된 본문 문자열(끝에 개행 포함).

    Raises:
        ValueError: paragraphs가 비어 있을 때.

    Examples:
        >>> render_txt([(0.0, "안녕.")], timestamps=True)
        '[00:00] 안녕.\\n'
    """
    if not paragraphs:
        raise ValueError(
            "빈 전사 결과: 문단이 하나도 없습니다. 음성이 없는 "
            "파일이거나 VAD가 전부 걸러냈을 수 있습니다."
        )
    blocks = [
        f"[{format_timestamp(s)}] {t}" if timestamps else t
        for s, t in paragraphs
    ]
    return "\n\n".join(blocks) + "\n"


def build_header(
    source_name: str,
    model_name: str,
    language: str,
    duration_s: float,
) -> str:
    """전사문 머리에 붙일 메타데이터 주석 블록을 만든다.

    Args:
        source_name: 원본 파일 이름.
        model_name: 사용한 Whisper 모델 이름.
        language: 전사 언어 코드(예: 'ko').
        duration_s: 원본 길이(초).

    Returns:
        '#'로 시작하는 줄들 + 빈 줄로 끝나는 헤더 문자열.

    Examples:
        >>> "large-v3" in build_header("a.m4a", "large-v3", "ko", 60.0)
        True
    """
    duration = format_timestamp(duration_s)
    lines = [
        f"# 원본: {source_name}",
        f"# 모델: faster-whisper {model_name} | 언어: {language} "
        f"| 길이: {duration}",
    ]
    return "\n".join(lines) + "\n\n"


def render_srt(segments: Sequence[SegmentLike]) -> str:
    """세그먼트 목록을 SRT 자막 문자열로 만든다.

    Args:
        segments: start/end/text 속성을 가진 세그먼트 목록.

    Returns:
        표준 SRT 형식 문자열(끝에 개행 포함).

    Raises:
        ValueError: segments가 비어 있을 때.

    Examples:
        >>> render_srt([])  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ValueError: ...
    """
    if not segments:
        raise ValueError(
            "빈 전사 결과: SRT로 만들 세그먼트가 없습니다."
        )
    blocks = [
        f"{i}\n{srt_timestamp(s.start)} --> {srt_timestamp(s.end)}\n"
        f"{s.text.strip()}"
        for i, s in enumerate(segments, start=1)
    ]
    return "\n\n".join(blocks) + "\n"


def detect_device(requested: str) -> str:
    """실행 장치를 결정한다('auto'면 CUDA 유무로 판단).

    Args:
        requested: 'auto', 'cuda', 'cpu' 중 하나.

    Returns:
        'cuda' 또는 'cpu'.

    Raises:
        ValueError: requested가 허용 값이 아닐 때.

    Examples:
        >>> detect_device("cpu")
        'cpu'
    """
    if requested not in ("auto", "cuda", "cpu"):
        raise ValueError(
            f"잘못된 device: '{requested}'. "
            f"'auto', 'cuda', 'cpu' 중 하나여야 합니다."
        )
    if requested != "auto":
        return requested
    import ctranslate2

    return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"


def resolve_model_chain(model_arg: str, device: str) -> tuple[str, ...]:
    """시도할 모델 이름 목록을 결정한다.

    'auto'면 GPU에서는 large-v3부터 폴백 체인을, CPU에서는 small을
    쓴다. 특정 모델을 지정하면 그 모델만 시도한다.

    Args:
        model_arg: 'auto' 또는 Whisper 모델 이름.
        device: 'cuda' 또는 'cpu'.

    Returns:
        시도 순서대로 정렬된 모델 이름 튜플.

    Examples:
        >>> resolve_model_chain("auto", "cpu")
        ('small',)
        >>> resolve_model_chain("medium", "cuda")
        ('medium',)
    """
    if model_arg != "auto":
        return (model_arg,)
    if device == "cuda":
        return GPU_MODEL_CHAIN
    return (CPU_DEFAULT_MODEL,)


def load_model(model_name: str, device: str) -> "WhisperModel":
    """faster-whisper 모델을 적재한다(첫 실행 시 자동 다운로드).

    Args:
        model_name: Whisper 모델 이름(예: 'large-v3').
        device: 'cuda' 또는 'cpu'.

    Returns:
        적재된 WhisperModel 인스턴스.

    Examples:
        >>> load_model("small", "cpu")  # doctest: +SKIP
    """
    from faster_whisper import WhisperModel

    compute = "int8_float16" if device == "cuda" else "int8"
    threads = os.cpu_count() or 4
    print(f"  모델 적재: {model_name} ({device}, {compute})")
    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute,
        cpu_threads=threads,
    )


def is_oom_error(error: RuntimeError) -> bool:
    """RuntimeError가 GPU 메모리 부족인지 판별한다.

    Args:
        error: 판별할 예외 객체.

    Returns:
        메모리 부족으로 보이면 True.

    Examples:
        >>> is_oom_error(RuntimeError("CUDA failed: out of memory"))
        True
    """
    message = str(error).lower()
    return any(marker in message for marker in _OOM_MARKERS)


def is_dll_error(error: RuntimeError) -> bool:
    """RuntimeError가 CUDA DLL 미인식 문제인지 판별한다.

    Args:
        error: 판별할 예외 객체.

    Returns:
        cuBLAS/cuDNN DLL을 못 찾은 오류로 보이면 True.

    Examples:
        >>> is_dll_error(RuntimeError("cublas64_12.dll not found"))
        True
    """
    message = str(error).lower()
    return any(marker in message for marker in _DLL_MARKERS)


def transcribe_file(
    model: "WhisperModel",
    media: Path,
    language: str | None,
    terms: str | None,
    beam_size: int,
) -> tuple[list[SegmentLike], float, str]:
    """미디어 파일 하나를 전사해 세그먼트 목록을 돌려준다.

    Args:
        model: 적재된 WhisperModel.
        media: 전사할 미디어 파일 경로.
        language: 언어 코드('ko' 등) 또는 자동 감지 시 None.
        terms: hotwords로 넣을 용어 문자열 또는 None.
        beam_size: 빔 서치 크기(클수록 정확, 느림).

    Returns:
        (세그먼트 목록, 오디오 길이(초), 전사 언어 코드) 튜플.

    Raises:
        RuntimeError: 전사 결과가 비어 있을 때.

    Examples:
        >>> transcribe_file(m, Path("a.m4a"), "ko", None, 5)  # doctest: +SKIP
    """
    segments_iter, info = model.transcribe(
        str(media),
        language=language,
        beam_size=beam_size,
        vad_filter=True,
        condition_on_previous_text=False,
        hotwords=terms,
        log_progress=True,
    )
    segments = list(segments_iter)  # 실제 전사는 여기서 진행된다.
    if not segments:
        raise RuntimeError(
            f"빈 전사 결과: '{media.name}'에서 음성을 찾지 못했습니다. "
            f"무음 파일이 아닌지 확인하세요."
        )
    return segments, info.duration, info.language


def output_path_for(media: Path, out_dir: Path | None, ext: str) -> Path:
    """미디어 파일에 대응하는 출력 파일 경로를 만든다.

    Args:
        media: 원본 미디어 경로.
        out_dir: 출력 폴더(None이면 원본 옆에 저장).
        ext: 출력 확장자('.txt' 또는 '.srt').

    Returns:
        출력 파일 경로.

    Examples:
        >>> output_path_for(Path("a/b.mp4"), None, ".txt")
        PosixPath('a/b.txt')
    """
    base = out_dir if out_dir is not None else media.parent
    return base / (media.stem + ext)


def build_run_info(model_name: str, language: str, duration_s: float):
    """메타데이터 파일에 넣을 전사 실행 정보를 만든다.

    metadata 모듈이 transcribe를 참조하므로 순환 import를 피하려고
    이 함수 안에서 늦게 불러온다.

    Args:
        model_name: 사용한 Whisper 모델 이름.
        language: 전사 언어 코드.
        duration_s: 원본 길이(초).

    Returns:
        metadata.RunInfo 인스턴스.

    Examples:
        >>> build_run_info("small", "ko", 60.0).language
        'ko'
    """
    import metadata

    return metadata.RunInfo(
        model=model_name,
        language=language,
        duration_s=duration_s,
        transcribed=date.today().isoformat(),
    )


def write_metadata(media: Path, out_dir: Path | None, run) -> Path:
    """전사한 미디어 옆에 메타데이터 YAML을 저장한다.

    Args:
        media: 전사한 미디어 파일 경로.
        out_dir: 출력 폴더(None이면 원본 옆).
        run: build_run_info()가 만든 실행 정보.

    Returns:
        저장한 .meta.yaml 경로.

    Examples:
        >>> write_metadata(Path("a.m4a"), None, r)  # doctest: +SKIP
    """
    import metadata

    return metadata.write_for_media(media, out_dir, run)


def process_one(
    model: "WhisperModel",
    model_name: str,
    media: Path,
    args: argparse.Namespace,
    terms: str | None,
) -> None:
    """파일 하나를 전사하고 TXT(및 선택적 SRT)로 저장한다.

    Args:
        model: 적재된 WhisperModel.
        model_name: 사용 중인 모델 이름(헤더 기록용).
        media: 전사할 미디어 파일.
        args: 파싱된 CLI 인자.
        terms: hotwords 용어 문자열 또는 None.

    Examples:
        >>> process_one(m, "small", Path("a.m4a"), ns, None)  # doctest: +SKIP
    """
    started = time.monotonic()
    requested = None if args.language == "auto" else args.language
    segments, duration, language = transcribe_file(
        model, media, requested, terms, args.beam
    )
    paragraphs = build_paragraphs(
        segments, gap_s=args.gap, max_chars=args.max_chars
    )
    header = build_header(media.name, model_name, language, duration)
    body = render_txt(paragraphs, timestamps=not args.no_timestamps)
    txt_path = output_path_for(media, args.output_dir, ".txt")
    txt_path.write_text(header + body, encoding="utf-8")
    outputs = [txt_path.name]
    if args.srt:
        srt_path = output_path_for(media, args.output_dir, ".srt")
        srt_path.write_text(render_srt(segments), encoding="utf-8")
        outputs.append(srt_path.name)
    if not args.no_meta:
        run = build_run_info(model_name, language, duration)
        meta_path = write_metadata(media, args.output_dir, run)
        outputs.append(meta_path.name)
    elapsed = time.monotonic() - started
    print(
        f"  완료({format_timestamp(elapsed)} 소요): "
        f"{', '.join(outputs)}"
    )


def run_with_fallback(
    pending: list[Path],
    chain: tuple[str, ...],
    device: str,
    args: argparse.Namespace,
    terms: str | None,
) -> None:
    """모델 폴백 체인을 따라 남은 파일들을 모두 처리한다.

    GPU 메모리 부족(RuntimeError)이 나면 모델을 내리고 체인의
    다음(더 작은) 모델로 이어서 처리한다.

    Args:
        pending: 아직 처리하지 않은 미디어 파일 목록(소모됨).
        chain: 시도할 모델 이름 순서.
        device: 'cuda' 또는 'cpu'.
        args: 파싱된 CLI 인자.
        terms: hotwords 용어 문자열 또는 None.

    Raises:
        RuntimeError: 모든 모델을 시도해도 처리하지 못했을 때.

    Examples:
        >>> run_with_fallback([], ("small",), "cpu", ns, None)
        ...  # doctest: +SKIP
    """
    total = len(pending)
    for model_name in chain:
        model = None
        try:
            model = load_model(model_name, device)
            while pending:
                media = pending[0]
                done = total - len(pending) + 1
                print(f"[{done}/{total}] {media.name}")
                process_one(model, model_name, media, args, terms)
                pending.pop(0)
            return
        except RuntimeError as error:
            if is_dll_error(error) and not is_oom_error(error):
                raise RuntimeError(
                    f"{error}\n→ CUDA DLL을 찾지 못했습니다. "
                    f"cuBLAS·cuDNN DLL 파일들이 (하위 폴더가 아니라) "
                    f"venv의 Scripts 폴더 바로 안에 있는지 확인하세요. "
                    f"설치_사용_안내.md 1-3, 4장 참고."
                ) from error
            if not is_oom_error(error):
                raise
            print(
                f"  VRAM 부족으로 '{model_name}' 실패 → "
                f"더 작은 모델로 재시도합니다."
            )
        finally:
            del model
            gc.collect()
    raise RuntimeError(
        f"모델 체인 {chain}을 모두 시도했지만 VRAM이 부족합니다. "
        f"--model small 또는 --device cpu로 다시 실행해 보세요."
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """CLI 인자를 파싱한다.

    Args:
        argv: sys.argv[1:] 형태의 인자 목록.

    Returns:
        파싱된 argparse.Namespace.

    Examples:
        >>> parse_args(["a.mp4"]).language
        'ko'
    """
    parser = argparse.ArgumentParser(
        description=(
            "강연 영상/녹음 → 한국어 전사문(TXT). "
            "영상과 오디오 파일 모두 지원."
        ),
    )
    parser.add_argument(
        "paths", nargs="+", type=Path,
        help="미디어 파일 또는 폴더(여러 개 가능)",
    )
    parser.add_argument(
        "--model", default="auto",
        help="auto | large-v3 | large-v3-turbo | medium | small ...",
    )
    parser.add_argument(
        "--device", default="auto", choices=["auto", "cuda", "cpu"],
        help="실행 장치 (기본: auto)",
    )
    parser.add_argument(
        "--language", default="ko",
        help="언어 코드, 'auto'면 자동 감지 (기본: ko)",
    )
    parser.add_argument(
        "--terms", type=Path, default=None,
        help="용어 파일(한 줄에 하나, '#' 주석) — 표기 유도",
    )
    parser.add_argument(
        "--no-timestamps", action="store_true",
        help="문단 앞 [MM:SS] 표시를 뺀 순수 텍스트로 저장",
    )
    parser.add_argument(
        "--srt", action="store_true",
        help="SRT 자막 파일도 함께 저장",
    )
    parser.add_argument(
        "--no-meta", action="store_true",
        help="노트용 메타데이터(.meta.yaml)를 만들지 않는다",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="이미 있는 출력 파일을 다시 만든다",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="출력 폴더 (기본: 원본 파일 옆)",
    )
    parser.add_argument(
        "--beam", type=int, default=5,
        help="빔 서치 크기 (기본: 5, 빠르게는 1)",
    )
    parser.add_argument(
        "--gap", type=float, default=2.0,
        help="문단을 나누는 무음 길이(초, 기본: 2.0)",
    )
    parser.add_argument(
        "--max-chars", type=int, default=800,
        help="문단 최대 글자 수 (기본: 800)",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점: 인자 검증 → 파일 수집 → 전사 → 저장.

    Args:
        argv: 테스트용 인자 목록(None이면 sys.argv 사용).

    Returns:
        종료 코드(성공 0).

    Examples:
        >>> main(["--help"])  # doctest: +SKIP
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    prepare_dll_search_path()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.beam < 1:
        raise ValueError(
            f"잘못된 --beam: {args.beam}. 1 이상의 정수여야 합니다."
        )
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    media_files = discover_media(args.paths)
    terms = load_terms(args.terms) if args.terms is not None else None
    device = detect_device(args.device)
    chain = resolve_model_chain(args.model, device)
    pending: list[Path] = []
    for media in media_files:
        txt_path = output_path_for(media, args.output_dir, ".txt")
        if txt_path.exists() and not args.overwrite:
            print(f"건너뜀(이미 있음): {txt_path.name}")
            continue
        pending.append(media)
    if not pending:
        print("모든 파일이 이미 전사되어 있습니다 (--overwrite 참고).")
        return 0
    print(
        f"대상 {len(pending)}개 | 장치: {device} | "
        f"모델 후보: {' → '.join(chain)}"
    )
    run_with_fallback(pending, chain, device, args, terms)
    print("전체 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
