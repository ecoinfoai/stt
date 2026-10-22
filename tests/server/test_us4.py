"""US4: the server worker shares the GPU with the command line through one lock file."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

from stt import cli
from stt.server import contract, intake, worker
from stt.server.layout import Layout
from stt.server.records import load_job

from .conftest import FAKE_ENGINE, FakeClock, make_wav, new_id, run_worker, write_delete_request
from .inbox_helpers import make_layout, put_attachment, put_body, read_response, transcribe_body

POLL_S = 0.05

HOLDER_SCRIPT = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDONLY)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('ready', flush=True)\n"
    "sys.stdin.read()\n"
)

# Imitates the command line: takes the lock through stt.gpu_lock.acquire, then records
# the monotonic time at which it started and ended its GPU work.
CLI_SCRIPT = (
    "import json, sys, time\n"
    "from pathlib import Path\n"
    "from stt import gpu_lock\n"
    "lock, record, waiting, work_s = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4])\n"
    "with gpu_lock.acquire(lock, lambda: waiting.write_text('waiting')):\n"
    "    start = time.monotonic()\n"
    "    time.sleep(work_s)\n"
    "    end = time.monotonic()\n"
    "record.write_text(json.dumps({'start': start, 'end': end}))\n"
)


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "source.wav", 2.0)


@pytest.fixture
def lock_file(tmp_path: Path) -> Path:
    path = tmp_path / "gpu.lock"
    path.touch()
    return path


@pytest.fixture
def holder(lock_file: Path) -> Iterator[subprocess.Popen[str]]:
    """Another process that holds the GPU lock until its stdin is closed."""
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER_SCRIPT, str(lock_file)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    yield process
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            stream.close()


def release(process: subprocess.Popen[str]) -> None:
    assert process.stdin is not None
    process.stdin.close()
    process.wait(timeout=10)


def submit(layout: Layout, wav: Path, clock: FakeClock) -> str:
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id))
    intake.run_once(layout, clock.now)
    return request_id


def recording_engine(mode: str, record: Path) -> list[str]:
    return [sys.executable, str(FAKE_ENGINE), "--mode", mode, "--record", str(record)]


def read_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def start_cli_imitation(lock_file: Path, record: Path, waiting: Path, work_s: float) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", CLI_SCRIPT, str(lock_file), str(record), str(waiting), str(work_s)])


def wait_for(condition: Any, what: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def start_worker(
    layout: Layout, clock: FakeClock, lock_file: Path, engine_cmd: list[str], poll_s: float = POLL_S
) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def target() -> None:
        try:
            run_worker(layout, clock, engine_cmd=engine_cmd, gpu_lock=lock_file, poll_s=poll_s)
        except BaseException as error:  # surfaced by the caller
            errors.append(error)

    thread = threading.Thread(target=target)
    thread.start()
    return thread, errors


def join(thread: threading.Thread, errors: list[BaseException]) -> None:
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert not errors


def test_us4_2_server_job_waits_for_cli(
    layout: Layout, wav: Path, clock: FakeClock, lock_file: Path, holder: subprocess.Popen[str], tmp_path: Path
) -> None:
    request_id = submit(layout, wav, clock)
    record = tmp_path / "engine.jsonl"

    thread, errors = start_worker(layout, clock, lock_file, recording_engine("done", record))
    time.sleep(0.5)

    waiting = read_response(layout, request_id)
    assert waiting["state"] == "queued"
    assert waiting["queue_position"] == 1
    assert contract.validate_response(waiting) is None
    assert load_job(layout, request_id).state == "queued"
    assert not record.exists()
    assert thread.is_alive()

    release(holder)
    join(thread, errors)

    assert read_response(layout, request_id)["state"] == "done"
    assert len(read_records(record)) == 1


def test_us4_3_no_overlap_when_started_together(
    layout: Layout, wav: Path, clock: FakeClock, lock_file: Path, tmp_path: Path
) -> None:
    request_id = submit(layout, wav, clock)
    engine_record, cli_record = tmp_path / "engine.jsonl", tmp_path / "cli.json"

    cli_process = start_cli_imitation(lock_file, cli_record, tmp_path / "cli-waiting", 0.5)
    thread, errors = start_worker(layout, clock, lock_file, recording_engine("sleep=0.5", engine_record))
    join(thread, errors)
    assert cli_process.wait(timeout=15) == 0

    assert read_response(layout, request_id)["state"] == "done"
    engine = read_records(engine_record)[0]
    imitation = json.loads(cli_record.read_text(encoding="utf-8"))
    assert engine["end"] <= imitation["start"] or imitation["end"] <= engine["start"]


def test_delete_while_waiting_for_gpu(
    layout: Layout, wav: Path, clock: FakeClock, lock_file: Path, holder: subprocess.Popen[str], tmp_path: Path
) -> None:
    request_id = submit(layout, wav, clock)
    record = tmp_path / "engine.jsonl"
    thread, errors = start_worker(layout, clock, lock_file, recording_engine("done", record))
    time.sleep(0.2)

    write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=request_id)
    intake.run_once(layout, clock.now)
    join(thread, errors)

    assert holder.poll() is None
    assert load_job(layout, request_id).state == "deleted"
    assert read_response(layout, request_id)["state"] == "deleted"
    assert not record.exists()
    assert not (layout.work / request_id).exists()


def test_job_expiring_while_waiting_for_gpu_fails_without_starting_the_engine(
    layout: Layout, wav: Path, clock: FakeClock, lock_file: Path, holder: subprocess.Popen[str], tmp_path: Path
) -> None:
    request_id = submit(layout, wav, clock)
    record = tmp_path / "engine.jsonl"
    thread, errors = start_worker(layout, clock, lock_file, recording_engine("done", record))
    time.sleep(0.2)

    clock.advance(25 * 60 * 60)
    join(thread, errors)

    doc = read_response(layout, request_id)
    assert doc["state"] == "failed"
    assert doc["failure"]["reason"] == "audio_deleted"
    assert not record.exists()


def test_cli_gets_turn_between_server_jobs(
    layout: Layout, wav: Path, clock: FakeClock, lock_file: Path, tmp_path: Path
) -> None:
    first, second = submit(layout, wav, clock), submit(layout, wav, clock)
    engine_record, cli_record, waiting = tmp_path / "engine.jsonl", tmp_path / "cli.json", tmp_path / "cli-waiting"

    thread, errors = start_worker(layout, clock, lock_file, recording_engine("sleep=0.4", engine_record), poll_s=0.3)
    wait_for((layout.work / first / "out" / "partial.txt").exists, "the first job's engine")
    cli_process = start_cli_imitation(lock_file, cli_record, waiting, 0.2)
    wait_for(waiting.exists, "the command line to queue on the lock")
    join(thread, errors)
    assert cli_process.wait(timeout=15) == 0

    assert read_response(layout, first)["state"] == "done"
    assert read_response(layout, second)["state"] == "done"
    first_run, second_run = read_records(engine_record)
    imitation = json.loads(cli_record.read_text(encoding="utf-8"))
    assert first_run["end"] <= imitation["start"]
    assert imitation["end"] <= second_run["start"]


def test_worker_rests_poll_s_after_releasing_the_lock_before_the_next_job(
    layout: Layout, wav: Path, clock: FakeClock, lock_file: Path, tmp_path: Path
) -> None:
    submit(layout, wav, clock)
    submit(layout, wav, clock)
    record = tmp_path / "engine.jsonl"

    run_worker(layout, clock, engine_cmd=recording_engine("done", record), gpu_lock=lock_file, poll_s=0.5)

    first_run, second_run = read_records(record)
    assert second_run["start"] - first_run["end"] >= 0.5


def test_work_without_gpu_lock_option_exits_2(root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    models = tmp_path / "models"
    models.mkdir()

    assert cli.main(["server", "work", "--root", str(root), "--models", str(models)]) == 2

    assert "--gpu-lock" in capsys.readouterr().err


def test_work_exits_2_when_the_gpu_lock_file_is_missing(
    layout: Layout,
    wav: Path,
    clock: FakeClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_id = submit(layout, wav, clock)
    missing = tmp_path / "missing.lock"
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr("stt.server.cli._utc_now", clock.now)

    code = cli.main(["server", "work", "--root", str(layout.root), "--models", str(models), "--gpu-lock", str(missing)])

    assert code == 2
    assert capsys.readouterr().err == f"--gpu-lock points to a missing file: {missing}\n"
    assert load_job(layout, request_id).state == "queued"


def test_work_hands_the_gpu_lock_path_to_the_worker(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(worker, "run", fake_run)
    models = tmp_path / "models"
    models.mkdir()
    lock = tmp_path / "chosen.lock"

    assert cli.main(["server", "work", "--root", str(root), "--models", str(models), "--gpu-lock", str(lock)]) == 0

    assert seen["gpu_lock"] == lock
