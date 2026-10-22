"""Pinned model repositories and the offline revision check used by the engine start check."""

from __future__ import annotations

from pathlib import Path

PINNED: tuple[tuple[str, str], ...] = (
    ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478"),
    ("pyannote/speaker-diarization-3.1", "84fd25912480287da0247647c3d2b4853cb3ee5d"),
    ("pyannote/segmentation-3.0", "e66f3d3b9eb0873085418a7b813d3b369bf160bb"),
    ("pyannote/speaker-diarization-community-1", "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"),
    ("pyannote/wespeaker-voxceleb-resnet34-LM", "837717ddb9ff5507820346191109dc79c958d614"),
)


def ref_mismatches(hf_home: Path) -> list[str]:
    """Lists pinned repositories whose cached ``refs/main`` is missing or at another commit.

    Args:
        hf_home: The Hugging Face home directory holding ``hub/``.

    Returns:
        Repository names (``org/name``) in ``PINNED`` order; empty when all match.
    """
    mismatched: list[str] = []
    for repo, commit in PINNED:
        org, name = repo.split("/", 1)
        ref = hf_home / "hub" / f"models--{org}--{name}" / "refs" / "main"
        try:
            current = ref.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            mismatched.append(repo)
            continue
        if current != commit:
            mismatched.append(repo)
    return mismatched
