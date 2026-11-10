"""US3 of the decoder isolation: a recording that cannot be read fails alone, with a fixed reason and sentence.

These tests drive intake and the worker against the emulated reader socket (``reader_socket``), whose reader is
chosen per test with the ``use_*`` methods: the real supervisor with a decode-child double, or a whole-instance
double. They fail until intake and the worker read recordings through the reader.

Seams the worker must offer to the tests (they are not in the worker yet):

* The decoded audio is written with ``wave.open`` called through the ``wave`` module (``import wave``), so that
  patching ``wave.open`` reaches it.
* ``reader_socket.config`` is read when ``run_worker``, ``run_worker_once`` and ``intake.run_once`` are called, so
  that ``use_missing`` and ``use_limits`` take effect.
"""

from __future__ import annotations

import errno
import json
import os
import re
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, NamedTuple

import pytest

from stt.server import intake, reader, reader_client, worker
from stt.server.layout import Layout

from .conftest import (
    FakeClock,
    ReaderSocket,
    leftovers_besides_recordings,
    make_wav,
    new_id,
    run_worker,
    run_worker_once,
    write_delete_request,
)
from .inbox_helpers import inbox_names, make_layout, put_attachment, put_body, read_response, transcribe_body
from .test_decoder_us2 import recorded_engine_cmd, submit

FAKE_DECODE_CHILD = Path(__file__).with_name("fake_decode_child.py")
REAL_READER = [sys.executable, "-m", "stt", "server", "read"]
SILENCE_BYTES = 32000
RAW_DECODER_TEXT = "RAW-DECODER-ERROR-0xdeadbeef"


class Failure(NamedTuple):
    reason: str
    detail: str


UNREADABLE = Failure("unreadable_audio", "The recording could not be read as audio.")
NOT_STARTED = Failure("engine_error", "The server could not start the audio reader.")
NO_VERDICT = Failure("engine_error", "The engine stopped without a verdict.")
TIMEOUT = Failure("processing_timeout", "The job ran longer than its processing time limit.")


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


@pytest.fixture
def text_file(tmp_path: Path) -> Path:
    path = tmp_path / "notes.mp3"
    path.write_text("This is plain text, not audio.\n", encoding="utf-8")
    return path


def child_command(*arguments: str) -> list[str]:
    """Builds the decode-child double command with `arguments`."""
    return [sys.executable, str(FAKE_DECODE_CHILD), *arguments]


def put_request(layout: Layout, recording: Path, mtime: float) -> str:
    """Hands over a transcribe request for `recording`, with the request file dated `mtime`."""
    request_id = new_id()
    name = f"{request_id}.audio{recording.suffix}"
    put_attachment(layout.inbox, name, recording)
    path = put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, audio={"file": name}))
    os.utime(path, (mtime, mtime))
    return request_id


def assert_audio_holds_only(layout: Layout, request_id: str) -> None:
    """Asserts ``audio/`` holds the recording folder and the audio record of `request_id` and nothing else."""
    assert sorted(path.name for path in layout.audio.iterdir()) == sorted([request_id, f"{request_id}.json"])


def assert_failed(layout: Layout, request_id: str, expected: Failure) -> dict[str, Any]:
    """Asserts the response is ``failed`` with exactly the fixed reason and sentence."""
    response = read_response(layout, request_id)
    assert response["state"] == "failed"
    assert response["failure"] == {"reason": expected.reason, "detail": expected.detail}
    return response


def assert_no_result(layout: Layout, request_id: str, waiting: str | None = None) -> None:
    """Asserts `request_id` left no result and no leftovers; the files of the job `waiting` still queued are allowed."""
    assert not (layout.outbox / request_id).exists()
    assert [
        path for path in leftovers_besides_recordings(layout.root) if waiting is None or waiting not in path.name
    ] == []


