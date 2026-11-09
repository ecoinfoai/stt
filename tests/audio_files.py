"""Tiny real audio files for tests that run the command line's own decoder."""

from __future__ import annotations

import wave
from pathlib import Path

RATE = 16000


def write_silent_wav(path: Path, seconds: float = 0.5) -> Path:
    """Writes a 16 kHz mono 16-bit WAV of silence.

    Args:
        path: Destination file; its extension need not be ``.wav``.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(bytes(2 * int(RATE * seconds)))
    return path
