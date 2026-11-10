"""US1 of the reader hardening: the reader child opens only the audio decoders of ``reader.AUDIO_DECODERS``.

A recording that carries a video track or a cover picture is read for its audio only; a recording whose
audio codec is not on the list is refused when it is received. Every test drives the real reader supervisor
and the real decode child (the autouse ``reader_socket`` fixture) or the in-process functions they call.

Recorder codecs without an encoder in PyAV (WMA Pro, WMA Voice, WMA Lossless, AMR-WB) have no test file here:
the operator checks them on the server with a real recording (the production check of the hardening feature),
because no recording is fetched from outside and none is built by hand. AMR-NB is tested only where the
installed PyAV has the encoder.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from stt import audio_decode
from stt.server import deadlines, lifecycle, reader, reader_client
from stt.server.layout import Layout, hold
from stt.server.reader import AUDIO_DECODERS
from stt.server.records import Audio, Job, load_jobs, next_seq, save_audio, save_job

from .conftest import (
    FakeClock,
    ReaderSocket,
    encode_tone,
    make_adpcm_wav,
    make_amr_nb_3gp,
    make_mov_ima4,
    make_mp2,
    make_mp3_with_cover,
    make_mp4_video_only,
    make_mp4_with_dolby_audio,
    make_mp4_with_two_audio_streams,
    make_mp4_with_video,
    make_wav,
    new_id,
    run_worker,
    run_worker_once,
    stream_kinds_and_codecs,
)
from .inbox_helpers import make_layout, read_response
from .test_decoder_us2 import submit
from .test_reader import FORMAT_IDS, LISTED_FORMATS, assert_is_the_two_second_tone

UNREADABLE = {"reason": "unreadable_audio", "detail": "The recording could not be read as audio."}
JOB_LIMIT_S = 120.0
TWO_SECONDS_OF_SAMPLES = 2 * reader.SAMPLE_RATE

# Variants of the PCM family and ALAC: (id, muxer, encoder, file extension).
PCM_AND_ALAC = [
    ("alac-m4a", "ipod", "alac", "m4a"),
    ("pcm_s16le-wav", "wav", "pcm_s16le", "wav"),
    ("pcm_s24le-wav", "wav", "pcm_s24le", "wav"),
    ("pcm_s32le-wav", "wav", "pcm_s32le", "wav"),
    ("pcm_u8-wav", "wav", "pcm_u8", "wav"),
    ("pcm_f32le-wav", "wav", "pcm_f32le", "wav"),
    ("pcm_f64le-wav", "wav", "pcm_f64le", "wav"),
    ("pcm_alaw-wav", "wav", "pcm_alaw", "wav"),
    ("pcm_mulaw-wav", "wav", "pcm_mulaw", "wav"),
    ("pcm_s16be-mov", "mov", "pcm_s16be", "m4a"),
    ("pcm_s24be-mov", "mov", "pcm_s24be", "m4a"),
    ("pcm_s32be-mov", "mov", "pcm_s32be", "m4a"),
    ("pcm_f32be-mov", "mov", "pcm_f32be", "m4a"),
    ("pcm_f64be-mov", "mov", "pcm_f64be", "m4a"),
    ("pcm_s8-mov", "mov", "pcm_s8", "m4a"),
]


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def probe(reader_socket: ReaderSocket, recording: Path) -> reader_client.ProbeResult:
    """Measures `recording` through the real supervisor and decode child."""
    return reader_client.probe(reader_socket.config, recording)


def decode(reader_socket: ReaderSocket, recording: Path) -> reader_client.DecodeResult:
    """Decodes `recording` through the real supervisor and decode child."""
    return reader_client.decode(reader_socket.config, recording, job_running=lambda: True, job_limit_s=JOB_LIMIT_S)


def read_in_process(recording: Path, codec_whitelist: str | None) -> tuple[float, bytes]:
    """Returns the probed length and the decoded bytes, opened as the decode child opens a recording."""
    with open(recording, "rb") as handle:
        with audio_decode.open_container(
            handle, format_whitelist=reader.AUDIO_FORMATS, codec_whitelist=codec_whitelist
        ) as container:
            duration = reader.container_duration_s(container)
        handle.seek(0)
        output = io.BytesIO()
        with audio_decode.open_container(
            handle, format_whitelist=reader.AUDIO_FORMATS, codec_whitelist=codec_whitelist
        ) as container:
            audio_decode.write_s16(container, output)
    return duration, output.getvalue()


def audio_only_twin(path: Path, muxer: str, codec: str) -> Path:
    """Writes the audio-only recording whose audio stream is byte-identical to the one in the video/cover twin."""
    return encode_tone(path, muxer, codec, rate=16000)


def assert_reads_ok_like(reader_socket: ReaderSocket, recording: Path, plain: Path) -> None:
    """Asserts the real child probes and decodes `recording` to the same values as the audio-only `plain`."""
    expected_probe, expected_decode = probe(reader_socket, plain), decode(reader_socket, plain)
    assert expected_probe.outcome == "ok" and expected_decode.outcome == "ok"

    measured, decoded = probe(reader_socket, recording), decode(reader_socket, recording)

    assert measured.outcome == "ok" and decoded.outcome == "ok"
    assert measured.duration_s == expected_probe.duration_s
    assert bytes(decoded.audio) == bytes(expected_decode.audio)
    assert len(decoded.audio) > 0


def video_streams_unopened(recording: Path) -> None:
    """Asserts the child's open leaves every video stream without a decoder and refuses to decode it.

    ``format`` is the pixel format that ``avformat_find_stream_info`` fills in only when it opened the video
    decoder, so ``None`` shows that the container-level allowlist kept the analysis from opening it.
    ``pix_fmt`` says the same but crashes PyAV 17.1 on a context that was never opened.
    """
    import av

    with open(recording, "rb") as handle, reader._open_container(handle) as container:
        videos = container.streams.video
        assert len(videos) >= 1
        assert all(stream.codec_context.format is None for stream in videos)
        with pytest.raises(av.error.ArgumentError):
            list(container.decode(video=0))


def place_accepted_job(layout: Layout, recording: Path, clock: FakeClock) -> str:
    """Puts a queued job and its recording on disk as an earlier release accepted them (no reader involved)."""
    request_id = new_id()
    now = clock.now()
    folder = layout.audio / request_id
    folder.mkdir(mode=0o700)
    target = folder / f"audio{recording.suffix}"
    shutil.copyfile(recording, target)
    os.chmod(target, 0o600)
    with hold(layout.state_lock):
        job = Job(
            request_id=request_id,
            requested_by="professor-a",
            seq=next_seq(load_jobs(layout)),
            accepted_at=now,
            audio_id=request_id,
            language="ko",
            diarize=False,
            hints=None,
            duration_s=2.0,
            state="queued",
            expires_at=deadlines.purge_time(now),
        )
        save_audio(
            layout,
            Audio(
                audio_id=request_id,
                original_name=f"{request_id}.audio{recording.suffix}",
                owner="professor-a",
                received_at=now,
                delete_at=deadlines.purge_time(now),
                users=(request_id,),
            ),
        )
        save_job(layout, job)
        lifecycle.create_marker(layout, job)
        lifecycle.rewrite_queued_responses(layout, now)
    return request_id


def assert_done(layout: Layout, request_id: str) -> None:
    response = read_response(layout, request_id)
    assert response["state"] == "done"
    assert (layout.outbox / request_id / "result.json").is_file()


def assert_refused_when_received(layout: Layout, request_id: str) -> None:
    response = read_response(layout, request_id)
    assert response["state"] == "failed"
    assert response["failure"] == UNREADABLE
    assert list(layout.audio.iterdir()) == []
    assert not (layout.jobs / request_id).exists()


# ------------------------------------------------ US1-1: recordings the list covers read as before


@pytest.mark.parametrize(("extension", "muxer", "codec", "rate"), LISTED_FORMATS, ids=FORMAT_IDS)
def test_hardening_us1_1_the_listed_formats_read_the_same_with_the_decoder_list(
    reader_socket: ReaderSocket, tmp_path: Path, extension: str, muxer: str, codec: str, rate: int
) -> None:
    recording = encode_tone(tmp_path / f"tone.{extension}", muxer, codec, rate)

    assert_reads_the_same_with_the_list(reader_socket, recording)


def test_hardening_us1_1_wav_reads_the_same_with_the_decoder_list(reader_socket: ReaderSocket, tmp_path: Path) -> None:
    assert_reads_the_same_with_the_list(reader_socket, make_wav(tmp_path / "tone.wav", 2.0, silent=False))


@pytest.mark.parametrize(
    ("muxer", "codec", "extension"),
    [case[1:] for case in PCM_AND_ALAC],
    ids=[case[0] for case in PCM_AND_ALAC],
)
def test_hardening_us1_1_alac_and_the_pcm_variants_read_the_same_with_the_decoder_list(
    reader_socket: ReaderSocket, tmp_path: Path, muxer: str, codec: str, extension: str
) -> None:
    recording = encode_tone(tmp_path / f"tone.{extension}", muxer, codec)

    assert_reads_the_same_with_the_list(reader_socket, recording)


def assert_reads_the_same_with_the_list(reader_socket: ReaderSocket, recording: Path) -> None:
    (audio_stream,) = [name for kind, name in stream_kinds_and_codecs(recording) if kind == "audio"]
    assert audio_stream in AUDIO_DECODERS.split(",")
    assert read_in_process(recording, AUDIO_DECODERS) == read_in_process(recording, None)

    measured, decoded = probe(reader_socket, recording), decode(reader_socket, recording)

    assert measured.outcome == "ok" and decoded.outcome == "ok"
    duration, expected = read_in_process(recording, None)
    assert measured.duration_s == pytest.approx(duration, abs=0.001)
    assert bytes(decoded.audio) == expected
    assert len(expected) > 0


# ------------------------------------------------ US1-2 and edge 1: a video track is not opened


@pytest.mark.parametrize(("video_encoder", "video_decoder"), [("libx264", "h264"), ("mpeg4", "mpeg4")])
def test_hardening_us1_2_a_recording_with_a_video_track_reads_like_its_audio_alone(
    reader_socket: ReaderSocket, tmp_path: Path, video_encoder: str, video_decoder: str
) -> None:
    recording = make_mp4_with_video(tmp_path / "talk.m4a", video_codec=video_encoder)
    plain = audio_only_twin(tmp_path / "plain.m4a", "mp4", "aac")
    assert stream_kinds_and_codecs(recording) == [("audio", "aac"), ("video", video_decoder)]

    assert_reads_ok_like(reader_socket, recording, plain)
    video_streams_unopened(recording)


@pytest.mark.parametrize("video_encoder", ["libx264", "mpeg4"])
def test_hardening_edge_1_a_recording_with_a_video_track_is_received_and_transcribed(
    layout: Layout, clock: FakeClock, tmp_path: Path, video_encoder: str
) -> None:
    recording = make_mp4_with_video(tmp_path / "talk.m4a", video_codec=video_encoder)

    request_id = submit(layout, recording, clock)

    assert read_response(layout, request_id)["state"] == "queued"
    run_worker(layout, clock)
    assert_done(layout, request_id)


# ------------------------------------------------ US1-3: a cover picture is not opened


def test_hardening_us1_3_an_mp3_with_a_cover_picture_reads_like_its_audio_alone(
    reader_socket: ReaderSocket, tmp_path: Path
) -> None:
    recording = make_mp3_with_cover(tmp_path / "talk.mp3")
    plain = audio_only_twin(tmp_path / "plain.mp3", "mp3", "libmp3lame")
    assert stream_kinds_and_codecs(recording) == [("audio", "mp3float"), ("video", "png")]

    assert_reads_ok_like(reader_socket, recording, plain)
    video_streams_unopened(recording)


def test_hardening_us1_3_an_mp3_with_a_cover_picture_is_received_and_transcribed(
    layout: Layout, clock: FakeClock, tmp_path: Path
) -> None:
    request_id = submit(layout, make_mp3_with_cover(tmp_path / "talk.mp3"), clock)

    run_worker(layout, clock)

    assert_done(layout, request_id)


# ------------------------------------------------ US1-4: a recording with only a video track is refused


def test_hardening_us1_4_a_recording_with_only_a_video_track_is_refused_when_received(
    layout: Layout, clock: FakeClock, reader_socket: ReaderSocket, tmp_path: Path
) -> None:
    recording = make_mp4_video_only(tmp_path / "screen.m4a")
    assert stream_kinds_and_codecs(recording) == [("video", "h264")]

    request_id = submit(layout, recording, clock)

    assert_refused_when_received(layout, request_id)
    video_streams_unopened(recording)
    assert probe(reader_socket, recording).outcome == "failed"


# ------------------------------------------------ US1-5: an audio codec outside the list is refused

# The ``.m4a`` suffix is one the request schema allows; the Yamaha WAV is named like a WAV.
OUTSIDE_THE_LIST = {
    "yamaha-adpcm-wav": lambda path: make_adpcm_wav(path.with_suffix(".wav"), "adpcm_yamaha"),
    "quicktime-ima4-m4a": lambda path: make_mov_ima4(path.with_suffix(".m4a")),
}


@pytest.mark.parametrize("name", OUTSIDE_THE_LIST)
def test_hardening_us1_5_an_audio_codec_outside_the_list_is_refused_when_received(
    layout: Layout, clock: FakeClock, reader_socket: ReaderSocket, tmp_path: Path, name: str
) -> None:
    recording = OUTSIDE_THE_LIST[name](tmp_path / "talk")
    (audio_stream,) = [codec for kind, codec in stream_kinds_and_codecs(recording) if kind == "audio"]
    assert audio_stream not in AUDIO_DECODERS.split(",")

    request_id = submit(layout, recording, clock)

    assert_refused_when_received(layout, request_id)
    assert probe(reader_socket, recording).outcome == "failed"


@pytest.mark.parametrize("name", OUTSIDE_THE_LIST)
def test_hardening_us1_5_the_decode_child_ends_with_status_1_when_the_decoder_is_outside_the_list(
    tmp_path: Path, name: str
) -> None:
    import av

    recording = OUTSIDE_THE_LIST[name](tmp_path / "talk")
    with pytest.raises(av.error.ArgumentError):
        reader._decode(os.open(recording, os.O_RDONLY), os.open(os.devnull, os.O_WRONLY))

    fd = os.open(recording, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with open(os.devnull, "wb") as sink:
            run = subprocess.run(
                [sys.executable, "-I", "-m", "stt.server.reader", "child", "decode", str(fd), str(sink.fileno())],
                pass_fds=[fd, sink.fileno()],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=60,
                check=False,
            )
    finally:
        os.close(fd)
    assert run.returncode == 1
    assert run.stdout == b"" and run.stderr == b""


@pytest.mark.parametrize("name", OUTSIDE_THE_LIST)
def test_hardening_us1_5_a_job_accepted_before_the_list_fails_when_processed(
    layout: Layout, clock: FakeClock, tmp_path: Path, capfd: pytest.CaptureFixture[str], name: str
) -> None:
    recording = OUTSIDE_THE_LIST[name](tmp_path / "talk")
    request_id = place_accepted_job(layout, recording, clock)
    capfd.readouterr()

    assert run_worker_once(layout, clock) is True

    response = read_response(layout, request_id)
    assert response["state"] == "failed"
    assert response["failure"] == UNREADABLE
    assert not (layout.outbox / request_id).exists()
    assert capfd.readouterr().out.splitlines() == [f"{request_id} running", f"{request_id} failed unreadable_audio"]


def test_hardening_us1_5_the_name_check_reads_the_first_audio_stream_only() -> None:
    def container(*streams: Any) -> Any:
        return SimpleNamespace(streams=SimpleNamespace(audio=list(streams)))

    def stream(name: str | None) -> Any:
        if name is None:
            return SimpleNamespace(codec_context=None)
        return SimpleNamespace(codec_context=SimpleNamespace(codec=SimpleNamespace(name=name)))

    assert reader.first_audio_decoder_allowed(container()) is False
    assert reader.first_audio_decoder_allowed(container(stream(None))) is False
    assert reader.first_audio_decoder_allowed(container(stream("adpcm_yamaha"))) is False
    assert reader.first_audio_decoder_allowed(container(stream("h264"))) is False
    assert reader.first_audio_decoder_allowed(container(stream("aac"))) is True
    assert reader.first_audio_decoder_allowed(container(stream("aac"), stream("adpcm_yamaha"))) is True
    assert reader.first_audio_decoder_allowed(container(stream("adpcm_yamaha"), stream("aac"))) is False
    assert reader.first_audio_decoder_allowed(container(stream("pcm_s16le"))) is True


# ------------------------------------------------ edge 2: only the first of two audio streams is read


def test_hardening_edge_2_two_audio_streams_read_the_first_one_only(reader_socket: ReaderSocket, tmp_path: Path) -> None:
    recording = make_mp4_with_two_audio_streams(tmp_path / "talk.m4a")
    first_only = audio_only_twin(tmp_path / "first.m4a", "mp4", "aac")
    assert stream_kinds_and_codecs(recording) == [("audio", "aac"), ("audio", "aac")]

    assert_reads_ok_like(reader_socket, recording, first_only)


# ------------------------------------------------ recorder codecs the list adds

RECORDER_CODECS = {
    "ima-adpcm-wav": lambda path: make_adpcm_wav(path.with_suffix(".wav"), "adpcm_ima_wav"),
    "ms-adpcm-wav": lambda path: make_adpcm_wav(path.with_suffix(".wav"), "adpcm_ms"),
    "mp2-mp3": lambda path: make_mp2(path.with_suffix(".mp3")),
    "mp2-wav": lambda path: make_mp2(path.with_suffix(".wav")),
    "ac3-m4a": lambda path: make_mp4_with_dolby_audio(path.with_suffix(".m4a"), "ac3"),
    "eac3-m4a": lambda path: make_mp4_with_dolby_audio(path.with_suffix(".m4a"), "eac3"),
    "amr-nb-m4a": lambda path: make_amr_nb_3gp(path.with_suffix(".m4a")),
}


@pytest.mark.parametrize("name", RECORDER_CODECS)
def test_hardening_us1_the_recorder_codecs_of_the_list_read_through_the_reader(
    reader_socket: ReaderSocket, tmp_path: Path, name: str
) -> None:
    recording = RECORDER_CODECS[name](tmp_path / "talk")

    measured, decoded = probe(reader_socket, recording), decode(reader_socket, recording)

    assert measured.outcome == "ok" and decoded.outcome == "ok"
    assert measured.duration_s == pytest.approx(2.0, abs=0.2)
    assert_is_the_two_second_tone(bytes(decoded.audio))


# ------------------------------------------------ FR-005: the command line keeps every decoder


def test_hardening_fr_5_the_command_line_decodes_a_codec_outside_the_list(tmp_path: Path) -> None:
    recording = make_adpcm_wav(tmp_path / "talk.wav", "adpcm_yamaha")
    reported: list[float] = []

    decoded = audio_decode.decode_samples(recording, on_early_stop=reported.append)

    assert decoded.dtype == np.float32
    assert abs(len(decoded) - TWO_SECONDS_OF_SAMPLES) <= 2400
    assert 0.25 < float(np.abs(decoded).max()) < 0.35
    assert reported == []


# ------------------------------------------------ FR-001: the list holds audio decoders only


def test_hardening_fr_1_every_name_on_the_list_is_an_installed_audio_decoder() -> None:
    import av

    names = AUDIO_DECODERS.split(",")

    assert len(names) == len(set(names)) == 32
    assert all(av.codec.Codec(name, "r").type == "audio" for name in names)
