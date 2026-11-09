"""US4 of the decoder isolation: the contract and the deletion promise are unchanged by the isolation.

US4-1 (the packaged schemas equal the 001 contract files byte for byte) is checked by
``test_contract.py::test_packaged_schema_matches_contract_bytes`` and is not repeated here. US4-7 (the command
line behaves as before) is the unchanged ``tests/test_cli*.py``, ``tests/test_transcribe.py`` and
``tests/test_diarize.py``.

These tests drive intake, the worker and the sweep against the real reader supervisor behind the emulated reader
socket (``reader_socket``). They fail until intake and the worker read recordings through the reader. The worker
killed in US4-6 runs as a real process (``stt server work``), so it needs ``--reader-socket`` to reach the reader.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest

from stt.server import intake, worker
from stt.server.layout import Layout

from .conftest import (
    FAKE_ENGINE,
    FakeClock,
    ReaderSocket,
    encode_tone,
    leftovers,
    leftovers_besides_recordings,
    make_wav,
    new_id,
    run_sweep,
    run_worker,
    run_worker_once,
    write_delete_request,
)
from .inbox_helpers import make_layout, read_response
from .test_decoder_us2 import (
    GATED_CHILD,
    WAIT_S,
    decode_connections,
    pipe_identity,
    recorded_engine_cmd,
    recording_identity,
    submit,
    submit_second_pass,
)

AFTER_PURGE = "2026-03-02T14:58:30Z"
INTERRUPTED_DETAIL = "The worker stopped before the job finished."
HELD_MAX_WAIT_S = 60.0
LONG_POLL_S = 30.0

WORKER_SCRIPT = (
    "import sys\n"
    "from datetime import datetime\n"
    "import stt.server.cli as server_cli\n"
    "import stt.server.worker as server_worker\n"
    "from stt import cli\n"
    "moment = datetime.fromisoformat(sys.argv[1])\n"
    "server_cli._utc_now = lambda: moment\n"
    "server_worker.DEFAULT_ENGINE_CMD = (sys.executable, sys.argv[2], '--mode', 'done')\n"
    "sys.exit(cli.main(sys.argv[3:]))\n"
)


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def tone(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "tone.wav", 2.0, silent=False)


@pytest.fixture
def flac(tmp_path: Path) -> Path:
    """A recording whose own name is not ``audio.wav``, so any ``audio.wav`` found is a decoded one."""
    return encode_tone(tmp_path / "tone.flac", "flac", "flac")


class HeldDecode(NamedTuple):
    """A job whose decode the test double keeps open until released.

    Attributes:
        request_id: The job being decoded.
        worker: The thread running the worker.
        release: The file that lets the double go on; the test must not create it before the worker ended.
    """

    request_id: str
    worker: threading.Thread
    release: Path


def gated_command(started: Path, release: Path) -> list[str]:
    return [
        sys.executable,
        str(GATED_CHILD),
        "--started", str(started),
        "--release", str(release),
        "--max-wait-s", str(HELD_MAX_WAIT_S),
    ]


@contextmanager
def decode_held_open(
    layout: Layout, recording: Path, clock: FakeClock, reader_socket: ReaderSocket, tmp_path: Path
) -> Iterator[HeldDecode]:
    """Submits a job and runs one worker pass in a thread until its decode is in flight and held by the double.

    On exit the double is released and the worker thread joined, so a failing test never leaves either behind.
    """
    started, release = tmp_path / "decode.started", tmp_path / "decode.release"
    reader_socket.use_supervisor(gated_command(started, release))
    request_id = submit(layout, recording, clock)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            run_worker_once(layout, clock)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    try:
        deadline = time.monotonic() + WAIT_S
        while not started.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists(), "the worker never asked the reader to decode"
        yield HeldDecode(request_id, worker, release)
    finally:
        release.touch()
        worker.join(timeout=WAIT_S)
    assert not worker.is_alive()
    assert errors == []


def five_jobs_on_one_recording(layout: Layout, recording: Path, clock: FakeClock) -> list[str]:
    """Hands over the first pass, the automatic second pass and three re-transcriptions of one recording."""
    first = submit(layout, recording, clock)
    return [first, *(submit_second_pass(layout, clock, first) for _ in range(4))]


def test_decoder_us4_2_five_jobs_on_one_recording_are_all_done_and_each_is_decoded_on_its_own_connection(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    jobs = five_jobs_on_one_recording(layout, tone, clock)

    assert run_worker(layout, clock) == 0

    assert [read_response(layout, request_id)["state"] for request_id in jobs] == ["done"] * 5
    decodes = decode_connections(reader_socket)
    assert len(decodes) == 5
    shared = recording_identity(layout, jobs[0])
    assert [[fd for fd in fds if fd.kind == stat.S_IFREG] for fds in decodes] == [[shared]] * 5
    assert len({pipe_identity(fds) for fds in decodes}) == 5


def test_decoder_us4_3_delete_everything_after_five_jobs_leaves_no_file(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    jobs = five_jobs_on_one_recording(layout, tone, clock)
    run_worker(layout, clock)
    assert len(decode_connections(reader_socket)) == 5
    clock.advance(60)

    write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=jobs[0])
    intake.run_once(layout, clock.now)

    assert [read_response(layout, request_id)["state"] for request_id in jobs] == ["deleted"] * 5
    assert leftovers(layout.root) == []


def test_decoder_us4_4_delete_everything_while_a_decode_is_held_stops_the_reader_and_leaves_no_file(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    with decode_held_open(layout, tone, clock, reader_socket, tmp_path) as held:
        write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=held.request_id)
        intake.run_once(layout, clock.now)

        held.worker.join(timeout=WAIT_S)
        assert not held.worker.is_alive(), "the worker went on waiting for the decode of a deleted job"
        reader_socket.wait_for_exits(2)
        assert not held.release.exists()

    assert read_response(layout, held.request_id)["state"] == "deleted"
    assert f"{held.request_id} stopped" in capfd.readouterr().out.splitlines()
    assert leftovers(layout.root) == []


def test_decoder_us4_5_the_daily_purge_while_a_decode_is_held_stops_the_reader_and_leaves_no_file(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    with decode_held_open(layout, tone, clock, reader_socket, tmp_path) as held:
        clock.set(AFTER_PURGE)
        run_sweep(layout, clock)

        held.worker.join(timeout=WAIT_S)
        assert not held.worker.is_alive(), "the worker went on waiting for the decode of a purged job"
        reader_socket.wait_for_exits(2)
        assert not held.release.exists()

    failure = read_response(layout, held.request_id)["failure"]
    assert failure["reason"] == "audio_deleted"
    assert f"{held.request_id} stopped" in capfd.readouterr().out.splitlines()
    assert leftovers(layout.root) == []


def test_decoder_us4_4_a_delete_that_lands_before_the_first_job_check_ends_the_job_as_stopped(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    reader_socket.use_limits(poll_s=LONG_POLL_S)
    with decode_held_open(layout, tone, clock, reader_socket, tmp_path) as held:
        write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=held.request_id)
        intake.run_once(layout, clock.now)
        held.release.touch()
        held.worker.join(timeout=WAIT_S)
        assert not held.worker.is_alive()

    assert read_response(layout, held.request_id)["state"] == "deleted"
    assert f"{held.request_id} stopped" in capfd.readouterr().out.splitlines()
    assert leftovers(layout.root) == []


def test_decoder_us4_5_a_purge_that_lands_before_the_first_job_check_ends_the_job_as_stopped(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    reader_socket.use_limits(poll_s=LONG_POLL_S)
    with decode_held_open(layout, tone, clock, reader_socket, tmp_path) as held:
        clock.set(AFTER_PURGE)
        run_sweep(layout, clock)
        held.release.touch()
        held.worker.join(timeout=WAIT_S)
        assert not held.worker.is_alive()

    lines = capfd.readouterr().out.splitlines()
    assert f"{held.request_id} failed audio_deleted" in lines
    assert f"{held.request_id} stopped" in lines
    assert leftovers(layout.root) == []


def test_decoder_us4_4_a_delete_between_starting_the_job_and_opening_the_recording_ends_it_as_stopped(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    request_id = submit(layout, tone, clock)
    real_log = worker._log

    def delete_when_running(logged_id: str, state: str) -> None:
        real_log(logged_id, state)
        if state == "running":
            write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=request_id)
            intake.run_once(layout, clock.now)

    monkeypatch.setattr(worker, "_log", delete_when_running)

    assert run_worker_once(layout, clock) is True

    assert read_response(layout, request_id)["state"] == "deleted"
    assert f"{request_id} stopped" in capfd.readouterr().out.splitlines()
    assert leftovers(layout.root) == []


def test_decoder_us4_6_a_worker_killed_during_a_decode_is_cleared_as_interrupted_and_leaves_no_decoded_audio(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket, tmp_path: Path
) -> None:
    started, release = tmp_path / "decode.started", tmp_path / "decode.release"
    reader_socket.use_supervisor(gated_command(started, release))
    request_id = submit(layout, tone, clock)
    models, gpu_lock = tmp_path / "models", tmp_path / "gpu.lock"
    models.mkdir()
    gpu_lock.touch()
    worker = subprocess.Popen(
        [
            sys.executable, "-c", WORKER_SCRIPT, clock.now().isoformat(), str(FAKE_ENGINE),
            "server", "work",
            "--root", str(layout.root),
            "--models", str(models),
            "--gpu-lock", str(gpu_lock),
            "--reader-socket", str(reader_socket.path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + WAIT_S
        while not started.exists() and worker.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists(), "the worker never asked the reader to decode"
        assert read_response(layout, request_id)["state"] == "running"
        worker.kill()
        worker.wait(timeout=WAIT_S)
        reader_socket.wait_for_exits(2)
        assert not release.exists()
    finally:
        release.touch()
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=WAIT_S)

    clock.advance(60)
    run_sweep(layout, clock)

    response = read_response(layout, request_id)
    assert response["state"] == "failed"
    assert response["failure"] == {"reason": "interrupted", "detail": INTERRUPTED_DETAIL}
    assert list(layout.work.iterdir()) == []
    assert leftovers_besides_recordings(layout.root) == []


def test_decoder_us4_sc008_no_decoded_audio_is_left_after_a_job_that_is_done_or_failed(
    layout: Layout, flac: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    done = submit(layout, flac, clock)
    run_worker(layout, clock)
    failed = submit(layout, flac, clock)
    run_worker(layout, clock, "fail=engine_error")

    assert read_response(layout, done)["state"] == "done"
    assert read_response(layout, failed)["state"] == "failed"
    assert len(decode_connections(reader_socket)) == 2
    assert list(layout.root.rglob("audio.wav")) == []
    assert leftovers_besides_recordings(layout.root) == []


def test_decoder_edge_6_a_second_pass_on_the_same_recording_gets_its_own_decoded_audio_in_its_own_work_folder(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket, tmp_path: Path
) -> None:
    first = submit(layout, tone, clock)
    second = submit_second_pass(layout, clock, first)
    record = tmp_path / "engine.jsonl"

    run_worker(layout, clock, engine_cmd=recorded_engine_cmd(record))

    assert [read_response(layout, request_id)["state"] for request_id in (first, second)] == ["done", "done"]
    assert len(decode_connections(reader_socket)) == 2
    seen = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert [entry["spec"]["request_id"] for entry in seen] == [first, second]
    for entry in seen:
        assert entry["work_files"]["audio.wav"] == 0o600
        assert entry["wav"] == {"channels": 1, "sample_width": 2, "frame_rate": 16000}
    assert list(layout.work.iterdir()) == []
