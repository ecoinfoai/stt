"""Pinned model list and revision check tests."""

from __future__ import annotations

from pathlib import Path

from stt.server import models

EXPECTED_PINNED = (
    ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478"),
    ("pyannote/speaker-diarization-3.1", "84fd25912480287da0247647c3d2b4853cb3ee5d"),
    ("pyannote/segmentation-3.0", "e66f3d3b9eb0873085418a7b813d3b369bf160bb"),
    ("pyannote/speaker-diarization-community-1", "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"),
    ("pyannote/wespeaker-voxceleb-resnet34-LM", "837717ddb9ff5507820346191109dc79c958d614"),
)


def _ref(hf_home: Path, repo: str) -> Path:
    org, name = repo.split("/", 1)
    return hf_home / "hub" / f"models--{org}--{name}" / "refs" / "main"


def test_pinned_matches_research_table() -> None:
    assert models.PINNED == EXPECTED_PINNED


def test_all_refs_match_returns_empty(pinned_models: Path) -> None:
    assert models.ref_mismatches(pinned_models) == []


def test_trailing_newline_in_ref_is_ignored(pinned_models: Path) -> None:
    repo, commit = models.PINNED[0]
    _ref(pinned_models, repo).write_text(commit + "\n", encoding="utf-8")

    assert models.ref_mismatches(pinned_models) == []


def test_missing_repository_is_reported(pinned_models: Path) -> None:
    repo = models.PINNED[2][0]
    _ref(pinned_models, repo).unlink()

    assert models.ref_mismatches(pinned_models) == [repo]


def test_other_commit_is_reported(pinned_models: Path) -> None:
    repo = models.PINNED[1][0]
    _ref(pinned_models, repo).write_text("0" * 40, encoding="utf-8")

    assert models.ref_mismatches(pinned_models) == [repo]


def test_missing_and_other_commit_reported_in_pinned_order(pinned_models: Path) -> None:
    last, first = models.PINNED[4][0], models.PINNED[0][0]
    _ref(pinned_models, last).unlink()
    _ref(pinned_models, first).write_text("deadbeef", encoding="utf-8")

    assert models.ref_mismatches(pinned_models) == [first, last]


def test_empty_hf_home_reports_every_repository(tmp_path: Path) -> None:
    assert models.ref_mismatches(tmp_path) == [repo for repo, _ in EXPECTED_PINNED]
