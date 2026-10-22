"""Lifecycle tests: start and finish keep markers, records and queued responses in step."""

from __future__ import annotations

from pathlib import Path

import pytest

from stt.server import contract, intake, lifecycle
from stt.server.layout import Layout, hold
from stt.server.records import Failure, load_job

from .conftest import FakeClock, make_wav, new_id
from .inbox_helpers import make_layout, put_attachment, put_body, read_response, transcribe_body


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


def accept_three(layout: Layout, tmp_path: Path, clock: FakeClock) -> list[str]:
    wav = make_wav(tmp_path / "source.wav", 1.0)
    ids = []
    for _ in range(3):
        request_id = new_id()
        put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
        put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id))
        intake.run_once(layout, clock.now)
        ids.append(request_id)
    return ids


def test_start_marks_running_removes_marker_and_moves_the_line_up(layout: Layout, tmp_path: Path) -> None:
    clock = FakeClock()
    first, second, third = accept_three(layout, tmp_path, clock)
    clock.advance(10)

    with hold(layout.state_lock):
        started = lifecycle.start(layout, load_job(layout, first), clock.now())

    assert started.state == "running"
    assert started.started_at == clock.now()
    assert load_job(layout, first) == started
    assert sorted(path.name for path in layout.queue.iterdir()) == [f"00000002-{second}", f"00000003-{third}"]
    running = read_response(layout, first)
    assert contract.validate_response(running) is None
    assert running["state"] == "running"
    assert "queue_position" not in running
    assert running["started_at"] == "2026-03-02T01:00:10Z"
    assert read_response(layout, second)["queue_position"] == 1
    assert read_response(layout, third)["queue_position"] == 2


def test_finish_done_records_end_state_and_updates_remaining_queue(layout: Layout, tmp_path: Path) -> None:
    clock = FakeClock()
    first, second, _ = accept_three(layout, tmp_path, clock)
    with hold(layout.state_lock):
        started = lifecycle.start(layout, load_job(layout, first), clock.now())
        clock.advance(30)
        finished = lifecycle.finish(layout, started, "done", clock.now())

    assert finished.state == "done"
    assert finished.finished_at == clock.now()
    assert finished.failure is None
    assert load_job(layout, first) == finished
    response = read_response(layout, first)
    assert contract.validate_response(response) is None
    assert response["state"] == "done"
    assert response["finished_at"] == "2026-03-02T01:00:30Z"
    assert read_response(layout, second)["queue_position"] == 1


def test_finish_failed_from_queued_removes_marker_and_reports_failure(layout: Layout, tmp_path: Path) -> None:
    clock = FakeClock()
    first, second, third = accept_three(layout, tmp_path, clock)
    failure = Failure(reason="engine_error", detail="The transcription engine failed.")
    with hold(layout.state_lock):
        finished = lifecycle.finish(layout, load_job(layout, second), "failed", clock.now(), failure)

    assert finished.failure == failure
    assert sorted(path.name for path in layout.queue.iterdir()) == [f"00000001-{first}", f"00000003-{third}"]
    response = read_response(layout, second)
    assert contract.validate_response(response) is None
    assert response["state"] == "failed"
    assert response["failure"] == {"reason": "engine_error", "detail": "The transcription engine failed."}
    assert read_response(layout, first)["queue_position"] == 1
    assert read_response(layout, third)["queue_position"] == 2
