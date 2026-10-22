"""Lifecycle tests: start and finish keep markers, records and queued responses in step."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from stt.server import contract, intake, lifecycle
from stt.server.layout import Layout, hold
from stt.server.records import Failure, load_audio, load_job, save_audio, save_job

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


def share_audio(layout: Layout, owner_id: str, other_id: str, policy: dict[str, str]) -> None:
    """Makes `other_id` use the recording of `owner_id` under `policy`."""
    audio = load_audio(layout, owner_id)
    save_audio(layout, replace(audio, users=(owner_id, other_id), policy=policy))
    save_job(layout, replace(load_job(layout, other_id), audio_id=owner_id))


def test_finish_removes_hints_and_keeps_audio_of_another_policy(layout: Layout, tmp_path: Path) -> None:
    clock = FakeClock()
    first, second, _ = accept_three(layout, tmp_path, clock)
    save_job(layout, replace(load_job(layout, first), hints=("alpha",)))
    share_audio(layout, first, second, {"after_job": second})

    with hold(layout.state_lock):
        lifecycle.finish(layout, load_job(layout, first), "done", clock.now())

    assert load_job(layout, first).hints is None
    assert (layout.audio / first / "audio.wav").is_file()
    assert load_audio(layout, first).deleted_at is None


def test_finish_of_the_policy_job_deletes_audio_and_refreshes_other_users(layout: Layout, tmp_path: Path) -> None:
    clock = FakeClock()
    first, second, _ = accept_three(layout, tmp_path, clock)
    share_audio(layout, first, second, {"after_job": second})
    with hold(layout.state_lock):
        for request_id in (first, second):
            lifecycle.start(layout, load_job(layout, request_id), clock.now())
        lifecycle.finish(layout, load_job(layout, first), "done", clock.now())
        clock.advance(30)
        lifecycle.finish(layout, load_job(layout, second), "done", clock.now())

    audio = load_audio(layout, first)
    assert not (layout.audio / first).exists()
    assert audio.deleted_by == "after_job"
    assert audio.deleted_at == clock.now()
    for request_id in (first, second):
        response = read_response(layout, request_id)
        assert contract.validate_response(response) is None
        assert response["audio"]["deleted_at"] == "2026-03-02T01:00:30Z"
        assert response["state"] == "done"


@pytest.mark.parametrize(
    ("cause", "deleted_by"),
    [("midnight", "schedule"), ("retention_limit", "schedule"), ("deleted_on_request", "request")],
)
def test_delete_audio_fails_waiting_users_and_keeps_the_record(
    layout: Layout, tmp_path: Path, cause: str, deleted_by: str
) -> None:
    clock = FakeClock()
    first, second, third = accept_three(layout, tmp_path, clock)
    share_audio(layout, first, second, {"after_job": second})
    with hold(layout.state_lock):
        lifecycle.finish(layout, load_job(layout, first), "done", clock.now())
        clock.advance(60)
        lifecycle.delete_audio(layout, first, cause, clock.now())

    audio = load_audio(layout, first)
    assert not (layout.audio / first).exists()
    assert audio.deleted_by == deleted_by
    assert audio.deleted_at == clock.now()
    assert load_job(layout, first).state == "done"
    failed = load_job(layout, second)
    assert failed.state == "failed"
    assert failed.failure == Failure("audio_deleted", "The recording was deleted before the job finished.", cause)
    assert load_job(layout, third).state == "queued"
    assert list(layout.queue.glob(f"*-{second}")) == []
    assert read_response(layout, first)["audio"]["deleted_at"] == "2026-03-02T01:01:00Z"
    response = read_response(layout, second)
    assert contract.validate_response(response) is None
    assert response["failure"]["cause"] == cause
    assert read_response(layout, third)["queue_position"] == 1


def test_clear_dead_worker_interrupts_running_jobs_only(layout: Layout, tmp_path: Path) -> None:
    clock = FakeClock()
    first, second, _ = accept_three(layout, tmp_path, clock)
    with hold(layout.state_lock):
        lifecycle.start(layout, load_job(layout, first), clock.now())
        lifecycle.clear_dead_worker(layout, clock.now())

    interrupted = load_job(layout, first)
    assert interrupted.state == "failed"
    assert interrupted.failure == Failure("interrupted", "The worker stopped before the job finished.")
    assert load_job(layout, second).state == "queued"
    assert (layout.audio / second / "audio.wav").is_file()
