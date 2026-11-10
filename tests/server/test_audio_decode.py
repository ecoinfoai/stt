"""The shared decoder: the reader child's s16 writer and the command line's float32 samples."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from stt import audio_decode
from stt.server import reader

from .conftest import (
    FAKE_FRAME_SAMPLES,
    FASTER_WHISPER_DECODER_MAX_AV_MAJOR,
    FakeFrameContainer,
    av_major,
    encode_tone,
    make_wav,
    wav_samples,
)


def test_the_decoder_and_the_reader_agree_on_the_sample_rate() -> None:
    assert audio_decode.SAMPLE_RATE == reader.SAMPLE_RATE == 16000


def test_a_16khz_mono_wav_decodes_to_exactly_its_own_samples(tmp_path: Path) -> None:
    recording = make_wav(tmp_path / "tone.wav", 2.0, silent=False)
    stored = np.frombuffer(wav_samples(recording), dtype="<i2")

    decoded = audio_decode.decode_samples(recording)

    assert decoded.dtype == np.float32
    assert np.array_equal(decoded, stored.astype(np.float32) / 32768.0)


def test_decoded_samples_equal_faster_whisper_where_its_decoder_runs(tmp_path: Path) -> None:
    recording = make_wav(tmp_path / "tone.wav", 2.0, silent=False)
    if av_major() <= FASTER_WHISPER_DECODER_MAX_AV_MAJOR:
        from faster_whisper.audio import decode_audio

        reference = decode_audio(str(recording))
    else:
        reference = np.frombuffer(wav_samples(recording), dtype="<i2").astype(np.float32) / 32768.0

    assert np.array_equal(audio_decode.decode_samples(recording), reference)


def test_a_format_outside_the_reader_list_decodes_for_the_command_line(tmp_path: Path) -> None:
    recording = encode_tone(tmp_path / "tone.aiff", "aiff", "pcm_s16be")

    decoded = audio_decode.decode_samples(recording)

    assert decoded.dtype == np.float32 and decoded.ndim == 1
    assert len(decoded) == 2 * audio_decode.SAMPLE_RATE
    assert 0.25 < float(np.abs(decoded).max()) < 0.35


def test_other_sample_rates_are_resampled_to_16khz_mono(tmp_path: Path) -> None:
    recording = encode_tone(tmp_path / "tone.wav", "wav", "pcm_s16le", rate=48000)

    decoded = audio_decode.decode_samples(recording)

    assert abs(len(decoded) - 2 * audio_decode.SAMPLE_RATE) <= 16
    assert 0.25 < float(np.abs(decoded).max()) < 0.35


def test_a_text_file_with_a_recording_extension_fails_loudly(tmp_path: Path) -> None:
    import av

    recording = tmp_path / "notes.m4a"
    recording.write_text("this is not audio\n", encoding="utf-8")

    with pytest.raises(av.error.InvalidDataError):
        audio_decode.decode_samples(recording)


def test_a_missing_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        audio_decode.decode_samples(tmp_path / "absent.m4a")


# ------------------------------------------------ a damaged frame ends the decode early


def test_a_damaged_frame_ends_the_decode_with_everything_before_it_and_reports_the_early_stop() -> None:
    output = io.BytesIO()

    stopped_early = audio_decode.write_s16(FakeFrameContainer(3, fail_after=True), output)

    assert stopped_early is True
    assert output.getvalue() == np.full(3 * FAKE_FRAME_SAMPLES, 1000, dtype="<i2").tobytes()


def test_a_recording_damaged_from_its_first_frame_gives_empty_output_and_reports_the_early_stop() -> None:
    output = io.BytesIO()

    stopped_early = audio_decode.write_s16(FakeFrameContainer(0, fail_after=True), output)

    assert stopped_early is True
    assert output.getvalue() == b""


def test_an_undamaged_recording_does_not_report_an_early_stop() -> None:
    output = io.BytesIO()

    stopped_early = audio_decode.write_s16(FakeFrameContainer(3, fail_after=False), output)

    assert stopped_early is False
    assert len(output.getvalue()) == 3 * FAKE_FRAME_SAMPLES * 2


def test_decode_samples_tells_the_caller_how_far_a_damaged_recording_got(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audio_decode, "open_container", lambda source: FakeFrameContainer(10, fail_after=True))
    reported: list[float] = []

    decoded = audio_decode.decode_samples(tmp_path / "damaged.m4a", on_early_stop=reported.append)

    assert len(decoded) == 10 * FAKE_FRAME_SAMPLES
    assert reported == [10 * FAKE_FRAME_SAMPLES / audio_decode.SAMPLE_RATE]


def test_decode_samples_reports_nothing_for_an_undamaged_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audio_decode, "open_container", lambda source: FakeFrameContainer(10, fail_after=False))
    reported: list[float] = []

    audio_decode.decode_samples(tmp_path / "clean.m4a", on_early_stop=reported.append)

    assert reported == []


class _RecordingModel:
    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]:
        segment = SimpleNamespace(start=0.0, end=1.0, text="x", words=[])
        return iter([segment]), SimpleNamespace(duration=1.0, language="ko")


def test_the_command_line_warns_once_on_standard_error_when_the_decode_stopped_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from stt import transcribe

    monkeypatch.setattr(audio_decode, "open_container", lambda source: FakeFrameContainer(20, fail_after=True))

    transcribe.transcribe_file(_RecordingModel(), tmp_path / "damaged.m4a", "ko", None, 5)

    captured = capsys.readouterr()
    assert captured.err == (
        "warning: decoding stopped at invalid data after 2.0 s; the transcript covers only that part\n"
    )
    assert captured.out == ""


def test_the_command_line_prints_nothing_for_an_undamaged_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from stt import transcribe

    monkeypatch.setattr(audio_decode, "open_container", lambda source: FakeFrameContainer(20, fail_after=False))

    transcribe.transcribe_file(_RecordingModel(), tmp_path / "clean.m4a", "ko", None, 5)

    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""
