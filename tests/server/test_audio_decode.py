"""The shared decoder: the reader child's s16 writer and the command line's float32 samples."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stt import audio_decode
from stt.server import reader

from .conftest import av_major, encode_tone, make_wav, wav_samples

FASTER_WHISPER_DECODER_MAX_AV_MAJOR = 18


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


def test_the_reader_format_list_still_applies_through_open_container(tmp_path: Path) -> None:
    import av

    recording = encode_tone(tmp_path / "tone.aiff", "aiff", "pcm_s16be")

    with open(recording, "rb") as handle, pytest.raises(av.error.InvalidDataError):
        audio_decode.open_container(handle, format_whitelist=reader.AUDIO_FORMATS)
