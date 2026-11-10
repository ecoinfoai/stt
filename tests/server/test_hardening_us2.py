"""US2 of the reader hardening: the worker names a recording whose decoded audio is shorter than its probed length.

A recording whose end was cut off still reads, so its job ends ``done`` with the transcript of what is left. The
worker writes one fixed line, ``worker.TRUNCATION_LINE``, between ``<id> running`` and ``<id> done`` when the decoded
audio is at least ``worker.TRUNCATION_THRESHOLD_S`` shorter than the length measured on receipt. These tests drive the
real reader supervisor and the real decode child behind the emulated reader socket, and the fake engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stt import audio_decode
from stt.server import intake, reader, reader_client, worker
from stt.server.layout import Layout

from .conftest import (
    FakeClock,
    ReaderSocket,
    encode_tone,
    make_faststart_m4a,
    new_id,
    run_worker,
    run_worker_once,
    truncate_after_mdat_header,
    truncate_to_ratio,
    write_delete_request,
)
from .inbox_helpers import make_layout, read_response
from .test_decoder_us2 import recorded_engine_cmd, submit, submit_second_pass
from .test_decoder_us3 import child_command
from .test_reader import LISTED_FORMATS

TEN_SECONDS = 10.0
JOB_LIMIT_S = 120.0
BYTES_PER_SECOND = 2 * reader.SAMPLE_RATE


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_half_cut_flac(path: Path) -> Path:
    """Writes a 10-second FLAC tone and cuts it to half its bytes."""
    return truncate_to_ratio(encode_tone(path, "flac", "flac", seconds=TEN_SECONDS), 0.5)


def make_half_cut_mp3(path: Path) -> Path:
    """Writes a 10-second MP3 tone (with a Xing header) and cuts it to half its bytes."""
    return truncate_to_ratio(encode_tone(path, "mp3", "libmp3lame", seconds=TEN_SECONDS), 0.5)


def make_half_cut_faststart_m4a(path: Path) -> Path:
    """Writes a 10-second M4A with its index first and cuts it to half its bytes."""
    return truncate_to_ratio(make_faststart_m4a(path, TEN_SECONDS), 0.5)


HALF_CUT = {"mp3": make_half_cut_mp3, "flac": make_half_cut_flac, "faststart-m4a": make_half_cut_faststart_m4a}


def stdout_lines(capfd: pytest.CaptureFixture[str]) -> list[str]:
    return capfd.readouterr().out.splitlines()


def decoded_seconds(reader_socket: ReaderSocket, recording: Path) -> float:
    """Decodes `recording` through the real supervisor and returns the decoded length in seconds."""
    result = reader_client.decode(reader_socket.config, recording, job_running=lambda: True, job_limit_s=JOB_LIMIT_S)
    assert result.outcome == "ok"
    return len(result.audio) / BYTES_PER_SECOND


# ---------------------------------------------------------------- US2-1


@pytest.mark.parametrize("kind", sorted(HALF_CUT))
def test_hardening_us2_1_a_recording_cut_in_half_ends_done_with_one_truncation_line_between_running_and_done(
    layout: Layout,
    clock: FakeClock,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    recording = HALF_CUT[kind](tmp_path / f"cut.{'m4a' if kind.endswith('m4a') else kind}")
    request_id = submit(layout, recording, clock)
    assert read_response(layout, request_id)["state"] == "queued"
    capfd.readouterr()

    assert run_worker_once(layout, clock) is True

    assert read_response(layout, request_id)["state"] == "done"
    assert (layout.outbox / request_id / "result.json").is_file()
    assert stdout_lines(capfd) == [f"{request_id} running", worker.TRUNCATION_LINE, f"{request_id} done"]


# ---------------------------------------------------------------- US2-2


INTACT_FORMATS = [*LISTED_FORMATS, ("wav", "wav", "pcm_s16le", 16000)]


@pytest.mark.parametrize(
    ("extension", "muxer", "codec", "rate"), INTACT_FORMATS, ids=[case[0] for case in INTACT_FORMATS]
)
def test_hardening_us2_2_an_intact_recording_leaves_no_truncation_line(
    layout: Layout,
    clock: FakeClock,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    extension: str,
    muxer: str,
    codec: str,
    rate: int,
) -> None:
    recording = encode_tone(tmp_path / f"intact.{extension}", muxer, codec, rate=rate, seconds=TEN_SECONDS)
    request_id = submit(layout, recording, clock)
    capfd.readouterr()

    assert run_worker_once(layout, clock) is True

    assert read_response(layout, request_id)["state"] == "done"
    assert stdout_lines(capfd) == [f"{request_id} running", f"{request_id} done"]


def test_hardening_us2_2_an_intact_faststart_m4a_leaves_no_truncation_line(
    layout: Layout, clock: FakeClock, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    request_id = submit(layout, make_faststart_m4a(tmp_path / "intact.m4a", TEN_SECONDS), clock)
    capfd.readouterr()

    assert run_worker_once(layout, clock) is True

    assert stdout_lines(capfd) == [f"{request_id} running", f"{request_id} done"]


# ---------------------------------------------------------------- US2-3


@pytest.mark.parametrize(
    ("probed_s", "decoded_seconds", "expected"),
    [
        (10.0, 9.0, True),
        (10.0, 8.5, True),
        (2.0, 0.0, True),
        (10.0, 9.0 + 1 / BYTES_PER_SECOND, False),
        (10.0, 10.0, False),
        (10.0, 12.0, False),
        (3600.0, 3600.056, False),
        (10.0, 10.0 - 0.046, False),
        (10.0, 10.0 - 0.0065, False),
        (10.0, 10.0 - 0.00775, False),
    ],
    ids=[
        "exactly-one-second-short",
        "1.5-seconds-short",
        "nothing-decoded",
        "one-sample-under-the-threshold",
        "equal",
        "decoded-longer",
        "reference-hour-longer",
        "wma-end-0.046",
        "opus-end-0.0065",
        "002-end-0.00775",
    ],
)
def test_hardening_us2_3_is_truncated_compares_the_probed_length_with_the_decoded_bytes(
    probed_s: float, decoded_seconds: float, expected: bool
) -> None:
    decoded_bytes = round(decoded_seconds * BYTES_PER_SECOND)

    assert worker.is_truncated(probed_s, decoded_bytes) is expected


def test_hardening_us2_3_the_threshold_is_one_second() -> None:
    assert worker.TRUNCATION_THRESHOLD_S == 1.0


# ---------------------------------------------------------------- US2-4


def test_hardening_us2_4_a_faststart_m4a_cut_after_its_mdat_header_ends_done_with_an_empty_transcript_and_one_line(
    layout: Layout, clock: FakeClock, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    recording = truncate_after_mdat_header(make_faststart_m4a(tmp_path / "no_media.m4a", TEN_SECONDS))
    request_id = submit(layout, recording, clock)
    capfd.readouterr()
    engine_record = tmp_path / "engine.jsonl"

    assert run_worker_once(layout, clock, engine_cmd=recorded_engine_cmd(engine_record)) is True

    assert read_response(layout, request_id)["state"] == "done"
    assert '"wav_frames": 0' in engine_record.read_text(encoding="utf-8")
    assert stdout_lines(capfd) == [f"{request_id} running", worker.TRUNCATION_LINE, f"{request_id} done"]


# ---------------------------------------------------------------- US2-5


def test_hardening_us2_5_a_cut_recording_and_its_second_pass_leave_one_line_per_job(
    layout: Layout, clock: FakeClock, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    first = submit(layout, make_half_cut_flac(tmp_path / "cut.flac"), clock)
    second = submit_second_pass(layout, clock, first)
    capfd.readouterr()

    assert run_worker_once(layout, clock) is True
    assert run_worker_once(layout, clock) is True

    assert read_response(layout, first)["state"] == "done"
    assert read_response(layout, second)["state"] == "done"
    assert stdout_lines(capfd) == [
        f"{first} running",
        worker.TRUNCATION_LINE,
        f"{first} done",
        f"{second} running",
        worker.TRUNCATION_LINE,
        f"{second} done",
    ]


# ---------------------------------------------------------------- FR-006


def test_hardening_us2_6_the_line_holds_no_request_id_path_file_name_or_number(
    layout: Layout, clock: FakeClock, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    recording = make_half_cut_flac(tmp_path / "secret-name.flac")
    request_id = submit(layout, recording, clock)
    capfd.readouterr()

    run_worker_once(layout, clock)

    (line,) = [line for line in stdout_lines(capfd) if "shorter" in line]
    assert line == worker.TRUNCATION_LINE
    assert not any(character.isdigit() for character in line)
    for secret in (request_id, recording.name, str(tmp_path), "/"):
        assert secret not in line


# ---------------------------------------------------------------- edge case 5


class NoDuration:
    """Wraps an open container so that its container duration is missing, as for a format that has none."""

    duration = None

    def __init__(self, container: Any) -> None:
        self._container = container

    def __getattr__(self, name: str) -> Any:
        return getattr(self._container, name)


def packet_sum_seconds(recording: Path) -> float:
    """Measures `recording` the way the reader does when the container has no duration."""
    with open(recording, "rb") as handle:
        with audio_decode.open_container(
            handle, format_whitelist=reader.AUDIO_FORMATS, codec_whitelist=reader.AUDIO_DECODERS
        ) as container:
            return reader.container_duration_s(NoDuration(container))


@pytest.mark.parametrize("cut", [False, True], ids=["intact", "half-cut"])
def test_hardening_edge_5_the_packet_sum_path_measures_what_decodes_so_no_line_is_due(
    reader_socket: ReaderSocket, tmp_path: Path, cut: bool
) -> None:
    recording = encode_tone(tmp_path / "sine.flac", "flac", "flac", seconds=TEN_SECONDS)
    if cut:
        truncate_to_ratio(recording, 0.5)

    probed_s = packet_sum_seconds(recording)
    decoded_s = decoded_seconds(reader_socket, recording)

    assert probed_s == pytest.approx(decoded_s, abs=1.0)
    assert worker.is_truncated(probed_s, round(decoded_s * BYTES_PER_SECOND)) is False
    assert (decoded_s < TEN_SECONDS - 1.0) is cut


@pytest.mark.parametrize(
    ("extension", "muxer", "codec", "rate"),
    [("wav", "wav", "pcm_s16le", 16000), ("ogg", "ogg", "libopus", 48000)],
    ids=["wav", "ogg"],
)
def test_hardening_edge_5_a_format_without_a_length_in_its_header_is_cut_with_its_probed_length(
    layout: Layout,
    clock: FakeClock,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    extension: str,
    muxer: str,
    codec: str,
    rate: int,
) -> None:
    recording = encode_tone(tmp_path / f"cut.{extension}", muxer, codec, rate=rate, seconds=TEN_SECONDS)
    truncate_to_ratio(recording, 0.5)
    request_id = submit(layout, recording, clock)
    capfd.readouterr()

    assert run_worker_once(layout, clock) is True

    assert read_response(layout, request_id)["state"] == "done"
    assert stdout_lines(capfd) == [f"{request_id} running", f"{request_id} done"]


# ---------------------------------------------------------------- edge case 4


def test_hardening_edge_4_a_decode_that_stopped_early_leaves_the_line_when_the_probed_length_is_a_second_longer(
    layout: Layout,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    recording = encode_tone(tmp_path / "tone.flac", "flac", "flac", seconds=TEN_SECONDS)
    request_id = submit(layout, recording, clock)
    capfd.readouterr()
    reader_socket.use_supervisor(child_command("--mode", "early-stop", "--count", str(4 * BYTES_PER_SECOND)))

    assert run_worker_once(layout, clock) is True

    assert read_response(layout, request_id)["state"] == "done"
    assert stdout_lines(capfd) == [f"{request_id} running", worker.TRUNCATION_LINE, f"{request_id} done"]


def test_hardening_edge_4_a_decode_that_stopped_early_within_a_second_of_the_probed_length_leaves_no_line(
    layout: Layout,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    recording = encode_tone(tmp_path / "tone.flac", "flac", "flac", seconds=TEN_SECONDS)
    request_id = submit(layout, recording, clock)
    capfd.readouterr()
    reader_socket.use_supervisor(child_command("--mode", "early-stop", "--count", str(int(9.5 * BYTES_PER_SECOND))))

    assert run_worker_once(layout, clock) is True

    assert stdout_lines(capfd) == [f"{request_id} running", f"{request_id} done"]


# ---------------------------------------------------------------- a read that did not end ok


@pytest.mark.parametrize("how", ["failed", "timeout", "stopped"])
def test_hardening_us2_7_a_read_that_does_not_end_ok_leaves_no_truncation_line(
    layout: Layout,
    clock: FakeClock,
    reader_socket: ReaderSocket,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    how: str,
) -> None:
    request_id = submit(layout, make_half_cut_flac(tmp_path / "cut.flac"), clock)
    capfd.readouterr()
    if how == "failed":
        reader_socket.use_supervisor(child_command("--mode", "exit-error"))
        run_worker_once(layout, clock)
    elif how == "timeout":
        reader_socket.use_supervisor(child_command("--mode", "hang"))
        reader_socket.use_limits(decode_limit_s=30.0)
        run_worker(layout, clock, processing_limit=lambda duration_s: 1.0)
    else:

        def delete_then_stop(*args: Any, **kwargs: Any) -> str:
            write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=request_id)
            intake.run_once(layout, clock.now)
            return "stopped"

        monkeypatch.setattr(worker, "_read", delete_then_stop)
        run_worker_once(layout, clock)

    lines = stdout_lines(capfd)
    assert lines[0] == f"{request_id} running"
    assert worker.TRUNCATION_LINE not in lines


# ---------------------------------------------------------------- a delete right after the audio was written


def test_hardening_us2_8_a_job_deleted_right_after_its_audio_was_written_ends_as_stopped_and_the_next_job_runs(
    layout: Layout,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    doomed = submit(layout, make_half_cut_flac(tmp_path / "cut.flac"), clock)
    next_job = submit(layout, make_half_cut_flac(tmp_path / "next.flac"), clock)
    real_read = worker._read
    deleted: list[str] = []

    def read_then_delete(*args: Any, **kwargs: Any) -> Any:
        outcome = real_read(*args, **kwargs)
        if not deleted:
            deleted.append(doomed)
            write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=doomed)
            intake.run_once(layout, clock.now)
        return outcome

    monkeypatch.setattr(worker, "_read", read_then_delete)
    capfd.readouterr()

    run_worker(layout, clock)

    assert read_response(layout, doomed)["state"] == "deleted"
    assert not (layout.outbox / doomed).exists()
    assert read_response(layout, next_job)["state"] == "done"
    lines = stdout_lines(capfd)
    assert f"{doomed} stopped" in lines
    assert f"{next_job} done" in lines