def test_decoder_us3_1_a_text_file_with_a_recording_extension_is_rejected_and_the_rest_of_the_bundle_is_queued(
    layout: Layout, tone: Path, text_file: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    bad, good = put_request(layout, text_file, 1_000.0), put_request(layout, tone, 2_000.0)

    assert intake.run_once(layout, clock.now) == 0

    assert_failed(layout, bad, UNREADABLE)
    assert read_response(layout, good)["state"] == "queued"
    assert not (layout.jobs / bad).exists()
    assert_audio_holds_only(layout, good)
    assert inbox_names(layout) == []
    assert reader_socket.spawn_count == 2


MEASURE_FAILURES = {
    "error": (child_command("--mode", "exit-error"), {}),
    "killed": (child_command("--mode", "sigkill"), {}),
    "hang": (child_command("--mode", "hang"), {"probe_limit_s": 0.5}),
    "wrong_result": (child_command("--mode", "write-raw", "--payload", "not a result"), {}),
}


@pytest.mark.parametrize("failure", MEASURE_FAILURES)
def test_decoder_us3_2_a_length_probe_that_fails_after_start_rejects_only_that_request_and_keeps_no_copy(
    layout: Layout,
    tone: Path,
    silence: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    failure: str,
) -> None:
    child, limits = MEASURE_FAILURES[failure]
    reader_socket.use_supervisor([*child, "--once", str(tmp_path / "first.claimed")])
    reader_socket.use_limits(**limits)
    bad, good = put_request(layout, tone, 1_000.0), put_request(layout, silence, 2_000.0)

    assert intake.run_once(layout, clock.now) == 0

    assert_failed(layout, bad, UNREADABLE)
    assert not (layout.jobs / bad).exists()
    assert_audio_holds_only(layout, good)
    assert inbox_names(layout) == []
    assert read_response(layout, good)["state"] == "queued"
    assert reader_socket.spawn_count == 2


class DecodeFailure(NamedTuple):
    """One way a read of the GPU stage's recording goes wrong.

    Attributes:
        child: The decode-child arguments, or None to use `fake` instead.
        fake: The whole-instance double mode, or None to use `child`.
        limits: Changes to the reader configuration.
        shrink_output_limit: Whether the caller's output limit is lowered to 1000 bytes.
    """

    child: tuple[str, ...] | None = None
    fake: str | None = None
    limits: dict[str, float] = {}
    shrink_output_limit: bool = False


DECODE_FAILURES = {
    "error": DecodeFailure(child=("--mode", "exit-error")),
    "no_verdict": DecodeFailure(fake="no-verdict"),
    "killed_like_memory_cap": DecodeFailure(child=("--mode", "sigkill")),
    "time_limit": DecodeFailure(child=("--mode", "hang"), limits={"decode_limit_s": 1.0}),
    "output_over_limit": DecodeFailure(child=("--mode", "write-bytes", "--count", "2000"), shrink_output_limit=True),
    "output_of_odd_size": DecodeFailure(child=("--mode", "write-bytes", "--count", "3")),
}


def arrange_decode_failure(
    reader_socket: ReaderSocket, monkeypatch: pytest.MonkeyPatch, failure: DecodeFailure
) -> None:
    if failure.fake is not None:
        reader_socket.use_fake(failure.fake)
    else:
        assert failure.child is not None
        reader_socket.use_supervisor(child_command(*failure.child))
    reader_socket.use_limits(**failure.limits)
    if failure.shrink_output_limit:
        monkeypatch.setattr(reader_client, "MAX_DECODED_BYTES", 1000)


@pytest.mark.parametrize("failure", DECODE_FAILURES)
def test_decoder_us3_3_to_6_a_failing_decode_fails_that_job_without_result_and_the_next_job_is_done(
    layout: Layout,
    tone: Path,
    silence: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    bad, good = submit(layout, tone, clock), submit(layout, silence, clock)
    spawned_before = reader_socket.spawn_count
    capfd.readouterr()
    arrange_decode_failure(reader_socket, monkeypatch, DECODE_FAILURES[failure])

    assert run_worker_once(layout, clock) is True

    assert reader_socket.spawn_count - spawned_before == 1
    assert_failed(layout, bad, UNREADABLE)
    assert_no_result(layout, bad, waiting=good)
    assert capfd.readouterr().out.splitlines() == [f"{bad} running", f"{bad} failed unreadable_audio"]

    reader_socket.use_command(REAL_READER)
    reader_socket.use_limits(decode_limit_s=reader.DECODE_LIMIT_S)
    monkeypatch.setattr(reader_client, "MAX_DECODED_BYTES", reader.MAX_DECODED_BYTES)

    assert run_worker_once(layout, clock) is True

    assert read_response(layout, good)["state"] == "done"
    assert (layout.outbox / good / "result.json").is_file()


def test_decoder_us3_6_one_worker_run_goes_on_with_the_next_job_after_a_failed_decode(
    layout: Layout,
    tone: Path,
    silence: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
) -> None:
    bad, good = submit(layout, tone, clock), submit(layout, silence, clock)
    spawned_before = reader_socket.spawn_count
    reader_socket.use_supervisor(child_command("--mode", "exit-error", "--once", str(tmp_path / "first.claimed")))

    assert run_worker(layout, clock) == 0

    assert reader_socket.spawn_count - spawned_before == 2
    assert_failed(layout, bad, UNREADABLE)
    assert_no_result(layout, bad)
    assert read_response(layout, good)["state"] == "done"


ENGINE_VERDICTS = [
    ("fail=gpu_out_of_memory", Failure("gpu_out_of_memory", "Fake failure.")),
    ("fail=gpu_unavailable", Failure("gpu_unavailable", "Fake failure.")),
    ("fail=models_unavailable", Failure("models_unavailable", "Fake failure.")),
    ("fail=engine_error", Failure("engine_error", "Fake failure.")),
    ("die", NO_VERDICT),
    ("fail=unreadable_audio", NO_VERDICT),
]


@pytest.mark.parametrize(("mode", "expected"), ENGINE_VERDICTS, ids=[mode for mode, _ in ENGINE_VERDICTS])
def test_decoder_us3_7_when_the_read_succeeds_a_gpu_stage_failure_keeps_its_present_classification(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket, mode: str, expected: Failure
) -> None:
    request_id = submit(layout, tone, clock)
    spawned_before = reader_socket.spawn_count

    run_worker(layout, clock, mode)

    assert reader_socket.spawn_count - spawned_before == 1
    assert_failed(layout, request_id, expected)
    assert_no_result(layout, request_id)


UNAVAILABLE_READERS = ["missing_socket", "not_started", "closed_before_start"]


def make_reader_unavailable(reader_socket: ReaderSocket, how: str) -> None:
    if how == "missing_socket":
        reader_socket.use_missing()
    elif how == "not_started":
        reader_socket.use_fake("not-started")
    else:
        reader_socket.use_fake("no-start")


@pytest.mark.parametrize("how", UNAVAILABLE_READERS)
def test_decoder_us3_8_the_worker_fails_engine_error_when_the_reader_cannot_be_started(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    capfd: pytest.CaptureFixture[str],
    how: str,
) -> None:
    request_id = submit(layout, tone, clock)
    spawned_before = reader_socket.spawn_count
    capfd.readouterr()
    make_reader_unavailable(reader_socket, how)

    assert run_worker_once(layout, clock) is True

    assert reader_socket.spawn_count - spawned_before == (0 if how == "missing_socket" else 1)
    assert_failed(layout, request_id, NOT_STARTED)
    assert_no_result(layout, request_id)
    assert capfd.readouterr().out.splitlines() == [f"{request_id} running", f"{request_id} failed engine_error"]


@pytest.mark.parametrize("how", UNAVAILABLE_READERS)
def test_decoder_us3_8_intake_logs_and_drops_a_request_when_the_reader_cannot_be_started(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    capfd: pytest.CaptureFixture[str],
    how: str,
) -> None:
    make_reader_unavailable(reader_socket, how)
    request_id = put_request(layout, tone, 1_000.0)

    assert intake.run_once(layout, clock.now) == 1

    lines = capfd.readouterr().out.splitlines()
    assert len(lines) == 1
    assert re.fullmatch(rf"{request_id} intake error ReaderUnavailableError", lines[0])
    assert not (layout.outbox / f"{request_id}.json").exists()
    assert inbox_names(layout) == []
    assert list(layout.audio.iterdir()) == []
    assert not (layout.jobs / request_id).exists()


def test_decoder_us3_9_the_decoders_own_error_text_reaches_no_response_and_no_log_of_intake_or_worker(
    layout: Layout,
    tone: Path,
    silence: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    capfd: pytest.CaptureFixture[str],
) -> None:
    leaking = child_command("--mode", "write-raw-then-error", "--payload", RAW_DECODER_TEXT)
    accepted = submit(layout, tone, clock)
    reader_socket.use_supervisor(leaking)
    refused = put_request(layout, silence, 1_000.0)
    intake.run_once(layout, clock.now)
    run_worker(layout, clock)

    assert_failed(layout, refused, UNREADABLE)
    assert_failed(layout, accepted, UNREADABLE)
    captured = capfd.readouterr()
    seen = captured.out + captured.err
    documents = [(layout.outbox / f"{request_id}.json").read_text(encoding="utf-8") for request_id in (refused, accepted)]
    assert RAW_DECODER_TEXT not in seen
    assert all(RAW_DECODER_TEXT not in document for document in documents)


def test_decoder_edge_4_the_job_limit_coming_first_ends_the_job_as_processing_timeout(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    request_id = submit(layout, tone, clock)
    reader_socket.use_supervisor(child_command("--mode", "hang"))
    reader_socket.use_limits(decode_limit_s=30.0)

    run_worker(layout, clock, processing_limit=lambda duration_s: 1.0)

    assert_failed(layout, request_id, TIMEOUT)
    assert_no_result(layout, request_id)


def test_decoder_edge_4_the_reader_limit_coming_first_ends_the_job_as_unreadable_audio(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    request_id = submit(layout, tone, clock)
    reader_socket.use_supervisor(child_command("--mode", "hang"))
    reader_socket.use_limits(decode_limit_s=0.5)

    run_worker(layout, clock, processing_limit=lambda duration_s: 30.0)

    assert_failed(layout, request_id, UNREADABLE)
    assert_no_result(layout, request_id)


def test_decoder_edge_4_a_read_that_uses_up_the_job_limit_ends_the_job_without_starting_the_engine(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id = submit(layout, tone, clock)
    engine_launches: list[object] = []
    lapse = [0.0]
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        worker,
        "time",
        SimpleNamespace(monotonic=lambda: real_monotonic() + lapse[0], sleep=time.sleep),
    )
    real_read = worker._read

    def read_then_run_out_of_time(*args: Any, **kwargs: Any) -> Any:
        outcome = real_read(*args, **kwargs)
        lapse[0] += 100_000.0
        return outcome

    monkeypatch.setattr(worker, "_read", read_then_run_out_of_time)
    real_run_engine = worker._run_engine

    def counting_run_engine(*args: Any, **kwargs: Any) -> Any:
        engine_launches.append(args)
        return real_run_engine(*args, **kwargs)

    monkeypatch.setattr(worker, "_run_engine", counting_run_engine)

    run_worker(layout, clock)

    assert_failed(layout, request_id, TIMEOUT)
    assert_no_result(layout, request_id)
    assert engine_launches == []


def failing_wave_open(real_open: Callable[..., Any]) -> Callable[..., Any]:
    """Wraps ``wave.open`` so that writing frames fails as a full disk does, after the file was created."""

    def broken_write(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    def opened(path: Any, mode: str | None = None) -> Any:
        handle = real_open(path, mode)
        if mode in ("wb", "w"):
            handle.writeframes = broken_write
            handle.writeframesraw = broken_write
        return handle

    return opened


def test_decoder_edge_5_a_storage_error_while_writing_the_decoded_audio_fails_the_job_and_leaves_no_file(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id = submit(layout, tone, clock)
    reader_socket.use_supervisor(child_command("--mode", "write-bytes", "--count", str(SILENCE_BYTES)))
    monkeypatch.setattr(wave, "open", failing_wave_open(wave.open))
    spawned_before = reader_socket.spawn_count

    assert run_worker_once(layout, clock) is True

    assert reader_socket.spawn_count - spawned_before == 1
    assert_failed(layout, request_id, UNREADABLE)
    assert_no_result(layout, request_id)
    assert [path for path in layout.root.rglob("*.wav") if layout.audio not in path.parents] == []


TWO_SECONDS_OF_SAMPLES = 2 * reader.SAMPLE_RATE


def test_decoder_edge_10_a_decode_that_stopped_early_ends_the_job_done_with_the_audio_before_it(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    request_id = submit(layout, tone, clock)
    capfd.readouterr()
    reader_socket.use_supervisor(child_command("--mode", "early-stop", "--count", str(2 * TWO_SECONDS_OF_SAMPLES)))
    record = tmp_path / "engine.jsonl"

    assert run_worker_once(layout, clock, engine_cmd=recorded_engine_cmd(record)) is True

    assert read_response(layout, request_id)["state"] == "done"
    assert (layout.outbox / request_id / "result.json").is_file()
    assert capfd.readouterr().out.splitlines() == [f"{request_id} running", f"{request_id} done"]
    (line,) = record.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["wav_frames"] == TWO_SECONDS_OF_SAMPLES


def test_decoder_edge_10_a_recording_damaged_from_its_first_frame_ends_the_job_done_with_an_empty_audio(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    request_id = submit(layout, tone, clock)
    capfd.readouterr()
    reader_socket.use_supervisor(child_command("--mode", "early-stop", "--count", "0"))
    record = tmp_path / "engine.jsonl"

    assert run_worker_once(layout, clock, engine_cmd=recorded_engine_cmd(record)) is True

    assert read_response(layout, request_id)["state"] == "done"
    assert (layout.outbox / request_id / "result.json").is_file()
    assert capfd.readouterr().out.splitlines() == [f"{request_id} running", f"{request_id} done"]
    (line,) = record.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["wav_frames"] == 0


@pytest.mark.parametrize("outcome", ["timeout", "failed", "unavailable"])
def test_decoder_us4_4_a_job_deleted_during_the_read_is_never_settled_as_a_failure_of_the_read(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    outcome: str,
) -> None:
    request_id = submit(layout, tone, clock)

    def delete_then_report(*args: Any, **kwargs: Any) -> str:
        write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=request_id)
        intake.run_once(layout, clock.now)
        return outcome

    monkeypatch.setattr(worker, "_read", delete_then_report)

    assert run_worker_once(layout, clock) is True

    assert read_response(layout, request_id)["state"] == "deleted"
    lines = capfd.readouterr().out.splitlines()
    assert f"{request_id} stopped" in lines
    assert not [line for line in lines if line.startswith(f"{request_id} failed")]
    assert leftovers_besides_recordings(layout.root) == []


@pytest.mark.parametrize("code", [errno.EACCES, errno.EMFILE])
def test_decoder_us3_8_an_os_error_before_anything_is_sent_to_the_reader_is_a_server_fault_not_unreadable_audio(
    layout: Layout,
    tone: Path,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    code: int,
) -> None:
    request_id = submit(layout, tone, clock)
    spawned_before = reader_socket.spawn_count
    capfd.readouterr()

    def refuse_to_open(path: Any) -> int:
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(reader_client, "_open_recording", refuse_to_open)

    assert run_worker_once(layout, clock) is True

    assert reader_socket.spawn_count == spawned_before
    assert_failed(layout, request_id, NOT_STARTED)
    assert_no_result(layout, request_id)
    assert capfd.readouterr().out.splitlines() == [f"{request_id} running", f"{request_id} failed engine_error"]
