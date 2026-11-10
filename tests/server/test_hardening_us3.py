"""US3 of the reader hardening: an ``engine_error`` line names its cause, and no read-stage failure stops the worker.

The worker logs ``<request_id> failed engine_error <cause>`` with one of the five names of
``worker.ENGINE_ERROR_CAUSES``. A recording that cannot be opened as a regular file (missing, a directory, a FIFO,
no permission) fails only its own job as ``recording_unusable`` without touching the reader, and the worker goes on
with the next job. Reader failures come from the emulated reader socket and its doubles, never from a crafted file.
"""

from __future__ import annotations

import errno
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

import pytest

from stt.server import intake, reader_client, worker
from stt.server.layout import Layout
from stt.server.records import load_job

from .conftest import FakeClock, ReaderSocket, make_wav, run_worker, run_worker_once
from .inbox_helpers import make_layout, read_response
from .test_decoder_us2 import submit
from .test_decoder_us3 import (
    NO_VERDICT,
    NOT_STARTED,
    UNAVAILABLE_READERS,
    Failure,
    assert_failed,
    assert_no_result,
    make_reader_unavailable,
    put_request,
)

T = TypeVar("T")

WORKER_WAIT_S = 5.0
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ENGINE_ERROR_LINE = re.compile(
    rf"^{UUID} failed engine_error (reader_unavailable|recording_unusable|unexpected|engine_failed|engine_no_verdict)$"
)
ENGINE_FAILED = Failure("engine_error", "Fake failure.")


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
def silence(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "silence.wav", 1.0)


def recording_of(layout: Layout, request_id: str) -> Path:
    """Returns the path of the stored recording of the job `request_id`."""
    (path,) = (layout.audio / load_job(layout, request_id).audio_id).glob("audio.*")
    return path


def spoil_recording(path: Path, how: str) -> None:
    """Replaces the stored recording at `path` the way `how` says."""
    if how == "permission":
        path.chmod(0)
        return
    path.unlink()
    if how == "directory":
        path.mkdir()
    elif how == "fifo":
        os.mkfifo(path)


def run_bounded(call: Callable[[], T], fifo: Path | None = None) -> T:
    """Runs `call` in a thread and fails the test when it has not returned after ``WORKER_WAIT_S``.

    The thread's own exception is raised here. If the thread is stuck opening the FIFO `fifo` for reading,
    opening its write end without blocking releases it, so a failing run ends instead of hanging.
    """
    outcome: list[T] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            outcome.append(call())
        except Exception as error:  # handed to the test thread below
            errors.append(error)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    try:
        thread.join(timeout=WORKER_WAIT_S)
        finished = not thread.is_alive()
    finally:
        if fifo is not None:
            try:
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            except OSError as error:
                assert error.errno == errno.ENXIO  # nobody is blocked on the FIFO
            thread.join(timeout=WORKER_WAIT_S)
    assert finished, f"the worker did not finish within {WORKER_WAIT_S} s"
    if errors:
        raise errors[0]
    return outcome[0]


def worker_lines(capfd: pytest.CaptureFixture[str]) -> list[str]:
    return capfd.readouterr().out.splitlines()


# ---------------------------------------------------------------- US3-1


@pytest.mark.parametrize("how", UNAVAILABLE_READERS)
def test_hardening_us3_1_an_unavailable_reader_names_reader_unavailable(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    capfd: pytest.CaptureFixture[str],
    how: str,
) -> None:
    request_id = submit(layout, tone, clock)
    capfd.readouterr()
    make_reader_unavailable(reader_socket, how)

    assert run_worker_once(layout, clock) is True

    assert_failed(layout, request_id, NOT_STARTED)
    assert_no_result(layout, request_id)
    assert worker_lines(capfd) == [f"{request_id} running", f"{request_id} failed engine_error reader_unavailable"]


# ---------------------------------------------------------------- US3-2

UNUSABLE_RECORDINGS = ["missing", "directory", "fifo", "permission"]


def skip_permission_case_as_root(how: str) -> None:
    if how == "permission" and os.geteuid() == 0:
        pytest.skip("root opens a file whose mode is 000, so the permission case cannot fail")


@pytest.mark.parametrize("how", UNUSABLE_RECORDINGS)
def test_hardening_us3_2_a_recording_that_cannot_be_opened_names_recording_unusable_and_starts_no_reader(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    capfd: pytest.CaptureFixture[str],
    how: str,
) -> None:
    skip_permission_case_as_root(how)
    request_id = submit(layout, tone, clock)
    path = recording_of(layout, request_id)
    spawned_before = reader_socket.spawn_count
    capfd.readouterr()
    spoil_recording(path, how)

    assert run_bounded(lambda: run_worker_once(layout, clock), path if how == "fifo" else None) is True

    assert reader_socket.spawn_count == spawned_before
    assert_failed(layout, request_id, NOT_STARTED)
    assert_no_result(layout, request_id)
    assert worker_lines(capfd) == [f"{request_id} running", f"{request_id} failed engine_error recording_unusable"]


# ---------------------------------------------------------------- US3-3


