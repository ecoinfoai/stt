"""Transcription engine: the subprocess the worker starts for one job.

Run as ``python -m stt.server.engine <work_dir>``. It reads ``spec.json`` from the
work folder, checks that a GPU and the pinned models are present, transcribes with
the same code the command line uses (``large-v3`` on ``cuda``, no fallback), writes
``out/transcript.txt`` and ``out/result.json``, and records its verdict in
``outcome.json``. Heavy libraries are imported only when a check or a step needs them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from stt.server import contract, models
from stt.server.layout import atomic_write_text

MODEL_NAME = "large-v3"
DEVICE = "cuda"
BEAM_SIZE = 5
PARAGRAPH_GAP_S = 2.0
PARAGRAPH_MAX_CHARS = 800

_DETAILS: dict[str, str] = {
    "gpu_unavailable": "The engine could not find a usable GPU.",
    "models_unavailable": "The pinned models are not present in the model cache.",
}


def cuda_device_count() -> int:
    """Returns the number of CUDA devices the transcription engine can see."""
    import ctranslate2

    return int(ctranslate2.get_cuda_device_count())


def torch_cuda_available() -> bool:
    """Returns whether torch, which the speaker engine uses, can use CUDA."""
    import torch

    return bool(torch.cuda.is_available())


def _write_outcome(work_dir: Path, outcome: dict[str, str]) -> None:
    atomic_write_text(work_dir / "outcome.json", json.dumps(outcome))


def _start_check_failure(diarize: bool) -> str | None:
    """Returns the failure reason of the start checks, or None when they pass.

    Raises:
        RuntimeError: If ``HF_HOME`` is not set in the environment.
    """
    if cuda_device_count() <= 0:
        return "gpu_unavailable"
    if diarize and not torch_cuda_available():
        return "gpu_unavailable"
    hf_home = os.environ.get("HF_HOME")
    if not hf_home:
        raise RuntimeError("HF_HOME is not set in the engine environment.")
    if models.ref_mismatches(Path(hf_home)):
        return "models_unavailable"
    return None


def _diarize_args() -> argparse.Namespace:
    """Builds the arguments ``transcribe.diarized_blocks`` reads, at command-line defaults."""
    from stt import transcribe

    return argparse.Namespace(
        voice_db=None,
        speakers=None,
        voice_threshold=transcribe.DEFAULT_VOICE_THRESHOLD,
        min_speaker_seconds=transcribe.DEFAULT_MIN_SPEAKER_SECONDS,
        gap=PARAGRAPH_GAP_S,
        max_chars=PARAGRAPH_MAX_CHARS,
    )


def _transcribe(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Runs the transcription and returns the transcript text and the result document."""
    from stt import diarize as dz
    from stt import transcribe

    started = time.monotonic()
    diarize: bool = spec["diarize"]
    hints: list[str] | None = spec["hints"]
    media = Path(spec["audio_path"])

    model = transcribe.load_model(MODEL_NAME, DEVICE)
    report = None
    terms = None
    if hints is not None:
        report = transcribe.build_terms_report(
            "hints",
            hints,
            lambda text: model.hf_tokenizer.encode(text, add_special_tokens=False).ids,
            transcribe.HOTWORD_TOKEN_LIMIT,
        )
        terms = ", ".join(hints[: report.applied]) or None
    segments, duration, language = transcribe.transcribe_file(
        model, media, spec["language"], terms, BEAM_SIZE, word_timestamps=diarize
    )
    header = transcribe.build_header(
        spec["original_name"], MODEL_NAME, language, duration, diarized=diarize
    )
    if diarize:
        pipeline = dz.load_pipeline(DEVICE)
        blocks = transcribe.diarized_blocks(media, segments, _diarize_args(), pipeline)
        body = dz.render_diarized(blocks, timestamps=True)
        paragraphs = [
            {
                "start": block.start,
                "end": block.end,
                "speaker": dz.speaker_label(block.speaker),
                "text": block.text,
                "uncertain": block.uncertain,
            }
            for block in blocks
        ]
    else:
        timed = transcribe.build_timed_paragraphs(
            segments, gap_s=PARAGRAPH_GAP_S, max_chars=PARAGRAPH_MAX_CHARS
        )
        body = transcribe.render_txt([(start, text) for start, _end, text in timed], True)
        paragraphs = [
            {"start": start, "end": end, "speaker": None, "text": text, "uncertain": False}
            for start, end, text in timed
        ]
    result = {
        "schema_version": 1,
        "request_id": spec["request_id"],
        "transcript": "transcript.txt",
        "paragraphs": paragraphs,
        "summary": {
            "model": MODEL_NAME,
            "device": DEVICE,
            "language": language,
            "audio_duration_s": duration,
            "processing_s": time.monotonic() - started,
            "diarized": diarize,
            "hints": None
            if report is None
            else {
                "applied": report.applied,
                "total": report.total,
                "dropped": list(report.dropped),
            },
        },
    }
    return header + body, result


def _write_results(work_dir: Path, transcript: str, result: dict[str, Any]) -> None:
    violation = contract.validate_result(result)
    if violation is not None:
        raise ValueError(f"The engine produced an invalid result document: {violation}")
    out = work_dir / "out"
    out.mkdir(exist_ok=True)
    atomic_write_text(out / "transcript.txt", transcript)
    atomic_write_text(out / "result.json", json.dumps(result, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    """Runs one job in ``work_dir`` and records the verdict in ``outcome.json``.

    Args:
        argv: Command-line arguments; exactly one, the job work folder.

    Returns:
        0 whenever a verdict was written, failed verdicts included.

    Raises:
        RuntimeError: If ``HF_HOME`` is not set in the environment.
        ValueError: If the produced result violates the result contract.
    """
    parser = argparse.ArgumentParser(prog="python -m stt.server.engine")
    parser.add_argument("work_dir", type=Path, help="job work folder holding spec.json")
    work_dir: Path = parser.parse_args(argv).work_dir

    spec = json.loads((work_dir / "spec.json").read_text(encoding="utf-8"))
    tmp_dir = work_dir / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    tempfile.tempdir = str(tmp_dir)

    reason = _start_check_failure(spec["diarize"])
    if reason is not None:
        _write_outcome(
            work_dir, {"status": "failed", "reason": reason, "detail": _DETAILS[reason]}
        )
        return 0
    transcript, result = _transcribe(spec)
    _write_results(work_dir, transcript, result)
    _write_outcome(work_dir, {"status": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
