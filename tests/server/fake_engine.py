"""A fake transcription engine for the worker tests.

Usage: ``python fake_engine.py --mode MODE [--record FILE] <work_dir>``

Modes:
    done: write ``out/transcript.txt``, ``out/result.json`` and a ``done`` verdict.
    fail=<reason>: write a ``failed`` verdict with that reason.
    sleep=<seconds>: write ``out/partial.txt``, sleep, then behave like ``done``.
    die: exit with code 1 without any verdict.

With ``--record FILE`` it also appends one JSON line with its timing, environment, the spec it was given and what
it found in the work folder at start (file modes and the ``audio.wav`` header), for tests to inspect.

Whatever the mode, the engine prints the spec hints and its transcript line to
stdout and stderr, so a worker that lets engine output reach its own logs fails
the logging tests. Only the standard library is imported to keep start-up fast.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
import wave
from pathlib import Path
from typing import Any

TRANSCRIPT_LINE = "This is a fake transcript line."
_ENV_KEYS = ("TMPDIR", "HF_HOME", "HF_HUB_OFFLINE")


def _result(spec: dict[str, Any]) -> dict[str, Any]:
    hints = spec["hints"]
    return {
        "schema_version": 1,
        "request_id": spec["request_id"],
        "transcript": "transcript.txt",
        "paragraphs": [
            {
                "start": 0.0,
                "end": float(spec["duration_s"]),
                "speaker": "Speaker 1" if spec["diarize"] else None,
                "text": TRANSCRIPT_LINE,
                "uncertain": False,
            }
        ],
        "summary": {
            "model": "large-v3",
            "device": "cuda",
            "language": spec["language"],
            "audio_duration_s": float(spec["duration_s"]),
            "processing_s": 0.0,
            "diarized": spec["diarize"],
            "hints": None
            if hints is None
            else {"applied": len(hints), "total": len(hints), "dropped": []},
        },
    }


def _write_done(work_dir: Path, spec: dict[str, Any]) -> None:
    out = work_dir / "out"
    out.mkdir(exist_ok=True)
    (out / "transcript.txt").write_text(TRANSCRIPT_LINE + "\n", encoding="utf-8")
    (out / "result.json").write_text(json.dumps(_result(spec)), encoding="utf-8")
    (work_dir / "outcome.json").write_text(json.dumps({"status": "done"}), encoding="utf-8")


def _make_noise(spec: dict[str, Any]) -> None:
    for stream in (sys.stdout, sys.stderr):
        print(TRANSCRIPT_LINE, spec["hints"], file=stream, flush=True)


def _inspect_work_dir(work_dir: Path) -> dict[str, Any]:
    """Describes what the engine finds in its work folder: file modes and, for ``audio.wav``, its header and frame count."""
    files = {entry.name: stat.S_IMODE(entry.stat().st_mode) for entry in work_dir.iterdir()}
    header = None
    frames = None
    if (work_dir / "audio.wav").is_file():
        with wave.open(str(work_dir / "audio.wav"), "rb") as handle:
            header = {
                "channels": handle.getnchannels(),
                "sample_width": handle.getsampwidth(),
                "frame_rate": handle.getframerate(),
            }
            frames = handle.getnframes()
    return {"work_files": files, "wav": header, "wav_frames": frames}


def main(argv: list[str]) -> int:
    """Runs the fake engine; returns its exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--record", type=Path)
    parser.add_argument("work_dir", type=Path)
    args = parser.parse_args(argv)
    work_dir: Path = args.work_dir
    started = time.monotonic()
    spec = json.loads((work_dir / "spec.json").read_text(encoding="utf-8"))
    _make_noise(spec)
    inspected = _inspect_work_dir(work_dir) if args.record is not None else {}

    code = 0
    mode: str = args.mode
    if mode == "done":
        _write_done(work_dir, spec)
    elif mode.startswith("fail="):
        verdict = {"status": "failed", "reason": mode[len("fail="):], "detail": "Fake failure."}
        (work_dir / "outcome.json").write_text(json.dumps(verdict), encoding="utf-8")
    elif mode.startswith("sleep="):
        out = work_dir / "out"
        out.mkdir(exist_ok=True)
        (out / "partial.txt").write_text("partial\n", encoding="utf-8")
        time.sleep(float(mode[len("sleep="):]))
        _write_done(work_dir, spec)
    elif mode == "die":
        code = 1
    else:
        raise SystemExit(f"unknown mode: {mode}")

    if args.record is not None:
        line = {
            "start": started,
            "end": time.monotonic(),
            "env": {key: os.environ.get(key) for key in _ENV_KEYS},
            "spec": spec,
            **inspected,
        }
        with args.record.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