@pytest.mark.parametrize("how", UNUSABLE_RECORDINGS)
def test_hardening_us3_3_the_worker_goes_on_with_the_next_job_after_an_unusable_recording(
    layout: Layout,
    tone: Path,
    silence: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    capfd: pytest.CaptureFixture[str],
    how: str,
) -> None:
    skip_permission_case_as_root(how)
    bad, good = submit(layout, tone, clock), submit(layout, silence, clock)
    path = recording_of(layout, bad)
    capfd.readouterr()
    spoil_recording(path, how)

    exit_code = run_bounded(lambda: run_worker(layout, clock), path if how == "fifo" else None)

    assert exit_code == 0
    assert_failed(layout, bad, NOT_STARTED)
    assert_no_result(layout, bad)
    assert read_response(layout, good)["state"] == "done"
    assert (layout.outbox / good / "result.json").is_file()
    captured = capfd.readouterr()
    assert "Traceback" not in captured.out + captured.err
    assert captured.out.splitlines() == [
        f"{bad} running",
        f"{bad} failed engine_error recording_unusable",
        f"{good} running",
        f"{good} done",
    ]


# ---------------------------------------------------------------- US3-4


def refuse_pipes(*args: Any, **kwargs: Any) -> tuple[int, int]:
    raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))


def refuse_with_value_error(*args: Any, **kwargs: Any) -> Any:
    raise ValueError("an unexpected value")


@pytest.mark.parametrize(
    ("arrange", "mode", "expected", "cause"),
    [
        ("pipe2_emfile", "done", NOT_STARTED, "unexpected"),
        ("value_error", "done", NOT_STARTED, "unexpected"),
        ("none", "fail=engine_error", ENGINE_FAILED, "engine_failed"),
        ("none", "fail=unreadable_audio", NO_VERDICT, "engine_no_verdict"),
        ("none", "die", NO_VERDICT, "engine_no_verdict"),
    ],
    ids=["pipe2-emfile", "value-error", "engine-verdict", "engine-reason-outside-the-list", "engine-died"],
)
def test_hardening_us3_4_each_cause_is_named_without_paths_names_errno_or_messages(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    arrange: str,
    mode: str,
    expected: Failure,
    cause: str,
) -> None:
    request_id = submit(layout, tone, clock)
    capfd.readouterr()
    if arrange == "pipe2_emfile":
        monkeypatch.setattr(os, "pipe2", refuse_pipes)
    elif arrange == "value_error":
        monkeypatch.setattr(reader_client, "decode", refuse_with_value_error)

    run_worker(layout, clock, mode)

    assert_failed(layout, request_id, expected)
    assert_no_result(layout, request_id)
    lines = worker_lines(capfd)
    assert lines == [f"{request_id} running", f"{request_id} failed engine_error {cause}"]
    assert ENGINE_ERROR_LINE.fullmatch(lines[1])
    assert cause in worker.ENGINE_ERROR_CAUSES


def test_hardening_us3_4_the_cause_names_are_the_five_of_the_contract() -> None:
    assert worker.ENGINE_ERROR_CAUSES == frozenset(
        {"reader_unavailable", "recording_unusable", "unexpected", "engine_failed", "engine_no_verdict"}
    )


def test_hardening_us3_4_other_failure_reasons_keep_their_lines_without_a_cause(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket, capfd: pytest.CaptureFixture[str]
) -> None:
    request_id = submit(layout, tone, clock)
    capfd.readouterr()

    run_worker(layout, clock, "fail=gpu_unavailable")

    assert worker_lines(capfd) == [f"{request_id} running", f"{request_id} failed gpu_unavailable"]


def test_hardening_us3_4_intake_logs_a_recording_that_is_not_a_regular_file_as_a_request_exception(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    def copy_as_a_directory(source: Any, folder: Path, extension: str) -> Path:
        folder.mkdir(mode=0o700)
        target = folder / f"audio.{extension}"
        target.mkdir()
        return target

    monkeypatch.setattr(intake, "_copy_attachment", copy_as_a_directory)
    request_id = put_request(layout, tone, 1_000.0)
    spawned_before = reader_socket.spawn_count

    assert intake.run_once(layout, clock.now) == 1

    assert capfd.readouterr().out.splitlines() == [f"{request_id} intake error RecordingError"]
    assert reader_socket.spawn_count == spawned_before
    assert not (layout.audio / request_id).exists()


# ---------------------------------------------------------------- edge case 6


def test_hardening_edge_6_an_os_error_in_the_read_stage_fails_only_that_job_as_unexpected(
    layout: Layout,
    tone: Path,
    silence: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    bad, good = submit(layout, tone, clock), submit(layout, silence, clock)
    capfd.readouterr()
    with monkeypatch.context() as patched:
        patched.setattr(os, "pipe2", refuse_pipes)
        assert run_worker_once(layout, clock) is True

    assert worker_lines(capfd) == [f"{bad} running", f"{bad} failed engine_error unexpected"]
    assert_failed(layout, bad, NOT_STARTED)
    assert_no_result(layout, bad, waiting=good)
    assert run_worker_once(layout, clock) is True
    assert read_response(layout, good)["state"] == "done"


def test_hardening_edge_6_an_error_that_is_neither_os_nor_value_error_rises_out_of_the_worker(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    request_id = submit(layout, tone, clock)
    capfd.readouterr()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("not an OS error")

    monkeypatch.setattr(reader_client, "decode", explode)

    with pytest.raises(RuntimeError, match="not an OS error"):
        run_worker_once(layout, clock)

    assert worker_lines(capfd) == [f"{request_id} running"]
    assert read_response(layout, request_id)["state"] == "running"
