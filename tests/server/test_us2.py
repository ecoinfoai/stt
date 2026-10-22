"""US2 worker side: nothing of a job outlives it, and a dead worker's traces are cleared."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from stt.server import contract, intake, lifecycle
from stt.server.layout import Layout, hold
from stt.server.records import load_audio, load_job

from .conftest import FAKE_ENGINE, FakeClock, leftovers, make_wav, new_id, run_worker
from .inbox_helpers import make_layout, put_attachment, put_body, read_response, transcribe_body

INTERRUPTED_DETAIL = "The worker stopped before the job finished."
AUDIO_DELETED_DETAIL = "The recording was deleted before the job finished."


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "source.wav", 2.0)


def submit(layout: Layout, wav: Path, clock: FakeClock, **changes: Any) -> str:
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, **changes))
    intake.run_once(layout, clock.now)
    return request_id


def assert_valid_response(layout: Layout, request_id: str) -> dict[str, Any]:
    doc = read_response(layout, request_id)
    assert contract.validate_response(doc) is None
    return doc


def test_us2_1_after_job_done_leaves_nothing(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha", "beta"])

    run_worker(layout, clock)

    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "done"
    assert doc["audio"]["deleted_at"] == "2026-03-02T01:00:00Z"
    assert not (layout.audio / request_id).exists()
    assert (layout.audio / f"{request_id}.json").is_file()
    assert load_audio(layout, request_id).deleted_by == "after_job"
    assert load_job(layout, request_id).hints is None
    assert (layout.outbox / request_id / "transcript.txt").is_file()
    assert leftovers(layout.root) == []


def test_us2_2_after_job_failed_leaves_nothing(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"])

    run_worker(layout, clock, "fail=engine_error")

    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "failed"
    assert "deleted_at" in doc["audio"]
    assert not (layout.audio / request_id).exists()
    assert leftovers(layout.root) == []


def test_at_midnight_job_keeps_its_audio_after_the_job(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"], delete_audio="at_midnight")

    run_worker(layout, clock)

    doc = assert_valid_response(layout, request_id)
    assert "deleted_at" not in doc["audio"]
    assert (layout.audio / request_id / "audio.wav").is_file()
    assert load_job(layout, request_id).hints is None


def test_us2_4_dead_worker_traces_cleared_by_worker_start(layout: Layout, wav: Path, clock: FakeClock) -> None:
    dead = submit(layout, wav, clock, hints=["alpha"])
    waiting = submit(layout, wav, clock)
    with hold(layout.state_lock):
        lifecycle.start(layout, load_job(layout, dead), clock.now())
    (layout.work / dead / "out").mkdir(parents=True)
    make_wav(layout.work / dead / "out" / "chunk.wav", 0.1)
    (layout.outbox / dead).mkdir()
    (layout.outbox / dead / "transcript.txt").write_text("published before the worker died\n", encoding="utf-8")
    (layout.outbox / f".{dead}.tmp").mkdir()
    (layout.outbox / f".{dead}.tmp" / "result.json").write_text("{}", encoding="utf-8")
    (layout.work / "stray").mkdir()
    clock.advance(60)

    run_worker(layout, clock)

    doc = assert_valid_response(layout, dead)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "interrupted", "detail": INTERRUPTED_DETAIL}
    assert not (layout.outbox / dead).exists()
    assert load_job(layout, dead).hints is None
    assert "deleted_at" in doc["audio"]
    assert assert_valid_response(layout, waiting)["state"] == "done"
    assert leftovers(layout.root) == []


@pytest.mark.parametrize(
    ("changes", "advance_to", "cause"),
    [
        ({"delete_audio": "at_midnight"}, "2026-03-02T15:00:30Z", "midnight"),
        ({"delete_audio": "after_job"}, "2026-03-03T00:58:30Z", "retention_limit"),
    ],
    ids=["policy_time", "hard_deadline"],
)
def test_edge_8_queued_job_past_deadline_not_started(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path, changes: dict[str, str], advance_to: str, cause: str
) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"], **changes)
    record = tmp_path / "record.jsonl"
    clock.set(advance_to)

    run_worker(layout, clock, engine_cmd=[sys.executable, str(FAKE_ENGINE), "--mode", "done", "--record", str(record)])

    assert not record.exists()
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "audio_deleted", "detail": AUDIO_DELETED_DETAIL, "cause": cause}
    assert doc["audio"]["deleted_at"] == advance_to
    assert "result" not in doc
    assert load_audio(layout, request_id).deleted_by == "schedule"
    assert leftovers(layout.root) == []


def test_worker_after_expired_job_still_runs_the_next_one(layout: Layout, wav: Path, clock: FakeClock) -> None:
    expired = submit(layout, wav, clock, delete_audio="at_midnight")
    clock.set("2026-03-02T14:59:00Z")
    fresh = submit(layout, wav, clock, delete_audio="after_job")
    clock.set("2026-03-02T15:00:30Z")

    run_worker(layout, clock)

    assert load_job(layout, expired).state == "failed"
    assert load_job(layout, fresh).state == "done"
    assert leftovers(layout.root) == []
