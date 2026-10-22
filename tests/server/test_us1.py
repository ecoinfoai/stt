"""US1 end to end: intake, worker and publication with the fake engine."""

from __future__ import annotations

import json
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from stt.server import contract, intake, lifecycle
from stt.server.layout import Layout, hold
from stt.server.records import load_job

from .conftest import FAKE_ENGINE, FakeClock, make_wav, new_id, run_worker, run_worker_once
from .fake_engine import TRANSCRIPT_LINE
from .inbox_helpers import inbox_names, make_layout, put_attachment, put_body, read_response, transcribe_body

NO_VERDICT_DETAIL = "The engine stopped without a verdict."


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
    """Hands over one request and lets intake accept it."""
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, **changes))
    intake.run_once(layout, clock.now)
    return request_id


def assert_valid_response(layout: Layout, request_id: str) -> dict[str, Any]:
    doc = read_response(layout, request_id)
    assert contract.validate_response(doc) is None
    return doc


def fake_engine_cmd(mode: str, *extra: str) -> list[str]:
    return [sys.executable, str(FAKE_ENGINE), "--mode", mode, *extra]


def wait_for_state(layout: Layout, request_id: str, state: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        path = layout.outbox / f"{request_id}.json"
        if path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc["state"] == state:
                return doc
        time.sleep(0.02)
    raise AssertionError(f"job never reached {state}")


def wait_for_file(path: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError("file never appeared")


def run_in_thread(layout: Layout, clock: FakeClock, mode: str) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def target() -> None:
        try:
            run_worker(layout, clock, mode)
        except BaseException as error:  # surfaced by the caller
            errors.append(error)

    thread = threading.Thread(target=target)
    thread.start()
    return thread, errors


def test_us1_1_accepted_job_reports_queue_then_done(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock)

    queued = assert_valid_response(layout, request_id)
    assert queued["state"] == "queued"
    assert queued["queue_position"] == 1
    assert inbox_names(layout) == []
    assert (layout.audio / request_id / "audio.wav").is_file()
    assert len(list(layout.queue.iterdir())) == 1

    assert run_worker(layout, clock) == 0

    done = assert_valid_response(layout, request_id)
    assert done["state"] == "done"
    assert (layout.outbox / request_id / "transcript.txt").read_text(encoding="utf-8") == TRANSCRIPT_LINE + "\n"
    result = json.loads((layout.outbox / request_id / "result.json").read_text(encoding="utf-8"))
    assert contract.validate_result(result) is None
    assert result["request_id"] == request_id
    assert not (layout.work / request_id).exists()
    assert list(layout.queue.iterdir()) == []


def test_us1_7_third_job_gets_position_three_and_moves_up(layout: Layout, wav: Path, clock: FakeClock) -> None:
    first, second, third = (submit(layout, wav, clock) for _ in range(3))

    positions = [read_response(layout, rid)["queue_position"] for rid in (first, second, third)]
    assert positions == [1, 2, 3]

    assert run_worker_once(layout, clock) is True

    assert read_response(layout, first)["state"] == "done"
    assert read_response(layout, second)["queue_position"] == 1
    assert read_response(layout, third)["queue_position"] == 2
    assert_valid_response(layout, second)
    assert_valid_response(layout, third)


def test_worker_picks_the_lowest_seq_first(layout: Layout, wav: Path, clock: FakeClock) -> None:
    first, second = submit(layout, wav, clock), submit(layout, wav, clock)

    run_worker_once(layout, clock)

    assert load_job(layout, first).state == "done"
    assert load_job(layout, second).state == "queued"


def test_run_returns_zero_and_run_one_false_on_empty_queue(layout: Layout, clock: FakeClock) -> None:
    assert run_worker_once(layout, clock) is False
    assert run_worker(layout, clock) == 0


def test_run_works_off_every_queued_job(layout: Layout, wav: Path, clock: FakeClock) -> None:
    ids = [submit(layout, wav, clock) for _ in range(3)]

    assert run_worker(layout, clock) == 0

    assert [load_job(layout, rid).state for rid in ids] == ["done"] * 3
    assert list(layout.queue.iterdir()) == []


def test_worker_passes_engine_env(layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path) -> None:
    request_id = submit(layout, wav, clock, diarize=True, hints=["alpha", "beta"])
    record = tmp_path / "record.jsonl"
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    run_worker(layout, clock, engine_cmd=fake_engine_cmd("done", "--record", str(record)), models_dir=models_dir)

    lines = record.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    env = json.loads(lines[0])["env"]
    assert env == {
        "TMPDIR": str(layout.work / request_id),
        "HF_HOME": str(models_dir),
        "HF_HUB_OFFLINE": "1",
    }
    result = json.loads((layout.outbox / request_id / "result.json").read_text(encoding="utf-8"))
    assert result["request_id"] == request_id
    assert result["summary"]["diarized"] is True
    assert result["summary"]["hints"] == {"applied": 2, "total": 2, "dropped": []}


def test_running_job_has_spec_in_private_work_folder_and_no_marker(
    layout: Layout, wav: Path, clock: FakeClock
) -> None:
    request_id = submit(layout, wav, clock, language="en", hints=["alpha"])
    thread, errors = run_in_thread(layout, clock, "sleep=0.5")

    running = wait_for_state(layout, request_id, "running")
    work_dir = layout.work / request_id
    # The fake engine writes partial.txt only after reading the complete spec.json.
    wait_for_file(work_dir / "out" / "partial.txt")
    spec = json.loads((work_dir / "spec.json").read_text(encoding="utf-8"))
    mode = stat.S_IMODE(work_dir.stat().st_mode)
    markers = [path.name for path in layout.queue.iterdir()]
    thread.join()

    assert not errors
    assert running["state"] == "running"
    assert contract.validate_response(running) is None
    assert markers == []
    assert mode == 0o700
    assert spec == {
        "request_id": request_id,
        "audio_path": str(layout.audio / request_id / "audio.wav"),
        "original_name": f"{request_id}.audio.wav",
        "language": "en",
        "diarize": False,
        "hints": ["alpha"],
        "duration_s": pytest.approx(2.0, abs=0.05),
    }


def test_engine_crash_without_verdict_is_engine_error(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock)

    run_worker(layout, clock, "die")

    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "engine_error", "detail": NO_VERDICT_DETAIL}
    assert not (layout.outbox / request_id).exists()
    assert not (layout.work / request_id).exists()


@pytest.mark.parametrize("content", ["not json", "[]", '{"status": "maybe"}', '{"status": "failed", "reason": "x"}'])
def test_broken_verdict_is_engine_error(layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path, content: str) -> None:
    request_id = submit(layout, wav, clock)
    script = tmp_path / "broken_engine.py"
    script.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path(sys.argv[1], 'outcome.json').write_text({content!r})\n",
        encoding="utf-8",
    )

    run_worker(layout, clock, engine_cmd=[sys.executable, str(script)])

    failure = assert_valid_response(layout, request_id)["failure"]
    assert failure == {"reason": "engine_error", "detail": NO_VERDICT_DETAIL}
    assert not (layout.work / request_id).exists()


