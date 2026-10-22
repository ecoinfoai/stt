"""US5 intake rejection tests: invalid requests are answered with a reason and leave nothing behind."""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path
from typing import Any

import pytest

from stt.server import contract, deadlines, intake
from stt.server.layout import Layout
from stt.server.records import load_job

from .conftest import FakeClock, leftovers, make_wav, new_id, run_worker
from .inbox_helpers import inbox_names, make_layout, put_attachment, put_body, put_bytes, read_response, transcribe_body
from .test_us2 import assert_group_gone, pid_engine


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "source.wav", 1.0)


def _drop_language(body: dict[str, Any]) -> None:
    del body["language"]


def _unknown_delete_audio(body: dict[str, Any]) -> None:
    body["delete_audio"] = "never"


def _empty_hints(body: dict[str, Any]) -> None:
    body["hints"] = []


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (_drop_language, "language: field is required"),
        (_unknown_delete_audio, "delete_audio: value is not allowed"),
        (_empty_hints, "hints: too few items"),
    ],
    ids=["missing_field", "unknown_value", "empty_hints"],
)
def test_us5_6_invalid_request_refused_with_reason(
    layout: Layout, wav: Path, mutate: Any, detail: str
) -> None:
    request_id = new_id()
    body = transcribe_body(request_id)
    mutate(body)
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", body)

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["kind"] == "invalid"
    assert response["request_id"] == request_id
    assert response["failure"] == {"reason": "invalid_request", "detail": detail}
    assert inbox_names(layout) == []
    assert not (layout.jobs / request_id).exists()


@pytest.mark.parametrize("case", ["id_mismatch", "no_attachment", "unknown_language"])
def test_us5_6_semantic_failures_become_failed_status(layout: Layout, wav: Path, case: str) -> None:
    request_id = new_id()
    body = transcribe_body(request_id)
    if case == "id_mismatch":
        body["request_id"] = new_id()
    if case == "unknown_language":
        body["language"] = "xx"
    if case != "no_attachment":
        put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", body)

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["kind"] == "transcribe"
    assert response["request_id"] == request_id
    assert response["state"] == "failed"
    assert "accepted_at" not in response
    assert response["failure"]["reason"] == "invalid_request"
    assert inbox_names(layout) == []
    assert not (layout.jobs / request_id).exists()
    assert list(layout.audio.iterdir()) == []
    assert list(layout.queue.iterdir()) == []


def test_us5_6_attachment_of_another_request_survives(layout: Layout, wav: Path) -> None:
    request_id = new_id()
    other_id = new_id()
    body = transcribe_body(request_id, audio={"file": f"{other_id}.audio.wav"})
    put_attachment(layout.inbox, f"{other_id}.audio.wav", wav)
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", body)

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert response["state"] == "failed"
    assert response["failure"]["reason"] == "invalid_request"
    assert inbox_names(layout) == [f"{other_id}.audio.wav"]


@pytest.mark.parametrize("name", ["foo", "0B9A8C2E-5F1D-4C3A-9E7B-2D6F4A1C8E30"])
def test_us5_6_non_uuid_name_dropped_silently_with_fixed_log_line(
    layout: Layout, wav: Path, capfd: pytest.CaptureFixture[str], name: str
) -> None:
    put_bytes(layout.inbox / f"{name}.json", b"{}")
    put_attachment(layout.inbox, f"{name}.audio.wav", wav)

    intake.run_once(layout, FakeClock().now)

    captured = capfd.readouterr()
    lines = (captured.out + captured.err).splitlines()
    assert len(lines) == 1
    assert name.lower() not in lines[0].lower()
    assert inbox_names(layout) == []
    assert list(layout.outbox.iterdir()) == []


AUDIO_TOO_LONG_DETAIL = "The recording is longer than 3 hours."
UNREADABLE_AUDIO_DETAIL = "The recording could not be read as audio."
PROCESSING_TIMEOUT_DETAIL = "The job ran longer than its processing time limit."


def _submit_wav(layout: Layout, wav: Path, clock: FakeClock) -> str:
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id))
    intake.run_once(layout, clock.now)
    return request_id


