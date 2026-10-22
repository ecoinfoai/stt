"""US2: a delete request and the daily purge leave nothing of a job, and a dead worker's traces are cleared."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from stt.server import contract, intake, lifecycle, worker
from stt.server.deadlines import parse_time
from stt.server.layout import Layout, hold
from stt.server.records import load_audio, load_job

from .conftest import (
    FAKE_ENGINE,
    FakeClock,
    leftovers,
    leftovers_besides_recordings,
    make_wav,
    new_id,
    run_sweep,
    run_worker,
    write_delete_request,
)
from .inbox_helpers import make_layout, put_attachment, put_body, read_response, transcribe_body

INTERRUPTED_DETAIL = "The worker stopped before the job finished."
AUDIO_DELETED_DETAIL = "The recording was deleted before the job finished."
PURGE = "2026-03-02T14:58:00Z"
AFTER_PURGE = "2026-03-02T14:58:30Z"
AFTER_MIDNIGHT = "2026-03-02T15:00:10Z"
NEXT_PURGE = "2026-03-03T14:58:00Z"


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


def submit_second(layout: Layout, clock: FakeClock, source: str, **changes: Any) -> str:
    request_id = new_id()
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, audio={"from_request": source}, **changes))
    intake.run_once(layout, clock.now)
    return request_id


def submit_delete(layout: Layout, clock: FakeClock, target: str, by: str = "professor-a") -> str:
    request_id = new_id()
    write_delete_request(layout.inbox, request_id=request_id, requested_by=by, target=target)
    intake.run_once(layout, clock.now)
    return request_id


def assert_valid_response(layout: Layout, request_id: str) -> dict[str, Any]:
    doc = read_response(layout, request_id)
    assert contract.validate_response(doc) is None
    return doc


def answer(layout: Layout, delete_id: str, target: str, outcome: str) -> dict[str, Any]:
    doc = assert_valid_response(layout, delete_id)
    assert doc["kind"] == "delete"
    assert (doc["target"], doc["outcome"]) == (target, outcome)
    return doc


def snapshot(layout: Layout, *skip: str) -> dict[Path, bytes]:
    """Reads every regular file under the state root except the inbox and the named outbox responses."""
    skipped = {layout.outbox / f"{name}.json" for name in skip}
    return {
        path: path.read_bytes()
        for path in layout.root.rglob("*")
        if path.is_file() and layout.inbox not in path.parents and path not in skipped
    }


def done_job(layout: Layout, wav: Path, clock: FakeClock, **changes: Any) -> str:
    request_id = submit(layout, wav, clock, **changes)
    run_worker(layout, clock)
    clock.advance(60)
    return request_id


def assert_state_root_empty(layout: Layout) -> None:
    for folder in (layout.jobs, layout.audio, layout.queue, layout.work, layout.inbox, layout.outbox):
        assert list(folder.iterdir()) == []


def pid_engine(tmp_path: Path) -> tuple[Path, list[str]]:
    """Returns a pid file and a ``sleep=30`` fake engine command that writes its own pid there.

    The launcher execs the fake engine, which keeps the pid; ``start_new_session`` makes it the process group id.
    """
    pid_file = tmp_path / "engine.pid"
    launcher = "import os, sys; open(sys.argv[1], 'w').write(str(os.getpid())); os.execv(sys.argv[2], sys.argv[2:])"
    return pid_file, [sys.executable, "-c", launcher, str(pid_file), sys.executable, str(FAKE_ENGINE), "--mode", "sleep=30"]


def start_worker_thread(
    layout: Layout, clock: FakeClock, engine_cmd: list[str], request_id: str
) -> tuple[threading.Thread, list[BaseException]]:
    """Starts the worker in a thread and returns once the engine of `request_id` has written its partial output."""
    errors: list[BaseException] = []

    def work() -> None:
        try:
            run_worker(layout, clock, engine_cmd=engine_cmd, poll_s=0.05)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    partial = layout.work / request_id / "out" / "partial.txt"
    started = time.monotonic()
    while not partial.exists() and time.monotonic() - started < 10:
        time.sleep(0.02)
    assert partial.exists()
    return thread, errors


def assert_group_gone(group: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise AssertionError("engine process group is still alive")


def plant_dead_worker_traces(layout: Layout, clock: FakeClock, request_id: str, published: bool) -> None:
    """Leaves what a killed worker leaves: a ``running`` job, a work folder with a WAV, maybe a published result."""
    with hold(layout.state_lock):
        lifecycle.start(layout, load_job(layout, request_id), clock.now())
    (layout.work / request_id / "out").mkdir(parents=True)
    make_wav(layout.work / request_id / "out" / "chunk.wav", 0.1)
    (layout.outbox / f".{request_id}.tmp").mkdir()
    (layout.outbox / f".{request_id}.tmp" / "result.json").write_text("{}", encoding="utf-8")
    if published:
        (layout.outbox / request_id).mkdir()
        (layout.outbox / request_id / "transcript.txt").write_text("published before the worker died\n", encoding="utf-8")


def test_us2_1_delete_request_on_a_done_job_leaves_nothing(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock, hints=["alpha"])
    before = assert_valid_response(layout, request_id)
    assert (layout.outbox / request_id / "transcript.txt").is_file()
    assert (layout.audio / request_id / "audio.wav").is_file()

    delete_id = submit_delete(layout, clock, request_id)

    answer(layout, delete_id, request_id, "deleted")
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "deleted"
    assert doc["deleted"] == {"at": "2026-03-02T01:01:00Z"}
    assert doc["audio"] == {"delete_at": PURGE, "deleted_at": "2026-03-02T01:01:00Z"}
    assert doc["result"]["deleted_at"] == "2026-03-02T01:01:00Z"
    assert doc["started_at"] == before["started_at"]
    assert doc["finished_at"] == before["finished_at"]
    assert "failure" not in doc
    assert not (layout.outbox / request_id).exists()
    assert not (layout.audio / request_id).exists()
    assert load_audio(layout, request_id).deleted_by == "request"
    assert load_job(layout, request_id).hints is None
    assert leftovers(layout.root) == []
    assert list(layout.inbox.iterdir()) == []


def test_delete_request_on_a_failed_job_leaves_out_the_result_entry(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    run_worker(layout, clock, "fail=engine_error")

    delete_id = submit_delete(layout, clock, request_id)

    answer(layout, delete_id, request_id, "deleted")
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "deleted"
    assert "result" not in doc
    assert "failure" not in doc
    assert load_job(layout, request_id).result_deleted_at is None
    assert not (layout.audio / request_id).exists()
    assert leftovers(layout.root) == []


def test_delete_request_completes_when_the_result_folder_is_already_gone(
    layout: Layout, wav: Path, clock: FakeClock
) -> None:
    request_id = done_job(layout, wav, clock)
    shutil.rmtree(layout.outbox / request_id)

    delete_id = submit_delete(layout, clock, request_id)

    answer(layout, delete_id, request_id, "deleted")
    assert assert_valid_response(layout, request_id)["state"] == "deleted"
    assert list(layout.inbox.iterdir()) == []


@pytest.mark.parametrize("target", ["first", "second"])
def test_us2_2_delete_request_on_either_pass_deletes_both(
    layout: Layout, wav: Path, clock: FakeClock, target: str
) -> None:
    first = done_job(layout, wav, clock)
    second = submit_second(layout, clock, first, hints=["alpha"], diarize=True)
    run_worker(layout, clock)
    clock.advance(60)
    for request_id in (first, second):
        assert (layout.outbox / request_id / "transcript.txt").is_file()

    delete_id = submit_delete(layout, clock, first if target == "first" else second)

    answer(layout, delete_id, first if target == "first" else second, "deleted")
    assert not (layout.audio / first).exists()
    assert load_audio(layout, first).deleted_by == "request"
    for request_id in (first, second):
        doc = assert_valid_response(layout, request_id)
        assert doc["state"] == "deleted"
        assert doc["deleted"] == {"at": "2026-03-02T01:02:00Z"}
        assert doc["audio"]["deleted_at"] == "2026-03-02T01:02:00Z"
        assert doc["result"]["deleted_at"] == "2026-03-02T01:02:00Z"
        assert not (layout.outbox / request_id).exists()
    assert leftovers(layout.root) == []


def test_us2_3_delete_request_stops_running_job(layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    pid_file, engine_cmd = pid_engine(tmp_path)
    thread, errors = start_worker_thread(layout, clock, engine_cmd, request_id)
    partial = layout.work / request_id / "out" / "partial.txt"
    engine_group = int(pid_file.read_text(encoding="utf-8"))

    delete_id = submit_delete(layout, clock, request_id)
    deleted_at = time.monotonic()
    while time.monotonic() - deleted_at < 2 and (layout.work / request_id).exists():
        time.sleep(0.02)

    assert time.monotonic() - deleted_at < 2
    assert_group_gone(engine_group)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    answer(layout, delete_id, request_id, "deleted")
    assert not (layout.work / request_id).exists()
    assert not (layout.outbox / request_id).exists()
    assert not partial.exists()
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "deleted"
    assert "result" not in doc
    assert leftovers(layout.root) == []


@pytest.mark.parametrize("running", ["first", "second"])
def test_delete_request_for_one_pass_stops_the_other_running_pass(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path, running: str
) -> None:
    first = submit(layout, wav, clock, hints=["alpha"])
    if running == "second":
        run_worker(layout, clock)
    second = submit_second(layout, clock, first, hints=["beta"])
    runner, target = (first, second) if running == "first" else (second, first)
    pid_file, engine_cmd = pid_engine(tmp_path)
    thread, errors = start_worker_thread(layout, clock, engine_cmd, runner)
    engine_group = int(pid_file.read_text(encoding="utf-8"))

    delete_id = submit_delete(layout, clock, target)

    assert_group_gone(engine_group)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    answer(layout, delete_id, target, "deleted")
    for request_id in (first, second):
        assert assert_valid_response(layout, request_id)["state"] == "deleted"
        assert not (layout.outbox / request_id).exists()
    assert not (layout.audio / first).exists()
    assert leftovers(layout.root) == []


def test_us2_4_delete_request_on_a_queued_job_never_runs_it(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path
) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    record = tmp_path / "record.jsonl"

    delete_id = submit_delete(layout, clock, request_id)
    run_worker(layout, clock, engine_cmd=[sys.executable, str(FAKE_ENGINE), "--mode", "done", "--record", str(record)])

    answer(layout, delete_id, request_id, "deleted")
    assert not record.exists()
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "deleted"
    for absent in ("result", "started_at", "queue_position"):
        assert absent not in doc
    assert doc["audio"]["deleted_at"] == "2026-03-02T01:00:00Z"
    assert not (layout.audio / request_id).exists()
    assert load_job(layout, request_id).hints is None
    assert leftovers(layout.root) == []


def test_us2_5_daily_purge_removes_done_failed_queued_and_running_jobs(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path
) -> None:
    clock.set("2026-03-02T14:50:00Z")
    done = submit(layout, wav, clock, hints=["alpha"])
    run_worker(layout, clock)
    failed = submit(layout, wav, clock, hints=["alpha"])
    run_worker(layout, clock, "fail=engine_error")
    running = submit(layout, wav, clock, hints=["alpha"])
    queued = submit(layout, wav, clock, hints=["alpha"])
    pid_file, engine_cmd = pid_engine(tmp_path)
    thread, errors = start_worker_thread(layout, clock, engine_cmd, running)
    assert assert_valid_response(layout, queued)["state"] == "queued"
    clock.set(AFTER_PURGE)

    run_sweep(layout, clock)

    assert_group_gone(int(pid_file.read_text(encoding="utf-8")))
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    for request_id in (done, failed, running, queued):
        assert not (layout.outbox / f"{request_id}.json").exists()
        assert not (layout.outbox / request_id).exists()
        assert not (layout.jobs / request_id).exists()
        assert not (layout.audio / request_id).exists()
    assert leftovers(layout.root) == []
    assert_state_root_empty(layout)


def test_done_job_keeps_result_and_recording_until_23_58(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock)
    run_worker(layout, clock)

    clock.set("2026-03-02T14:57:59Z")
    run_sweep(layout, clock)

    assert (layout.outbox / request_id / "transcript.txt").is_file()
    assert (layout.outbox / f"{request_id}.json").is_file()
    assert (layout.jobs / request_id / "job.json").is_file()
    assert (layout.audio / request_id / "audio.wav").is_file()
    assert "deleted_at" not in assert_valid_response(layout, request_id)["audio"]

    clock.set(PURGE)
    run_sweep(layout, clock)

    assert_state_root_empty(layout)


def test_us2_6_job_received_after_23_58_is_purged_by_the_next_sweep(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path
) -> None:
    clock.set(AFTER_PURGE)
    late = submit(layout, wav, clock, hints=["alpha"])
    doc = assert_valid_response(layout, late)
    assert (doc["expires_at"], doc["audio"]["delete_at"]) == (PURGE, PURGE)
    record = tmp_path / "record.jsonl"

    run_worker(layout, clock, engine_cmd=[sys.executable, str(FAKE_ENGINE), "--mode", "done", "--record", str(record)])
    clock.advance(15)
    run_sweep(layout, clock)

    assert not record.exists()
    assert leftovers(layout.root) == []
    assert_state_root_empty(layout)


def test_us2_6_job_received_after_midnight_stays_until_that_days_23_58(
    layout: Layout, wav: Path, clock: FakeClock
) -> None:
    clock.set(AFTER_MIDNIGHT)
    request_id = submit(layout, wav, clock, hints=["alpha"])
    run_worker(layout, clock)
    doc = assert_valid_response(layout, request_id)
    assert (doc["expires_at"], doc["audio"]["delete_at"]) == (NEXT_PURGE, NEXT_PURGE)

    for moment in ("2026-03-02T15:01:00Z", "2026-03-03T01:00:00Z", "2026-03-03T14:57:59Z"):
        clock.set(moment)
        run_sweep(layout, clock)
        assert assert_valid_response(layout, request_id)["state"] == "done"
        assert (layout.outbox / request_id / "transcript.txt").is_file()
        assert (layout.audio / request_id / "audio.wav").is_file()

    clock.set(NEXT_PURGE)
    run_sweep(layout, clock)

    assert parse_time(NEXT_PURGE) - parse_time(AFTER_MIDNIGHT) < timedelta(hours=24)
    assert_state_root_empty(layout)


def test_us2_7_dead_worker_traces_cleared_by_worker_start(layout: Layout, wav: Path, clock: FakeClock) -> None:
    dead = submit(layout, wav, clock, hints=["alpha"])
    waiting = submit(layout, wav, clock)
    plant_dead_worker_traces(layout, clock, dead, published=True)
    (layout.work / "stray").mkdir()
    clock.advance(60)

    run_worker(layout, clock)

    doc = assert_valid_response(layout, dead)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "interrupted", "detail": INTERRUPTED_DETAIL}
    assert not (layout.outbox / dead).exists()
    assert load_job(layout, dead).hints is None
    assert assert_valid_response(layout, waiting)["state"] == "done"
    assert leftovers_besides_recordings(layout.root) == []
    assert (layout.audio / dead / "audio.wav").is_file()


def test_us2_7_dead_worker_traces_cleared_by_sweep_and_recording_by_the_purge(
    layout: Layout, wav: Path, clock: FakeClock
) -> None:
    dead = submit(layout, wav, clock, hints=["alpha"])
    waiting = submit(layout, wav, clock)
    plant_dead_worker_traces(layout, clock, dead, published=True)
    clock.advance(60)

    run_sweep(layout, clock)

    doc = assert_valid_response(layout, dead)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "interrupted", "detail": INTERRUPTED_DETAIL}
    assert not (layout.outbox / dead).exists()
    assert load_job(layout, dead).hints is None
    assert load_job(layout, waiting).state == "queued"
    assert not (layout.work / dead).exists()
    assert not (layout.outbox / f".{dead}.tmp").exists()
    assert (layout.audio / dead / "audio.wav").is_file()

    clock.set(AFTER_PURGE)
    run_sweep(layout, clock)

    assert_state_root_empty(layout)


def test_sweep_leaves_a_running_job_alone_while_a_worker_holds_its_lock(
    layout: Layout, wav: Path, clock: FakeClock
) -> None:
    request_id = submit(layout, wav, clock)
    plant_dead_worker_traces(layout, clock, request_id, published=False)

    with hold(layout.worker_lock):
        run_sweep(layout, clock)

    assert load_job(layout, request_id).state == "running"
    assert (layout.work / request_id / "out" / "chunk.wav").is_file()
    assert (layout.outbox / f".{request_id}.tmp").is_dir()


@pytest.mark.parametrize("ended", ["done", "queued"])
def test_us2_8_daily_purge_runs_while_the_worker_is_stopped(
    layout: Layout, wav: Path, clock: FakeClock, ended: str
) -> None:
    request_id = done_job(layout, wav, clock) if ended == "done" else submit(layout, wav, clock, hints=["alpha"])
    clock.set(AFTER_PURGE)

    run_sweep(layout, clock)

    assert not (layout.jobs / request_id).exists()
    assert leftovers(layout.root) == []
    assert_state_root_empty(layout)


@pytest.mark.parametrize("case", ["other_requester", "unknown_target"])
def test_us2_9_other_requester_delete_refused(layout: Layout, wav: Path, clock: FakeClock, case: str) -> None:
    request_id = done_job(layout, wav, clock)
    target = request_id if case == "other_requester" else new_id()
    before = snapshot(layout)

    delete_id = submit_delete(layout, clock, target, by="professor-b")

    doc = answer(layout, delete_id, target, "refused")
    assert set(doc) == {"schema_version", "kind", "request_id", "target", "outcome", "processed_at", "expires_at"}
    assert snapshot(layout, delete_id) == before
    assert (layout.audio / request_id / "audio.wav").is_file()


def test_us2_10_second_delete_reports_already_deleted(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock)
    first = submit_delete(layout, clock, request_id)
    before = snapshot(layout, first)
    clock.advance(60)

    second = submit_delete(layout, clock, request_id)

    answer(layout, second, request_id, "already_deleted")
    assert snapshot(layout, first, second) == before


def test_delete_request_id_mismatch_refused(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock)
    before = snapshot(layout)
    file_id = new_id()
    path = write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=request_id)
    path.rename(layout.inbox / f"{file_id}.json")

    intake.run_once(layout, clock.now)

    doc = assert_valid_response(layout, file_id)
    assert doc["kind"] == "invalid"
    assert doc["failure"]["reason"] == "invalid_request"
    assert list(layout.inbox.iterdir()) == []
    assert snapshot(layout, file_id) == before
    assert (layout.outbox / request_id / "transcript.txt").is_file()


def test_duplicate_delete_request_marks_its_response(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock)
    delete_id = submit_delete(layout, clock, request_id)
    clock.advance(60)
    write_delete_request(layout.inbox, request_id=delete_id, requested_by="professor-a", target=request_id)

    intake.run_once(layout, clock.now)

    doc = answer(layout, delete_id, request_id, "deleted")
    assert doc["duplicate_refused_at"] == "2026-03-02T01:02:00Z"
    assert load_job(layout, request_id).state == "deleted"
    assert list(layout.inbox.iterdir()) == []


def test_delete_everything_marks_the_result_only_for_a_done_job(layout: Layout, wav: Path, clock: FakeClock) -> None:
    queued = submit(layout, wav, clock)
    running = submit(layout, wav, clock)
    with hold(layout.state_lock):
        lifecycle.start(layout, load_job(layout, running), clock.now())
        for request_id in (queued, running):
            lifecycle.delete_everything(layout, load_job(layout, request_id), clock.now())

    for request_id in (queued, running):
        job = load_job(layout, request_id)
        assert job.state == "deleted"
        assert job.deleted_at == clock.now()
        assert job.result_deleted_at is None
        assert "result" not in assert_valid_response(layout, request_id)


def test_engine_stops_when_the_job_record_is_purged_mid_run(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path
) -> None:
    request_id = submit(layout, wav, clock)
    pid_file, engine_cmd = pid_engine(tmp_path)
    thread, errors = start_worker_thread(layout, clock, engine_cmd, request_id)
    group = int(pid_file.read_text(encoding="utf-8"))

    shutil.rmtree(layout.jobs / request_id)

    assert_group_gone(group)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    assert not (layout.work / request_id).exists()
    assert not (layout.outbox / request_id).exists()


def test_engine_is_killed_when_the_poll_loop_raises(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_id = submit(layout, wav, clock)
    pid_file, engine_cmd = pid_engine(tmp_path)
    partial = layout.work / request_id / "out" / "partial.txt"

    def broken(layout: Layout, request_id: str) -> bool:
        if not partial.exists() or not pid_file.exists():
            return True
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "_is_running", broken)

    with pytest.raises(KeyboardInterrupt):
        run_worker(layout, clock, engine_cmd=engine_cmd, poll_s=0.05)

    assert_group_gone(int(pid_file.read_text(encoding="utf-8")))
    assert not (layout.work / request_id).exists()


def test_edge_8_queued_job_past_the_purge_is_not_started(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path
) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    record = tmp_path / "record.jsonl"
    clock.set(AFTER_PURGE)

    run_worker(layout, clock, engine_cmd=[sys.executable, str(FAKE_ENGINE), "--mode", "done", "--record", str(record)])

    assert not record.exists()
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "audio_deleted", "detail": AUDIO_DELETED_DETAIL}
    assert doc["audio"] == {"delete_at": PURGE, "deleted_at": AFTER_PURGE}
    assert "result" not in doc
    assert load_audio(layout, request_id).deleted_by == "purge"
    assert leftovers(layout.root) == []


def test_worker_after_a_purged_job_still_runs_the_next_one(layout: Layout, wav: Path, clock: FakeClock) -> None:
    clock.set("2026-03-02T14:57:00Z")
    purged = submit(layout, wav, clock)
    clock.set(AFTER_MIDNIGHT)
    fresh = submit(layout, wav, clock)

    run_worker(layout, clock)

    assert load_job(layout, purged).state == "failed"
    assert load_job(layout, fresh).state == "done"
    assert (layout.audio / fresh / "audio.wav").is_file()
    assert not (layout.audio / purged).exists()
    assert leftovers_besides_recordings(layout.root) == []


def test_pick_raises_when_failing_a_due_job_leaves_it_queued(
    layout: Layout, wav: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_id = submit(layout, wav, clock)
    clock.set(AFTER_PURGE)
    monkeypatch.setattr(lifecycle, "purge_audio", lambda *args, **kwargs: [])
    outcome: dict[str, Any] = {}

    def pick() -> None:
        try:
            with hold(layout.state_lock):
                outcome["job"] = worker._pick(layout, clock.now())
        except RuntimeError as error:
            outcome["error"] = error

    picker = threading.Thread(target=pick, daemon=True)
    picker.start()
    picker.join(5)

    assert not picker.is_alive()
    assert "job" not in outcome
    assert str(outcome["error"]) == f"{request_id}: job is still queued after its due recording was deleted"
    assert load_job(layout, request_id).state == "queued"


def scenario_done(layout: Layout, wav: Path, clock: FakeClock) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    run_worker(layout, clock)
    return request_id


def scenario_failed(layout: Layout, wav: Path, clock: FakeClock) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    run_worker(layout, clock, "fail=engine_error")
    return request_id


def scenario_engine_died(layout: Layout, wav: Path, clock: FakeClock) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    run_worker(layout, clock, "die")
    return request_id


def scenario_worker_killed(layout: Layout, wav: Path, clock: FakeClock) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    plant_dead_worker_traces(layout, clock, request_id, published=False)
    return request_id


def scenario_died_after_publish(layout: Layout, wav: Path, clock: FakeClock) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    plant_dead_worker_traces(layout, clock, request_id, published=True)
    return request_id


@pytest.mark.parametrize("path", ["delete_request", "daily_purge"])
@pytest.mark.parametrize(
    "scenario",
    [scenario_done, scenario_failed, scenario_engine_died, scenario_worker_killed, scenario_died_after_publish],
    ids=["done", "failed", "engine_died", "worker_killed", "died_after_publish"],
)
def test_sc003_deletion_matrix(layout: Layout, wav: Path, clock: FakeClock, scenario: Any, path: str) -> None:
    request_id = scenario(layout, wav, clock)
    clock.advance(30)
    if path == "delete_request":
        submit_delete(layout, clock, request_id)
        assert assert_valid_response(layout, request_id)["state"] == "deleted"
        assert leftovers(layout.root) == []
    else:
        clock.set(AFTER_PURGE)

    run_sweep(layout, clock)

    assert leftovers(layout.root) == []
    assert not (layout.outbox / request_id).exists()
    if path == "delete_request":
        assert assert_valid_response(layout, request_id)["state"] == "deleted"
        clock.set(AFTER_PURGE)
        run_sweep(layout, clock)
    assert_state_root_empty(layout)