def test_done_verdict_without_result_files_is_engine_error(
    layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path
) -> None:
    request_id = submit(layout, wav, clock)
    script = tmp_path / "empty_engine.py"
    script.write_text(
        "import sys, pathlib\n"
        "pathlib.Path(sys.argv[1], 'outcome.json').write_text('{\"status\": \"done\"}')\n",
        encoding="utf-8",
    )

    run_worker(layout, clock, engine_cmd=[sys.executable, str(script)])

    failure = assert_valid_response(layout, request_id)["failure"]
    assert failure == {"reason": "engine_error", "detail": NO_VERDICT_DETAIL}
    assert not (layout.outbox / request_id).exists()
    assert not list(layout.outbox.glob(".*.tmp"))


def test_failed_verdict_keeps_reason_and_detail(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock)

    run_worker(layout, clock, "fail=gpu_unavailable")

    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "failed"
    assert doc["failure"] == {"reason": "gpu_unavailable", "detail": "Fake failure."}
    assert "result" not in doc
    assert not (layout.work / request_id).exists()
    assert list(layout.queue.iterdir()) == []


def test_failed_verdict_with_unknown_reason_is_engine_error(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock)

    run_worker(layout, clock, "fail=audio_deleted")

    assert read_response(layout, request_id)["failure"] == {"reason": "engine_error", "detail": NO_VERDICT_DETAIL}


def test_published_result_folder_and_file_modes(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock)

    run_worker(layout, clock)

    folder = layout.outbox / request_id
    # Only the permission bits are asserted: setgid sticks only when the test user belongs to the folder's group.
    assert stat.S_IMODE(folder.stat().st_mode) & 0o750 == 0o750
    assert stat.S_IMODE(folder.stat().st_mode) & 0o027 == 0
    assert sorted(path.name for path in folder.iterdir()) == ["result.json", "transcript.txt"]
    for path in folder.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not list(layout.outbox.glob(".*.tmp"))


def test_job_deleted_while_running_is_not_published(layout: Layout, wav: Path, clock: FakeClock) -> None:
    request_id = submit(layout, wav, clock)
    thread, errors = run_in_thread(layout, clock, "sleep=0.5")
    wait_for_state(layout, request_id, "running")

    with hold(layout.state_lock):
        lifecycle.finish(layout, load_job(layout, request_id), "deleted", clock.now())
    thread.join()

    assert not errors
    assert load_job(layout, request_id).state == "deleted"
    assert not (layout.outbox / request_id).exists()
    assert not (layout.work / request_id).exists()


def test_us1_8_every_response_and_result_validates(layout: Layout, wav: Path, clock: FakeClock) -> None:
    slow = submit(layout, wav, clock)
    assert read_response(layout, slow)["state"] == "queued"
    assert_valid_response(layout, slow)

    thread, errors = run_in_thread(layout, clock, "sleep=0.5")
    running = wait_for_state(layout, slow, "running")
    assert contract.validate_response(running) is None
    assert not any(slow in path.name for path in layout.queue.iterdir())
    thread.join()
    assert not errors

    assert assert_valid_response(layout, slow)["state"] == "done"
    result = json.loads((layout.outbox / slow / "result.json").read_text(encoding="utf-8"))
    assert contract.validate_result(result) is None

    broken = submit(layout, wav, clock, diarize=True, hints=["alpha"])
    run_worker(layout, clock, "fail=models_unavailable")
    assert assert_valid_response(layout, broken)["state"] == "failed"

    diarized = submit(layout, wav, clock, diarize=True, hints=["alpha"])
    run_worker(layout, clock)
    assert assert_valid_response(layout, diarized)["state"] == "done"
    result = json.loads((layout.outbox / diarized / "result.json").read_text(encoding="utf-8"))
    assert contract.validate_result(result) is None
    assert result["paragraphs"][0]["speaker"] is not None