def test_us5_1_over_three_hours_refused(layout: Layout, wav: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(intake, "probe_duration", lambda path: deadlines.MAX_AUDIO_S + 0.5)
    request_id = _submit_wav(layout, wav, FakeClock())

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["kind"] == "transcribe"
    assert response["state"] == "failed"
    assert "accepted_at" not in response
    assert response["failure"] == {"reason": "audio_too_long", "detail": AUDIO_TOO_LONG_DETAIL}
    assert inbox_names(layout) == []
    assert list(layout.audio.iterdir()) == []
    assert not (layout.jobs / request_id).exists()
    assert list(layout.queue.iterdir()) == []


def test_us5_1_exactly_three_hours_accepted(layout: Layout, wav: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(intake, "probe_duration", lambda path: deadlines.MAX_AUDIO_S)
    request_id = _submit_wav(layout, wav, FakeClock())

    response = read_response(layout, request_id)
    assert response["state"] == "queued"
    assert (layout.jobs / request_id / "job.json").exists()


def test_us5_2_unreadable_file_refused(layout: Layout, tmp_path: Path) -> None:
    noise = tmp_path / "noise.m4a"
    noise.write_bytes(os.urandom(4096))
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.m4a", noise)
    put_body(
        layout.inbox,
        f"{request_id}.json",
        transcribe_body(request_id, audio={"file": f"{request_id}.audio.m4a"}),
    )

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["state"] == "failed"
    assert "accepted_at" not in response
    assert response["failure"] == {"reason": "unreadable_audio", "detail": UNREADABLE_AUDIO_DETAIL}
    assert str(layout.root) not in response["failure"]["detail"]
    assert inbox_names(layout) == []
    assert list(layout.audio.iterdir()) == []
    assert not (layout.jobs / request_id).exists()


def test_us5_2_recording_without_audio_stream_refused(
    layout: Layout, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import av

    class NoAudio:
        duration = 5 * av.time_base

        class streams:
            audio: list[object] = []

        def __enter__(self) -> "NoAudio":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(av, "open", lambda path, **kwargs: NoAudio())
    request_id = _submit_wav(layout, wav, FakeClock())

    response = read_response(layout, request_id)
    assert response["failure"] == {"reason": "unreadable_audio", "detail": UNREADABLE_AUDIO_DETAIL}
    assert list(layout.audio.iterdir()) == []
    assert not (layout.jobs / request_id).exists()


def test_us5_3_timeout_stops_engine_without_result(layout: Layout, wav: Path, tmp_path: Path) -> None:
    clock = FakeClock()
    request_id = _submit_wav(layout, wav, clock)
    pid_file, engine_cmd = pid_engine(tmp_path)

    started = time.monotonic()
    run_worker(layout, clock, engine_cmd=engine_cmd, processing_limit=lambda duration: 0.5)

    assert time.monotonic() - started < 10
    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["state"] == "failed"
    assert response["failure"] == {"reason": "processing_timeout", "detail": PROCESSING_TIMEOUT_DETAIL}
    assert_group_gone(int(pid_file.read_text(encoding="utf-8")))
    assert not (layout.outbox / request_id).exists()
    assert not (layout.work / request_id).exists()
    assert leftovers(layout.root) == []


def test_us5_9_short_recording_gets_five_minute_floor(layout: Layout, tmp_path: Path) -> None:
    clock = FakeClock()
    request_id = _submit_wav(layout, make_wav(tmp_path / "ten.wav", 10.0), clock)
    asked: list[float] = []
    given: list[float] = []

    def spy(duration_s: float) -> float:
        asked.append(duration_s)
        given.append(deadlines.processing_limit(duration_s))
        return given[-1]

    run_worker(layout, clock, processing_limit=spy)

    assert asked == [pytest.approx(10.0, abs=0.01)]
    assert given == [300.0]
    assert read_response(layout, request_id)["state"] == "done"


def _euc_kr_tagged_wav(path: Path, seconds: float = 1.0) -> Path:
    """Writes a 16 kHz mono PCM WAV whose INAM tag holds EUC-KR bytes, which PyAV cannot decode as UTF-8."""
    rate = 16000
    data = bytes(2 * int(rate * seconds))
    title = b"\xc7\xd1\xb1\xdb" + b"\x00"
    inam = b"INAM" + struct.pack("<I", len(title)) + title
    inam += b"\x00" * (len(inam) % 2)
    info = b"INFO" + inam
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    body = b"WAVE" + fmt + b"LIST" + struct.pack("<I", len(info)) + info + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def test_us5_2_non_utf8_metadata_does_not_make_the_recording_unreadable(layout: Layout, tmp_path: Path) -> None:
    tagged = _euc_kr_tagged_wav(tmp_path / "tagged.wav")
    request_id = _submit_wav(layout, tagged, FakeClock())

    response = read_response(layout, request_id)
    assert response["state"] == "queued"
    assert load_job(layout, request_id).duration_s == pytest.approx(1.0, abs=0.01)


@pytest.mark.parametrize("fault", ["value_error", "type_error"])
def test_us5_2_probe_faults_in_the_recording_are_unreadable_not_server_errors(
    layout: Layout, wav: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    import av

    class Broken:
        duration = None

        class streams:
            audio = [type("Stream", (), {"time_base": None})()]

        def demux(self, stream: object) -> Any:
            return [type("Packet", (), {"duration": 5})()]

        def __enter__(self) -> "Broken":
            if fault == "value_error":
                raise UnicodeDecodeError("utf-8", b"\xb0", 0, 1, "invalid start byte")
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(av, "open", lambda path, **kwargs: Broken())
    request_id = _submit_wav(layout, wav, FakeClock())

    response = read_response(layout, request_id)
    assert response["failure"] == {"reason": "unreadable_audio", "detail": UNREADABLE_AUDIO_DETAIL}
    assert list(layout.audio.iterdir()) == []


def test_us5_2_request_queued_behind_a_bad_attachment_is_still_answered(
    layout: Layout, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import av

    clock = FakeClock()
    bad_id = new_id()
    put_attachment(layout.inbox, f"{bad_id}.audio.wav", wav)
    put_body(layout.inbox, f"{bad_id}.json", transcribe_body(bad_id))
    os.utime(layout.inbox / f"{bad_id}.json", (1, 1))
    good_id = new_id()
    put_attachment(layout.inbox, f"{good_id}.audio.wav", wav)
    put_body(layout.inbox, f"{good_id}.json", transcribe_body(good_id))
    real_open = av.open

    def open_or_fail(path: str, **kwargs: Any) -> Any:
        if bad_id in path:
            raise UnicodeDecodeError("utf-8", b"\xb0", 0, 1, "invalid start byte")
        return real_open(path, **kwargs)

    monkeypatch.setattr(av, "open", open_or_fail)

    intake.run_once(layout, clock.now)

    assert read_response(layout, bad_id)["failure"]["reason"] == "unreadable_audio"
    assert read_response(layout, good_id)["state"] == "queued"
    assert inbox_names(layout) == []
