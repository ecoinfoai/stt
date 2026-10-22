"""US2 worker side: nothing of a job outlives it, and a dead worker's traces are cleared."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from stt.server import contract, intake, lifecycle, worker
from stt.server.layout import Layout, hold
from stt.server.records import Failure, load_audio, load_job, save_job

from .conftest import (
    FAKE_ENGINE,
    FakeClock,
    leftovers,
    make_wav,
    new_id,
    run_sweep,
    run_worker,
    set_to_ctime_plus,
    write_delete_request,
)
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



def submit_delete(layout: Layout, clock: FakeClock, target: str, scope: str = "result", by: str = "professor-a") -> str:
    request_id = new_id()
    write_delete_request(layout.inbox, request_id=request_id, requested_by=by, target=target, scope=scope)
    intake.run_once(layout, clock.now)
    return request_id


def snapshot(layout: Layout, *skip: str) -> dict[Path, bytes]:
    """Reads every regular file under the state root except the inbox and the named outbox responses."""
    skipped = {layout.outbox / f"{name}.json" for name in skip}
    return {
        path: path.read_bytes()
        for path in layout.root.rglob("*")
        if path.is_file() and layout.inbox not in path.parents and path not in skipped
    }


def answer(layout: Layout, delete_id: str, target: str, scope: str, outcome: str) -> dict[str, Any]:
    doc = assert_valid_response(layout, delete_id)
    assert doc["kind"] == "delete"
    assert (doc["target"], doc["scope"], doc["outcome"]) == (target, scope, outcome)
    return doc


def done_job(layout: Layout, wav: Path, clock: FakeClock, **changes: Any) -> str:
    request_id = submit(layout, wav, clock, **changes)
    run_worker(layout, clock)
    clock.advance(60)
    return request_id


def test_us2_6_result_delete_keeps_audio_until_midnight(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock, delete_audio="at_midnight")
    assert (layout.outbox / request_id / "transcript.txt").is_file()

    delete_id = submit_delete(layout, clock, request_id)

    answer(layout, delete_id, request_id, "result", "deleted")
    assert not (layout.outbox / request_id).exists()
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "done"
    assert doc["result"]["deleted_at"] == "2026-03-02T01:01:00Z"
    assert "deleted_at" not in doc["audio"]
    assert (layout.audio / request_id / "audio.wav").is_file()
    assert load_job(layout, request_id).result_deleted_at == clock.now()
    assert list(layout.inbox.iterdir()) == []


def test_result_delete_retried_after_the_folder_went_completes(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock, delete_audio="at_midnight")
    shutil.rmtree(layout.outbox / request_id)

    delete_id = submit_delete(layout, clock, request_id)

    answer(layout, delete_id, request_id, "result", "deleted")
    assert load_job(layout, request_id).result_deleted_at == clock.now()
    assert assert_valid_response(layout, request_id)["result"]["deleted_at"] == "2026-03-02T01:01:00Z"
    assert list(layout.inbox.iterdir()) == []


def test_result_delete_twice_reports_already_deleted(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock, delete_audio="at_midnight")
    first = submit_delete(layout, clock, request_id)
    before = snapshot(layout, first)
    clock.advance(60)

    second = submit_delete(layout, clock, request_id)

    answer(layout, second, request_id, "result", "already_deleted")
    assert snapshot(layout, first, second) == before


def test_us2_8_delete_everything_on_stored_audio(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock, delete_audio="at_midnight", hints=["alpha"])
    before = assert_valid_response(layout, request_id)
    clock.advance(60)

    delete_id = submit_delete(layout, clock, request_id, "everything")

    answer(layout, delete_id, request_id, "everything", "deleted")
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "deleted"
    assert doc["deleted"] == {"at": "2026-03-02T01:02:00Z"}
    assert doc["audio"]["deleted_at"] == "2026-03-02T01:02:00Z"
    assert doc["result"]["deleted_at"] == "2026-03-02T01:02:00Z"
    assert doc["started_at"] == before["started_at"]
    assert doc["finished_at"] == before["finished_at"]
    assert "failure" not in doc
    assert not (layout.outbox / request_id).exists()
    assert load_audio(layout, request_id).deleted_by == "request"
    assert leftovers(layout.root) == []
    again = submit_delete(layout, clock, request_id)
    answer(layout, again, request_id, "result", "already_deleted")

    failed = submit(layout, wav, clock, delete_audio="at_midnight")
    run_worker(layout, clock, "fail=engine_error")
    submit_delete(layout, clock, failed, "everything")
    doc = assert_valid_response(layout, failed)
    assert doc["state"] == "deleted"
    assert "result" not in doc
    assert "failure" not in doc
    assert load_job(layout, failed).result_deleted_at is None
    later = submit_delete(layout, clock, failed)
    answer(layout, later, failed, "result", "no_result")


def pid_engine(tmp_path: Path) -> tuple[Path, list[str]]:
    """Returns a pid file and a ``sleep=30`` fake engine command that writes its own pid there.

    The launcher execs the fake engine, which keeps the pid; ``start_new_session`` makes it the process group id.
    """
    pid_file = tmp_path / "engine.pid"
    launcher = "import os, sys; open(sys.argv[1], 'w').write(str(os.getpid())); os.execv(sys.argv[2], sys.argv[2:])"
    return pid_file, [sys.executable, "-c", launcher, str(pid_file), sys.executable, str(FAKE_ENGINE), "--mode", "sleep=30"]


def test_us2_9_delete_everything_stops_running_job(layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    pid_file, engine_cmd = pid_engine(tmp_path)
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
    engine_group = int(pid_file.read_text(encoding="utf-8"))

    delete_id = submit_delete(layout, clock, request_id, "everything")
    deleted_at = time.monotonic()
    while time.monotonic() - deleted_at < 2 and (layout.work / request_id).exists():
        time.sleep(0.02)

    assert time.monotonic() - deleted_at < 2
    with pytest.raises(ProcessLookupError):
        os.killpg(engine_group, 0)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    answer(layout, delete_id, request_id, "everything", "deleted")
    assert not (layout.work / request_id).exists()
    assert not (layout.outbox / request_id).exists()
    assert not partial.exists()
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "deleted"
    assert leftovers(layout.root) == []


@pytest.mark.parametrize("scope", ["result", "everything"])
@pytest.mark.parametrize("case", ["other_requester", "unknown_target"])
def test_us2_10_other_requester_delete_refused(
    layout: Layout, wav: Path, clock: FakeClock, scope: str, case: str
) -> None:
    request_id = done_job(layout, wav, clock, delete_audio="at_midnight")
    target = request_id if case == "other_requester" else new_id()
    before = snapshot(layout)

    delete_id = submit_delete(layout, clock, target, scope, by="professor-b")

    doc = answer(layout, delete_id, target, scope, "refused")
    assert set(doc) == {
        "schema_version", "kind", "request_id", "target", "scope", "outcome", "processed_at", "expires_at"
    }
    assert snapshot(layout, delete_id) == before
    assert (layout.audio / request_id / "audio.wav").is_file()


def test_us2_11_second_delete_reports_already_deleted(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock, delete_audio="at_midnight")
    first = submit_delete(layout, clock, request_id, "everything")
    before = snapshot(layout, first)
    clock.advance(60)

    second = submit_delete(layout, clock, request_id, "everything")

    answer(layout, second, request_id, "everything", "already_deleted")
    assert snapshot(layout, first, second) == before


@pytest.mark.parametrize("state", ["queued", "running", "failed"])
def test_us2_12_result_delete_without_result_reports_no_result(
    layout: Layout, wav: Path, clock: FakeClock, state: str
) -> None:
    request_id = submit(layout, wav, clock, delete_audio="at_midnight")
    if state == "running":
        with hold(layout.state_lock):
            lifecycle.start(layout, load_job(layout, request_id), clock.now())
    if state == "failed":
        run_worker(layout, clock, "fail=engine_error")
    before = snapshot(layout)

    delete_id = submit_delete(layout, clock, request_id)

    answer(layout, delete_id, request_id, "result", "no_result")
    assert snapshot(layout, delete_id) == before
    assert load_job(layout, request_id).state == state


def test_delete_request_id_mismatch_refused(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = done_job(layout, wav, clock, delete_audio="at_midnight")
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
    request_id = done_job(layout, wav, clock, delete_audio="at_midnight")
    delete_id = submit_delete(layout, clock, request_id)
    clock.advance(60)
    write_delete_request(
        layout.inbox, request_id=delete_id, requested_by="professor-a", target=request_id, scope="everything"
    )

    intake.run_once(layout, clock.now)

    doc = answer(layout, delete_id, request_id, "result", "deleted")
    assert doc["duplicate_refused_at"] == "2026-03-02T01:02:00Z"
    assert load_job(layout, request_id).state == "done"
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


def start_worker_thread(
    layout: Layout, clock: FakeClock, engine_cmd: list[str], request_id: str
) -> tuple[threading.Thread, list[BaseException]]:
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


def test_us2_3_midnight_job_audio_gone_after_midnight(layout: Layout, wav: Path, clock: FakeClock) -> None:
    clock.set("2026-03-02T14:10:00Z")
    request_id = submit(layout, wav, clock, delete_audio="at_midnight")
    run_worker(layout, clock)

    clock.set("2026-03-02T14:59:00Z")
    run_sweep(layout, clock)

    assert (layout.audio / request_id / "audio.wav").is_file()
    assert "deleted_at" not in assert_valid_response(layout, request_id)["audio"]

    clock.set("2026-03-02T15:00:30Z")
    run_sweep(layout, clock)

    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "done"
    assert doc["audio"]["deleted_at"] == "2026-03-02T15:00:30Z"
    assert not (layout.audio / request_id).exists()
    assert load_audio(layout, request_id).deleted_by == "schedule"
    assert (layout.outbox / request_id / "transcript.txt").is_file()
    assert leftovers(layout.root) == []


def test_us2_4_dead_worker_traces_cleared_by_sweep(layout: Layout, wav: Path, clock: FakeClock) -> None:
    dead = submit(layout, wav, clock, hints=["alpha"])
    waiting = submit(layout, wav, clock)
    plant_dead_worker_traces(layout, clock, dead, published=True)
    clock.advance(60)

    run_sweep(layout, clock)

    doc = assert_valid_response(layout, dead)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "interrupted", "detail": INTERRUPTED_DETAIL}
    assert "deleted_at" in doc["audio"]
    assert not (layout.outbox / dead).exists()
    assert load_job(layout, dead).hints is None
    assert load_job(layout, waiting).state == "queued"
    assert [path for path in leftovers(layout.root) if dead in str(path)] == []
    assert (layout.audio / waiting / "audio.wav").is_file()


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


def test_us2_5_queued_midnight_job_fails_when_audio_deleted(layout: Layout, wav: Path, clock: FakeClock) -> None:
    clock.set("2026-03-02T14:50:00Z")
    request_id = submit(layout, wav, clock, delete_audio="at_midnight", hints=["alpha"])
    clock.set("2026-03-02T15:00:30Z")

    run_sweep(layout, clock)

    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "audio_deleted", "detail": AUDIO_DELETED_DETAIL, "cause": "midnight"}
    assert doc["audio"]["deleted_at"] == "2026-03-02T15:00:30Z"
    assert "result" not in doc
    assert not (layout.outbox / request_id).exists()
    assert leftovers(layout.root) == []


def test_us2_7_unfetched_result_gone_after_24h(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock)
    run_worker(layout, clock)
    accepted = clock.now()

    clock.set("2026-03-03T00:57:59Z")
    run_sweep(layout, clock)

    assert (clock.now() - accepted) == timedelta(hours=23, minutes=57, seconds=59)
    assert (layout.outbox / request_id / "transcript.txt").is_file()
    assert (layout.outbox / f"{request_id}.json").is_file()
    assert (layout.jobs / request_id / "job.json").is_file()
    assert (layout.audio / f"{request_id}.json").is_file()

    clock.set("2026-03-03T00:58:00Z")
    run_sweep(layout, clock)

    assert not (layout.outbox / request_id).exists()
    assert not (layout.outbox / f"{request_id}.json").exists()
    assert not (layout.jobs / request_id).exists()
    assert not (layout.audio / f"{request_id}.json").exists()
    assert leftovers(layout.root) == []


def scenario_done(layout: Layout, wav: Path, clock: FakeClock, policy: str) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"], delete_audio=policy)
    run_worker(layout, clock)
    return request_id


def scenario_failed(layout: Layout, wav: Path, clock: FakeClock, policy: str) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"], delete_audio=policy)
    run_worker(layout, clock, "fail=engine_error")
    return request_id


def scenario_engine_died(layout: Layout, wav: Path, clock: FakeClock, policy: str) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"], delete_audio=policy)
    run_worker(layout, clock, "die")
    return request_id


def scenario_worker_killed(layout: Layout, wav: Path, clock: FakeClock, policy: str) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"], delete_audio=policy)
    plant_dead_worker_traces(layout, clock, request_id, published=False)
    return request_id


def scenario_died_after_publish(layout: Layout, wav: Path, clock: FakeClock, policy: str) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"], delete_audio=policy)
    plant_dead_worker_traces(layout, clock, request_id, published=True)
    return request_id


def plant_crash_before_audio_removal(layout: Layout, clock: FakeClock, request_id: str, failure: Failure | None) -> None:
    """Leaves what a worker killed between saving the end state and removing the recording leaves."""
    with hold(layout.state_lock):
        started = lifecycle.start(layout, load_job(layout, request_id), clock.now())
        state = "done" if failure is None else "failed"
        save_job(layout, replace(started, state=state, finished_at=clock.now(), failure=failure, hints=None))
    if failure is None:
        (layout.outbox / request_id).mkdir()
        (layout.outbox / request_id / "transcript.txt").write_text("published before the worker died\n", encoding="utf-8")


def scenario_died_before_audio_removal(layout: Layout, wav: Path, clock: FakeClock, policy: str) -> str:
    request_id = submit(layout, wav, clock, hints=["alpha"], delete_audio=policy)
    plant_crash_before_audio_removal(layout, clock, request_id, None)
    return request_id


def scenario_intake_died_after_copy(layout: Layout, wav: Path, clock: FakeClock, policy: str) -> str:
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, delete_audio=policy))
    (layout.audio / request_id).mkdir(mode=0o700)
    shutil.copyfile(wav, layout.audio / request_id / "audio.wav")
    return request_id


@pytest.mark.parametrize("policy", ["after_job", "at_midnight"])
@pytest.mark.parametrize(
    "scenario",
    [
        scenario_done,
        scenario_failed,
        scenario_engine_died,
        scenario_worker_killed,
        scenario_died_after_publish,
        scenario_died_before_audio_removal,
        scenario_intake_died_after_copy,
    ],
    ids=[
        "done",
        "failed",
        "engine_died",
        "worker_killed",
        "died_after_publish",
        "died_before_audio_removal",
        "intake_died_after_copy",
    ],
)
def test_sc003_deletion_matrix(layout: Layout, wav: Path, clock: FakeClock, scenario: Any, policy: str) -> None:
    accepted = clock.now()
    request_id = scenario(layout, wav, clock, policy)
    orphan = scenario is scenario_intake_died_after_copy

    if orphan:
        set_to_ctime_plus(clock, layout.audio / request_id, 59)
        run_sweep(layout, clock)
        assert (layout.audio / request_id / "audio.wav").is_file()
        set_to_ctime_plus(clock, layout.audio / request_id, 61)
    elif policy == "at_midnight":
        run_sweep(layout, clock)
        clock.set("2026-03-02T15:00:30Z")
    run_sweep(layout, clock)

    assert leftovers(layout.root) == []
    if orphan:
        assert list(layout.inbox.iterdir()) == []
        set_to_ctime_plus(clock, layout.audio, 23 * 60 + 59)
    else:
        assert assert_valid_response(layout, request_id)["state"] in ("done", "failed")
        clock.set((accepted + timedelta(hours=23, minutes=59)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    run_sweep(layout, clock)

    assert leftovers(layout.root) == []
    for folder in (layout.jobs, layout.audio, layout.queue, layout.work, layout.inbox):
        assert list(folder.iterdir()) == []
    assert [path for path in layout.outbox.iterdir()] == []


@pytest.mark.parametrize("published", [False, True], ids=["worker_killed", "died_after_publish"])
def test_sc003_dead_worker_first_swept_after_the_scheduled_time(
    layout: Layout, wav: Path, clock: FakeClock, published: bool
) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"], delete_audio="at_midnight")
    plant_dead_worker_traces(layout, clock, request_id, published=published)
    clock.set("2026-03-02T15:00:30Z")

    run_sweep(layout, clock)

    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "interrupted", "detail": INTERRUPTED_DETAIL}
    assert doc["audio"]["deleted_at"] == "2026-03-02T15:00:30Z"
    assert "result" not in doc
    assert not (layout.outbox / request_id).exists()
    assert leftovers(layout.root) == []


@pytest.mark.parametrize(
    "failure", [None, Failure("engine_error", "The transcription engine failed.")], ids=["done", "failed"]
)
def test_sweep_removes_an_after_job_recording_whose_job_already_ended(
    layout: Layout, wav: Path, clock: FakeClock, failure: Failure | None
) -> None:
    request_id = submit(layout, wav, clock, hints=["alpha"])
    plant_crash_before_audio_removal(layout, clock, request_id, failure)
    clock.advance(60)

    run_sweep(layout, clock)

    assert not (layout.audio / request_id).exists()
    audio = load_audio(layout, request_id)
    assert audio.deleted_by == "after_job"
    assert audio.deleted_at == clock.now()
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == ("done" if failure is None else "failed")
    assert doc["audio"]["deleted_at"] == "2026-03-02T01:01:00Z"
    assert leftovers(layout.root) == []


def test_sweep_removes_an_after_job_recording_whose_job_record_is_gone(
    layout: Layout, wav: Path, clock: FakeClock
) -> None:
    request_id = submit(layout, wav, clock)
    plant_crash_before_audio_removal(layout, clock, request_id, None)
    shutil.rmtree(layout.jobs / request_id)

    run_sweep(layout, clock)

    assert not (layout.audio / request_id).exists()


def test_pick_raises_when_failing_a_due_job_leaves_it_queued(
    layout: Layout, wav: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_id = submit(layout, wav, clock, delete_audio="at_midnight")
    clock.set("2026-03-02T15:00:30Z")
    monkeypatch.setattr(lifecycle, "delete_audio", lambda *args, **kwargs: [])
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
