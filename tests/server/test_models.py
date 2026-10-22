"""Pinned model list and revision check tests."""

from __future__ import annotations

from pathlib import Path

import pytest

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


SECRET_TOKEN = "hf_secret_token_value"


class FakeDownload:
    """Records every download call; can be told to fail for one repository."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_on = fail_on

    def __call__(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if kwargs["repo_id"] == self.fail_on:
            raise OSError(f"401 Unauthorized for token {SECRET_TOKEN}")
        return "snapshot"


def _expected_complete() -> str:
    return "".join(f"{repo} {commit}\n" for repo, commit in models.PINNED)


def test_fetch_downloads_each_pinned_commit_into_the_hub_cache(tmp_path: Path) -> None:
    download = FakeDownload()

    models.fetch(tmp_path, SECRET_TOKEN, download)

    assert download.calls == [
        {"repo_id": repo, "revision": commit, "cache_dir": str(tmp_path / "hub"), "token": SECRET_TOKEN}
        for repo, commit in models.PINNED
    ]


def test_fetch_writes_refs_main_so_the_offline_check_passes(tmp_path: Path) -> None:
    models.fetch(tmp_path, SECRET_TOKEN, FakeDownload())

    assert models.ref_mismatches(tmp_path) == []
    for repo, commit in models.PINNED:
        assert _ref(tmp_path, repo).read_text(encoding="utf-8") == commit


def test_fetch_writes_the_pinned_list_to_complete_after_all_downloads(tmp_path: Path) -> None:
    models.fetch(tmp_path, SECRET_TOKEN, FakeDownload())

    assert (tmp_path / ".complete").read_text(encoding="utf-8") == _expected_complete()
    assert [p.name for p in tmp_path.iterdir() if p.name != "hub"] == [".complete"]


def test_fetch_creates_the_models_folder_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "not" / "yet"

    models.fetch(target, None, FakeDownload())

    assert (target / ".complete").is_file()


def test_fetch_makes_no_download_call_when_complete_matches(tmp_path: Path) -> None:
    models.fetch(tmp_path, SECRET_TOKEN, FakeDownload())
    second = FakeDownload()

    models.fetch(tmp_path, SECRET_TOKEN, second)

    assert second.calls == []


def test_fetch_downloads_again_when_the_pinned_list_changed(tmp_path: Path) -> None:
    old_commit = "0" * 40
    (tmp_path / ".complete").write_text(
        "".join(f"{repo} {old_commit}\n" for repo, _ in models.PINNED), encoding="utf-8"
    )
    download = FakeDownload()

    models.fetch(tmp_path, SECRET_TOKEN, download)

    assert len(download.calls) == len(models.PINNED)
    assert (tmp_path / ".complete").read_text(encoding="utf-8") == _expected_complete()


def test_fetch_downloads_again_when_complete_lacks_a_repository(tmp_path: Path) -> None:
    (tmp_path / ".complete").write_text(_expected_complete().splitlines(keepends=True)[0], encoding="utf-8")
    download = FakeDownload()

    models.fetch(tmp_path, None, download)

    assert len(download.calls) == len(models.PINNED)


def test_fetch_failure_names_the_repository_and_hides_the_token(tmp_path: Path) -> None:
    failing = models.PINNED[2][0]

    with pytest.raises(models.FetchError) as raised:
        models.fetch(tmp_path, SECRET_TOKEN, FakeDownload(fail_on=failing))

    assert failing in str(raised.value)
    assert SECRET_TOKEN not in str(raised.value)
    assert SECRET_TOKEN not in repr(raised.value)
    assert str(raised.value).isascii()


def test_fetch_failure_writes_no_complete_file(tmp_path: Path) -> None:
    with pytest.raises(models.FetchError):
        models.fetch(tmp_path, SECRET_TOKEN, FakeDownload(fail_on=models.PINNED[-1][0]))

    assert not (tmp_path / ".complete").exists()


def test_fetch_failure_stops_before_later_repositories(tmp_path: Path) -> None:
    download = FakeDownload(fail_on=models.PINNED[1][0])

    with pytest.raises(models.FetchError):
        models.fetch(tmp_path, SECRET_TOKEN, download)

    assert [call["repo_id"] for call in download.calls] == [models.PINNED[0][0], models.PINNED[1][0]]
    assert not _ref(tmp_path, models.PINNED[1][0]).exists()


def test_fetch_retry_after_failure_downloads_again(tmp_path: Path) -> None:
    with pytest.raises(models.FetchError):
        models.fetch(tmp_path, SECRET_TOKEN, FakeDownload(fail_on=models.PINNED[3][0]))
    retry = FakeDownload()

    models.fetch(tmp_path, SECRET_TOKEN, retry)

    assert len(retry.calls) == len(models.PINNED)
    assert (tmp_path / ".complete").is_file()


def test_fetch_repairs_the_cache_after_a_failed_upgrade_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    list_a = models.PINNED
    list_b = (("Systran/faster-whisper-large-v3", "1" * 40),) + list_a[1:]
    models.fetch(tmp_path, None, FakeDownload())

    monkeypatch.setattr(models, "PINNED", list_b)
    with pytest.raises(models.FetchError):
        models.fetch(tmp_path, None, FakeDownload(fail_on=list_a[1][0]))

    monkeypatch.setattr(models, "PINNED", list_a)
    repair = FakeDownload()
    models.fetch(tmp_path, None, repair)

    assert len(repair.calls) == len(list_a)
    assert models.ref_mismatches(tmp_path) == []
