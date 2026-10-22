"""Pinned model repositories, their download, and the offline revision check used by the engine start check."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

PINNED: tuple[tuple[str, str], ...] = (
    ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478"),
    ("pyannote/speaker-diarization-3.1", "84fd25912480287da0247647c3d2b4853cb3ee5d"),
    ("pyannote/segmentation-3.0", "e66f3d3b9eb0873085418a7b813d3b369bf160bb"),
    ("pyannote/speaker-diarization-community-1", "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"),
    ("pyannote/wespeaker-voxceleb-resnet34-LM", "837717ddb9ff5507820346191109dc79c958d614"),
)


class FetchError(RuntimeError):
    """A pinned repository could not be downloaded; the message names it and never carries the token."""


def _ref_path(models_dir: Path, repo: str) -> Path:
    org, name = repo.split("/", 1)
    return models_dir / "hub" / f"models--{org}--{name}" / "refs" / "main"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def fetch(models_dir: Path, token: str | None, download: Callable[..., object]) -> None:
    """Downloads every pinned repository at its pinned commit into ``<models_dir>/hub``.

    After each download ``refs/main`` is written to the pinned commit, because huggingface_hub
    does not write it when downloading by commit hash. When every repository is done,
    ``<models_dir>/.complete`` is written atomically with one ``<repo> <commit>`` line per
    ``PINNED`` entry. If ``.complete`` already holds exactly that list, nothing is downloaded;
    otherwise it is removed before downloading, so a failed run never leaves a list that no
    longer describes the rewritten ``refs/main`` files.

    Args:
        models_dir: The model folder (``HF_HOME``); created when missing.
        token: The Hugging Face access token, or None.
        download: A ``snapshot_download``-compatible callable taking the keywords ``repo_id``,
            ``revision``, ``cache_dir`` and ``token``.

    Raises:
        FetchError: A download failed. ``.complete`` is not written.
    """
    complete = models_dir / ".complete"
    expected = "".join(f"{repo} {commit}\n" for repo, commit in PINNED)
    try:
        if complete.read_text(encoding="utf-8") == expected:
            return
    except FileNotFoundError:
        pass
    complete.unlink(missing_ok=True)
    for repo, commit in PINNED:
        try:
            download(repo_id=repo, revision=commit, cache_dir=str(models_dir / "hub"), token=token)
        except Exception:
            raise FetchError(f"Failed to download model repository {repo}.") from None
        _write_atomic(_ref_path(models_dir, repo), commit)
    _write_atomic(complete, expected)


def ref_mismatches(hf_home: Path) -> list[str]:
    """Lists pinned repositories whose cached ``refs/main`` is missing or at another commit.

    Args:
        hf_home: The Hugging Face home directory holding ``hub/``.

    Returns:
        Repository names (``org/name``) in ``PINNED`` order; empty when all match.
    """
    mismatched: list[str] = []
    for repo, commit in PINNED:
        try:
            current = _ref_path(hf_home, repo).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            mismatched.append(repo)
            continue
        if current != commit:
            mismatched.append(repo)
    return mismatched
