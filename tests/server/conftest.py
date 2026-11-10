"""Shared helpers and fixtures for the server transcription tests.

Production modules (``stt.server.*``) are imported inside helpers and
fixtures so that test collection works before those modules exist.
"""

from __future__ import annotations

import array
import dataclasses
import json
import math
import os
import select
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

if TYPE_CHECKING:
    from stt.server.reader_client import ReaderConfig

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")
FAKE_READER = Path(__file__).with_name("fake_reader.py")
READER_HARNESS = Path(__file__).with_name("reader_harness.py")
MESSAGE_LIMIT = 4096
MAX_PEEKED_FDS = 16
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Returns an empty state root directory."""
    state_root = tmp_path / "stt"
    state_root.mkdir()
    return state_root


class FakeClock:
    """An injectable UTC clock that only moves when told to."""

    def __init__(self, start: str = "2026-03-02T01:00:00Z") -> None:
        self._now = datetime.strptime(start, _TIME_FORMAT).replace(tzinfo=timezone.utc)

    def now(self) -> datetime:
        """Returns the current fake time (UTC aware)."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Moves the clock forward."""
        self._now += timedelta(seconds=seconds)

    def set(self, iso: str) -> None:
        """Sets the clock to a ``YYYY-MM-DDTHH:MM:SSZ`` time."""
        self._now = datetime.strptime(iso, _TIME_FORMAT).replace(tzinfo=timezone.utc)


def set_to_ctime_plus(clock: FakeClock, path: Path, minutes: float) -> None:
    """Moves `clock` to `minutes` after the real inode change time of `path`.

    A file's ``st_ctime`` cannot be chosen by a test, so the clock follows it.

    Args:
        clock: The injected clock.
        path: A file or folder that exists.
        minutes: Offset from the file's ``st_ctime``.
    """
    changed = datetime.fromtimestamp(os.lstat(path).st_ctime, timezone.utc)
    clock.set((changed + timedelta(minutes=minutes)).strftime(_TIME_FORMAT))


def make_wav(path: Path, seconds: float, silent: bool = True) -> Path:
    """Writes a 16 kHz mono 16-bit WAV file.

    Args:
        path: Destination file.
        seconds: Duration in seconds.
        silent: True for silence, False for a 440 Hz tone.

    Returns:
        The written path.
    """
    rate = 16000
    frames = int(rate * seconds)
    if silent:
        data = bytes(2 * frames)
    else:
        data = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(frames)
        )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data)
    return path


def encode_tone(path: Path, container: str, codec: str, rate: int = 16000, seconds: float = 2.0) -> Path:
    """Writes a mono 440 Hz tone with PyAV.

    Args:
        path: Destination file.
        container: FFmpeg muxer name, such as ``ipod`` or ``adts``.
        codec: FFmpeg encoder name, such as ``aac``; encoded at 64 kbit/s where the codec takes a bit rate.
        rate: Sample rate in Hz.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av
    import numpy as np

    samples = int(rate * seconds)
    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(samples) / rate)).astype(np.float32)
    with av.open(str(path), "w", format=container) as output:
        stream = output.add_stream(codec, rate=rate, layout="mono")
        stream.bit_rate = 64000
        frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    return path


# ------------------------------------------------ test recordings made with PyAV
# Every recording is a sine tone, silence or a solid colour (and truncated copies of those);
# nothing here builds or keeps a file meant to exploit a decoder.

RECORDING_SECONDS = 2.0
_PICTURE_SIZE = 64
_SOLID_COLOUR = (0, 128, 255)


def _require_encoder(name: str) -> None:
    """Skips the calling test when the installed PyAV has no encoder called `name`."""
    import av

    try:
        av.Codec(name, "w")
    except ValueError:
        pytest.skip(f"the installed PyAV has no {name} encoder")


class _Track(NamedTuple):
    """A stream added to an output container and the packets encoded for it so far."""

    stream: Any
    packets: list[Any]


def _add_tone_stream(output: Any, codec: str, rate: int, freq: float, seconds: float) -> _Track:
    """Adds a mono audio stream to `output` and encodes a `freq` Hz tone for it (nothing is muxed yet)."""
    import av
    import numpy as np

    stream = output.add_stream(codec, rate=rate, layout="mono")
    if codec in ("aac", "libmp3lame", "mp2", "ac3", "eac3"):
        stream.bit_rate = 64000
    samples = int(rate * seconds)
    tone = (0.3 * np.sin(2 * np.pi * freq * np.arange(samples) / rate)).astype(np.float32)
    frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="flt", layout="mono")
    frame.sample_rate = rate
    return _Track(stream, list(stream.encode(frame)))


def _add_solid_video_stream(output: Any, codec: str, seconds: float, fps: int = 10) -> _Track:
    """Adds a solid-colour video stream to `output` and encodes its frames (nothing is muxed yet)."""
    import av
    import numpy as np

    stream = output.add_stream(codec, rate=fps)
    stream.width = _PICTURE_SIZE
    stream.height = _PICTURE_SIZE
    stream.pix_fmt = "yuv420p"
    image = np.full((_PICTURE_SIZE, _PICTURE_SIZE, 3), _SOLID_COLOUR, dtype=np.uint8)
    packets: list[Any] = []
    for index in range(int(seconds * fps)):
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        frame.pts = index
        packets.extend(stream.encode(frame))
    return _Track(stream, packets)


def _add_solid_cover_stream(output: Any) -> _Track:
    """Adds one solid-colour PNG picture as an attached-picture stream and encodes it (nothing is muxed yet)."""
    import av
    import numpy as np

    stream = output.add_stream("png", rate=1)
    stream.width = _PICTURE_SIZE
    stream.height = _PICTURE_SIZE
    stream.pix_fmt = "rgb24"
    stream.disposition = av.stream.Disposition.attached_pic
    image = np.full((_PICTURE_SIZE, _PICTURE_SIZE, 3), _SOLID_COLOUR, dtype=np.uint8)
    frame = av.VideoFrame.from_ndarray(image, format="rgb24")
    frame.pts = 0
    return _Track(stream, list(stream.encode(frame)))


def _finish(output: Any, tracks: list[_Track]) -> None:
    """Flushes every encoder and muxes all packets, track by track.

    Every stream must be added before the first packet is muxed: the container header is written then.
    """
    for track in tracks:
        for packet in [*track.packets, *track.stream.encode(None)]:
            output.mux(packet)


def stream_kinds_and_codecs(path: Path) -> list[tuple[str, str]]:
    """Lists the streams of a recording as ``(kind, decoder name)`` pairs, in stream order.

    Args:
        path: An existing recording.

    Returns:
        For example ``[("audio", "aac"), ("video", "h264")]``; the PNG cover of an MP3 is ``("video", "png")``.
    """
    import av

    with av.open(str(path)) as container:
        return [(stream.type, stream.codec_context.name) for stream in container.streams]


def make_mp4_with_video(path: Path, video_codec: str = "libx264", seconds: float = RECORDING_SECONDS) -> Path:
    """Writes an MP4 with a 440 Hz AAC tone and a solid-colour video stream.

    Container: MP4. Streams: audio AAC (16 kHz mono), video `video_codec`.

    Args:
        path: Destination file.
        video_codec: FFmpeg video encoder, ``libx264`` (decodes as H.264) or ``mpeg4``.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    with av.open(str(path), "w", format="mp4") as output:
        audio = _add_tone_stream(output, "aac", 16000, 440.0, seconds)
        video = _add_solid_video_stream(output, video_codec, seconds)
        _finish(output, [audio, video])
    return path


def make_mp3_with_cover(path: Path, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes an MP3 with a 440 Hz tone and one solid-colour PNG cover picture.

    Container: MP3. Streams: audio MP3 (``libmp3lame``, 16 kHz mono), then one PNG video stream
    flagged ``attached_pic``.

    Args:
        path: Destination file.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    with av.open(str(path), "w", format="mp3") as output:
        audio = _add_tone_stream(output, "libmp3lame", 16000, 440.0, seconds)
        cover = _add_solid_cover_stream(output)
        _finish(output, [audio, cover])
    return path


def make_mp4_video_only(path: Path, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes an MP4 that holds only a solid-colour H.264 video stream (no audio stream).

    Container: MP4. Streams: one video stream, H.264.

    Args:
        path: Destination file.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    with av.open(str(path), "w", format="mp4") as output:
        video = _add_solid_video_stream(output, "libx264", seconds)
        _finish(output, [video])
    return path


def make_faststart_m4a(path: Path, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes an M4A whose index (``moov``) comes before the media data (``mdat``).

    Container: MP4 (``movflags=+faststart``). Streams: audio AAC (16 kHz mono, 440 Hz tone).

    Args:
        path: Destination file.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    with av.open(str(path), "w", format="mp4", options={"movflags": "+faststart"}) as output:
        audio = _add_tone_stream(output, "aac", 16000, 440.0, seconds)
        _finish(output, [audio])
    return path


def make_mp4_with_two_audio_streams(path: Path, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes an MP4 with two AAC audio streams: a 440 Hz tone first, an 880 Hz tone second.

    Container: MP4. Streams: audio AAC (16 kHz mono) twice.

    Args:
        path: Destination file.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    with av.open(str(path), "w", format="mp4") as output:
        first = _add_tone_stream(output, "aac", 16000, 440.0, seconds)
        second = _add_tone_stream(output, "aac", 16000, 880.0, seconds)
        _finish(output, [first, second])
    return path


def make_adpcm_wav(path: Path, codec: str, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes a WAV holding a 440 Hz tone in an ADPCM codec.

    Container: WAV. Streams: audio `codec` (16 kHz mono).

    Args:
        path: Destination file.
        codec: FFmpeg encoder, ``adpcm_ima_wav`` or ``adpcm_ms`` (the listed ones), or ``adpcm_yamaha``
            (not on the decoder list).
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    _require_encoder(codec)
    with av.open(str(path), "w", format="wav") as output:
        audio = _add_tone_stream(output, codec, 16000, 440.0, seconds)
        _finish(output, [audio])
    return path


def make_mp2(path: Path, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes an MPEG Layer II recording; the suffix of `path` picks the container.

    Container: raw MP2 for ``.mp3``, WAV for ``.wav``. Streams: audio MP2 (16 kHz mono, 440 Hz tone).

    Args:
        path: Destination file, ending in ``.mp3`` or ``.wav``.
        seconds: Duration in seconds.

    Returns:
        The written path.

    Raises:
        ValueError: If the suffix is neither ``.mp3`` nor ``.wav``.
    """
    import av

    containers = {".mp3": "mp2", ".wav": "wav"}
    if path.suffix not in containers:
        raise ValueError("an MP2 test recording needs a .mp3 or .wav suffix")
    _require_encoder("mp2")
    with av.open(str(path), "w", format=containers[path.suffix]) as output:
        audio = _add_tone_stream(output, "mp2", 16000, 440.0, seconds)
        _finish(output, [audio])
    return path


def make_mp4_with_dolby_audio(path: Path, codec: str, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes an MP4 holding a 440 Hz tone in AC-3 or E-AC-3.

    Container: MP4. Streams: audio `codec` (48 kHz mono).

    Args:
        path: Destination file.
        codec: FFmpeg encoder, ``ac3`` or ``eac3``.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    _require_encoder(codec)
    with av.open(str(path), "w", format="mp4") as output:
        audio = _add_tone_stream(output, codec, 48000, 440.0, seconds)
        _finish(output, [audio])
    return path


def make_amr_nb_3gp(path: Path, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes a 3GP holding a 440 Hz tone in AMR-NB; skips the test when PyAV has no encoder for it.

    Container: 3GP (``3gp`` muxer; give `path` an ``.m4a`` suffix to test an M4A name). Streams: audio
    AMR-NB (8 kHz mono). Only PyAV 17 and 18 ship the ``libopencore_amrnb`` encoder.

    Args:
        path: Destination file.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    _require_encoder("libopencore_amrnb")
    with av.open(str(path), "w", format="3gp") as output:
        audio = _add_tone_stream(output, "libopencore_amrnb", 8000, 440.0, seconds)
        _finish(output, [audio])
    return path


def make_mov_ima4(path: Path, seconds: float = RECORDING_SECONDS) -> Path:
    """Writes a QuickTime MOV holding a 440 Hz tone in QuickTime IMA ADPCM (not on the decoder list).

    Container: MOV (give `path` an ``.m4a`` suffix to test an M4A name). Streams: audio ``adpcm_ima_qt``
    (16 kHz mono).

    Args:
        path: Destination file.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av

    _require_encoder("adpcm_ima_qt")
    with av.open(str(path), "w", format="mov") as output:
        audio = _add_tone_stream(output, "adpcm_ima_qt", 16000, 440.0, seconds)
        _finish(output, [audio])
    return path


def truncate_to_ratio(path: Path, ratio: float) -> Path:
    """Cuts the end off a file so that `ratio` of its bytes remain.

    Args:
        path: An existing file; changed in place.
        ratio: Fraction of the bytes to keep, strictly between 0 and 1.

    Returns:
        `path`.

    Raises:
        ValueError: If `ratio` is not strictly between 0 and 1.
    """
    if not 0 < ratio < 1:
        raise ValueError("ratio must be strictly between 0 and 1")
    os.truncate(path, int(path.stat().st_size * ratio))
    return path


_MDAT_HEADER_BYTES = 8


def truncate_after_mdat_header(path: Path) -> Path:
    """Cuts an MP4/M4A just after the 8-byte header of its ``mdat`` box, so no media data remains.

    Meant for an M4A whose index (``moov``) comes first (see `make_faststart_m4a`): the index stays
    whole while every audio packet is gone.

    Args:
        path: An existing MP4/M4A with a 32-bit-size ``mdat`` box; changed in place.

    Returns:
        `path`.

    Raises:
        ValueError: If the file has no top-level ``mdat`` box with a 32-bit size field.
    """
    data = path.read_bytes()
    offset = 0
    while offset + _MDAT_HEADER_BYTES <= len(data):
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        if data[offset + 4 : offset + 8] == b"mdat":
            if size < _MDAT_HEADER_BYTES:
                raise ValueError("the mdat box has no 32-bit size field")
            os.truncate(path, offset + _MDAT_HEADER_BYTES)
            return path
        if size < _MDAT_HEADER_BYTES:
            break
        offset += size
    raise ValueError("no top-level mdat box found")


# faster-whisper's decode_audio passes metadata_errors, which PyAV 19 removed.
FASTER_WHISPER_DECODER_MAX_AV_MAJOR = 18


def av_major() -> int:
    """Returns the major version of the installed PyAV."""
    import av

    return int(av.__version__.split(".")[0])


def wav_samples(path: Path) -> bytes:
    """Returns the 16-bit little-endian sample bytes stored in a WAV file."""
    with wave.open(str(path), "rb") as handle:
        return handle.readframes(handle.getnframes())


def new_id() -> str:
    """Returns a new lowercase UUID string."""
    return str(uuid.uuid4())


def _write_file(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(path, mode)


def _publish_request(inbox: Path, request_id: str, body: dict[str, Any], mode: int) -> Path:
    tmp = inbox / f"{request_id}.json.tmp"
    final = inbox / f"{request_id}.json"
    _write_file(tmp, json.dumps(body).encode("utf-8"), mode)
    os.replace(tmp, final)
    return final


def write_transcribe_request(
    inbox: Path,
    *,
    request_id: str,
    requested_by: str,
    attachment: Path | None,
    from_request: str | None = None,
    language: str = "ko",
    diarize: bool = False,
    hints: list[str] | None = None,
    mode: int = 0o640,
) -> Path:
    """Hands over a transcribe request the way the contract prescribes.

    The attachment is copied to ``<request_id>.audio.<ext>`` first, then the
    request is written as ``.json.tmp`` and renamed.

    Args:
        inbox: The inbox directory.
        request_id: Lowercase UUID of the request.
        requested_by: Opaque requester marker.
        attachment: Local recording to hand over, or None.
        from_request: Earlier request whose recording is reused, or None.
        language: Language code.
        diarize: Whether to separate speakers.
        hints: Optional glossary terms, written in the given order.
        mode: File mode of the request and the attachment.

    Returns:
        The final request path ``<inbox>/<request_id>.json``.
    """
    audio: dict[str, str] = {}
    if attachment is not None:
        name = f"{request_id}.audio{attachment.suffix}"
        target = inbox / name
        shutil.copyfile(attachment, target)
        os.chmod(target, mode)
        audio["file"] = name
    if from_request is not None:
        audio["from_request"] = from_request
    body: dict[str, Any] = {
        "schema_version": 1,
        "kind": "transcribe",
        "request_id": request_id,
        "requested_by": requested_by,
        "audio": audio,
        "language": language,
        "diarize": diarize,
    }
    if hints is not None:
        body["hints"] = hints
    return _publish_request(inbox, request_id, body, mode)


def write_delete_request(
    inbox: Path,
    *,
    request_id: str,
    requested_by: str,
    target: str,
    mode: int = 0o640,
) -> Path:
    """Hands over a delete request, which deletes everything (``.json.tmp``, then rename).

    Args:
        inbox: The inbox directory.
        request_id: Lowercase UUID of this delete request.
        requested_by: Opaque requester marker.
        target: ``request_id`` of the transcribe request whose recording and jobs go.
        mode: File mode of the request.

    Returns:
        The final request path ``<inbox>/<request_id>.json``.
    """
    body = {
        "schema_version": 1,
        "kind": "delete",
        "request_id": request_id,
        "requested_by": requested_by,
        "target": target,
    }
    return _publish_request(inbox, request_id, body, mode)


def leftovers(root: Path) -> list[Path]:
    """Lists everything under a state root that should not outlive its job.

    Covers recording folders ``audio/<id>/``, anything under ``work/`` and
    ``queue/``, temporary ``.*.tmp`` files and folders anywhere (including
    ``outbox/.*.tmp/``), ``*.wav`` files, and job records that still hold
    ``hints``.

    Args:
        root: The state root directory.

    Returns:
        Sorted paths of the leftovers; empty when nothing remains.
    """
    found: set[Path] = set()
    audio = root / "audio"
    if audio.is_dir():
        found.update(p for p in audio.iterdir() if p.is_dir())
    for name in ("work", "queue"):
        folder = root / name
        if folder.is_dir():
            found.update(folder.rglob("*"))
    found.update(root.rglob(".*.tmp"))
    found.update(root.rglob("*.wav"))
    jobs = root / "jobs"
    if jobs.is_dir():
        for record in jobs.rglob("*.json"):
            data = json.loads(record.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("hints") is not None:
                found.add(record)
    return sorted(found)


def leftovers_besides_recordings(root: Path) -> list[Path]:
    """Lists the leftovers outside ``audio/``; a recording stays until a delete request or the daily purge.

    Args:
        root: The state root directory.

    Returns:
        Sorted paths of the leftovers not under ``audio/``.
    """
    audio = root / "audio"
    return [path for path in leftovers(root) if audio not in path.parents]


_active_reader_socket: ReaderSocket | None = None


def _current_reader_config() -> ReaderConfig:
    """Returns the active ``reader_socket`` fixture's config as it is now, so ``use_missing`` and ``use_limits`` apply."""
    if _active_reader_socket is None:
        raise RuntimeError("no reader_socket fixture is active")
    return _active_reader_socket.config


def _engine_cmd(mode: str) -> list[str]:
    return [sys.executable, str(FAKE_ENGINE), "--mode", mode]


def _worker_kwargs(layout: Any, clock: FakeClock, mode: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    state_root = layout.root
    models_dir = state_root.parent / f"{state_root.name}-models"
    models_dir.mkdir(exist_ok=True)
    gpu_lock = state_root.parent / f"{state_root.name}-gpu.lock"
    gpu_lock.touch()
    options: dict[str, Any] = {
        "engine_cmd": _engine_cmd(mode),
        "now": clock.now,
        "poll_s": 0.05,
        "models_dir": models_dir,
        "gpu_lock": gpu_lock,
        "reader": _current_reader_config(),
    }
    options.update(kwargs)
    return options


def run_worker(layout: Any, clock: FakeClock, mode: str = "done", **kwargs: Any) -> int:
    """Runs ``worker.run`` with the fake engine, holding ``worker.lock`` like the CLI.

    The reader is the active ``reader_socket`` fixture's config, read now; ``reader=`` in `kwargs` overrides it.

    Args:
        layout: A ``stt.server.layout.Layout``.
        clock: The injected clock.
        mode: Fake engine mode (``done``, ``fail=<reason>``, ``sleep=<s>``, ``die``).
        **kwargs: Overrides and extra arguments for ``worker.run``.

    Returns:
        What ``worker.run`` returns.
    """
    from stt.server import worker
    from stt.server.layout import hold

    options = _worker_kwargs(layout, clock, mode, kwargs)
    with hold(layout.worker_lock):
        return worker.run(layout, options.pop("engine_cmd"), options.pop("now"), **options)


def run_worker_once(layout: Any, clock: FakeClock, mode: str = "done", **kwargs: Any) -> bool:
    """Runs ``worker.run_one`` with the fake engine, holding ``worker.lock`` like the CLI.

    The reader is the active ``reader_socket`` fixture's config, read now; ``reader=`` in `kwargs` overrides it.

    Args:
        layout: A ``stt.server.layout.Layout``.
        clock: The injected clock.
        mode: Fake engine mode (``done``, ``fail=<reason>``, ``sleep=<s>``, ``die``).
        **kwargs: Overrides and extra arguments for ``worker.run_one``.

    Returns:
        What ``worker.run_one`` returns.
    """
    from stt.server import worker
    from stt.server.layout import hold

    options = _worker_kwargs(layout, clock, mode, kwargs)
    with hold(layout.worker_lock):
        return worker.run_one(layout, options.pop("engine_cmd"), options.pop("now"), **options)


def run_sweep(layout: Any, clock: FakeClock) -> int:
    """Runs ``sweep.run_once``, holding ``sweep.lock`` like the CLI.

    Args:
        layout: A ``stt.server.layout.Layout``.
        clock: The injected clock.

    Returns:
        What ``sweep.run_once`` returns: the number of items it could not clean.
    """
    from stt.server import sweep
    from stt.server.layout import hold

    with hold(layout.sweep_lock):
        return sweep.run_once(layout, clock.now)


@pytest.fixture
def gpu_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes the engine's start-up check see one CUDA device."""
    monkeypatch.setattr("stt.server.engine.cuda_device_count", lambda *a, **k: 1)
    monkeypatch.setattr("stt.server.engine.torch_cuda_available", lambda *a, **k: True)


@pytest.fixture
def pinned_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Writes the pinned model refs under a temporary ``HF_HOME`` and points to it.

    Returns:
        The temporary ``HF_HOME`` directory.
    """
    from stt.server.models import PINNED

    hf_home = tmp_path / "hf_home"
    for repo, commit in PINNED:
        org, name = repo.split("/", 1)
        refs = hf_home / "hub" / f"models--{org}--{name}" / "refs"
        refs.mkdir(parents=True)
        (refs / "main").write_text(commit, encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(hf_home))
    return hf_home


def fake_reader_cmd(mode: str) -> list[str]:
    """Builds the command that runs ``fake_reader.py`` in `mode`.

    Args:
        mode: One of ``no-start``, ``not-started`` or ``no-verdict``.

    Returns:
        An argument list for ``EmulatedReaderSocket``.
    """
    return [sys.executable, str(FAKE_READER), "--mode", mode]


class ReceivedFd(NamedTuple):
    """Identity of one descriptor a caller sent with its request.

    Attributes:
        kind: ``stat.S_IFMT`` of the file, e.g. ``stat.S_IFREG`` or ``stat.S_IFIFO``.
        dev: ``st_dev`` of the file.
        ino: ``st_ino`` of the file.
    """

    kind: int
    dev: int
    ino: int


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


class EmulatedReaderSocket:
    """A temporary SEQPACKET socket that mirrors systemd ``Accept=yes``.

    For every accepted connection it peeks at the request (``MSG_PEEK``, so the
    message and its descriptors stay queued for the spawned program), records
    the identity of the descriptors, starts a new process from `cmd` in its own
    session with the connection as standard input (argument list, no shell,
    stdout to ``/dev/null``) and closes its own copy. The peek waits at most
    `peek_timeout_s` for the request; a connection that sends nothing in time
    is recorded as None and spawned anyway. Peeking is serial, so a silent
    client delays the next accept by that long.

    Because the record comes from the fixture, it works for any spawned
    program, the real supervisor included.

    Attributes:
        path: Socket path to hand to the caller under test.
    """

    def __init__(self, cmd: list[str], peek_timeout_s: float = 1.0) -> None:
        # AF_UNIX paths are limited to ~107 bytes, which pytest's tmp_path can exceed.
        self._dir = Path(tempfile.mkdtemp(prefix="stt-rd-"))
        self._cmd = list(cmd)
        self._peek_timeout_s = peek_timeout_s
        self._lock = threading.Lock()
        self._procs: list[subprocess.Popen[bytes]] = []
        self._received: list[tuple[ReceivedFd, ...] | None] = []
        self._error: BaseException | None = None
        self._stop = threading.Event()
        self._closed = False
        self.path = self._dir / "reader.sock"
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self._listener.bind(str(self.path))
            self._listener.listen()
        except BaseException:
            self._listener.close()
            shutil.rmtree(self._dir, ignore_errors=True)
            raise
        self._listener.settimeout(0.05)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _peek(self, connection: socket.socket) -> tuple[ReceivedFd, ...] | None:
        if not select.select([connection], [], [], self._peek_timeout_s)[0]:
            return None
        data, ancillary, flags, _ = connection.recvmsg(
            MESSAGE_LIMIT, socket.CMSG_SPACE(MAX_PEEKED_FDS * 4), socket.MSG_PEEK | socket.MSG_CMSG_CLOEXEC
        )
        fds = array.array("i")
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds.frombytes(payload[: len(payload) - len(payload) % fds.itemsize])
        identities: list[ReceivedFd] = []
        try:
            for fd in fds:
                info = os.fstat(fd)
                identities.append(ReceivedFd(stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino))
        finally:
            for fd in fds:
                os.close(fd)
        if flags & socket.MSG_CTRUNC:
            raise AssertionError("request carried more descriptors than the fixture can record")
        if not data and not identities:
            return None
        return tuple(identities)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                try:
                    peeked = self._peek(connection)
                    with self._lock:
                        cmd = list(self._cmd)
                    proc = subprocess.Popen(
                        cmd,
                        stdin=connection,
                        stdout=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except BaseException as exc:
                    self._error = exc
                    return
                with self._lock:
                    self._received.append(peeked)
                    self._procs.append(proc)

    def _raise_serve_error(self) -> None:
        if self._error is not None:
            raise AssertionError("emulated reader socket failed to serve a connection") from self._error

    def set_command(self, cmd: list[str]) -> None:
        """Makes every connection accepted from now on run `cmd`.

        Args:
            cmd: Argument list of the program to start per connection.
        """
        with self._lock:
            self._cmd = list(cmd)

    @property
    def spawn_count(self) -> int:
        """Returns how many reader processes have been started."""
        with self._lock:
            return len(self._procs)

    def received(self) -> list[tuple[ReceivedFd, ...] | None]:
        """Returns, per spawned process, the descriptors its request carried.

        An entry is None when the client sent no request before the peek timed
        out or hung up.
        """
        with self._lock:
            return list(self._received)

    def fd_counts(self) -> list[int | None]:
        """Returns, per spawned process, how many descriptors its request carried."""
        return [None if fds is None else len(fds) for fds in self.received()]

    def _all_gone(self) -> bool:
        with self._lock:
            procs = list(self._procs)
        return all(proc.poll() is not None and not _group_alive(proc.pid) for proc in procs)

    def wait_for_exits(self, count: int, timeout_s: float = 5.0) -> None:
        """Waits until `count` processes were started and their process groups are empty.

        Args:
            count: Expected number of started processes.
            timeout_s: Longest wait.

        Raises:
            AssertionError: If that state is not reached in time, or serving failed.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._raise_serve_error()
            if self.spawn_count >= count and self._all_gone():
                return
            time.sleep(0.01)
        self._raise_serve_error()
        raise AssertionError(f"reader processes did not settle: spawned={self.spawn_count}, expected={count}")

    def close(self, grace_s: float = 5.0) -> None:
        """Stops listening, reaps the processes and asserts none was left running.

        A process counts as left running when it, or anything else in its
        process group (e.g. a decoding child), is alive `grace_s` after the
        listener stopped. Such groups are killed with SIGKILL.

        Args:
            grace_s: How long a process group may take to end by itself.

        Raises:
            AssertionError: If a process group was still running after `grace_s`,
                or serving a connection had failed.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join()
        self._listener.close()
        with self._lock:
            procs = list(self._procs)
        deadline = time.monotonic() + grace_s
        running = 0
        for proc in procs:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            while proc.poll() is None or _group_alive(proc.pid):
                if time.monotonic() >= deadline:
                    running += 1
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    while _group_alive(proc.pid):
                        time.sleep(0.01)
                    break
                time.sleep(0.01)
        shutil.rmtree(self._dir, ignore_errors=True)
        self._raise_serve_error()
        assert running == 0, f"{running} reader process group(s) still running at teardown"


@pytest.fixture
def emulated_reader_socket() -> Iterator[Callable[..., EmulatedReaderSocket]]:
    """Returns a factory for opt-in emulated reader sockets.

    ``factory(cmd, peek_timeout_s=1.0)`` starts a socket that runs `cmd` once
    per connection; the result exposes ``path``, ``spawn_count``,
    ``received()``, ``fd_counts()`` and ``wait_for_exits()``. At teardown every
    socket is closed and the test fails if a spawned process (or anything in
    its process group) is still running.
    """
    created: list[EmulatedReaderSocket] = []

    def factory(cmd: list[str], peek_timeout_s: float = 1.0) -> EmulatedReaderSocket:
        emulated = EmulatedReaderSocket(cmd, peek_timeout_s)
        created.append(emulated)
        return emulated

    yield factory
    errors: list[str] = []
    for emulated in created:
        try:
            emulated.close()
        except AssertionError as exc:
            errors.append(str(exc))
    if errors:
        raise AssertionError("; ".join(errors))


class ReaderSocket:
    """An emulated reader socket that, unless told otherwise, runs the real supervisor.

    Every connection starts ``python -m stt server read`` with the connection as
    standard input, as systemd does for ``stt-reader@.service``. A test that needs
    another reader chooses the command *before* running a stage with one of the
    ``use_*`` methods; the choice applies to the connections accepted afterwards.

    Attributes:
        config: A ``reader_client.ReaderConfig`` for this socket's path, to hand to the
            code under test.
    """

    def __init__(self, emulated: EmulatedReaderSocket, config: ReaderConfig) -> None:
        self._emulated = emulated
        self.config = config

    @property
    def path(self) -> Path:
        """Returns the socket path."""
        return self._emulated.path

    @property
    def spawn_count(self) -> int:
        """Returns how many reader processes have been started."""
        return self._emulated.spawn_count

    def received(self) -> list[tuple[ReceivedFd, ...] | None]:
        """Returns, per started process, the descriptors its request carried."""
        return self._emulated.received()

    def fd_counts(self) -> list[int | None]:
        """Returns, per started process, how many descriptors its request carried."""
        return self._emulated.fd_counts()

    def wait_for_exits(self, count: int, timeout_s: float = 5.0) -> None:
        """Waits until `count` processes were started and their process groups are empty.

        Raises:
            AssertionError: If that state is not reached in time.
        """
        self._emulated.wait_for_exits(count, timeout_s)

    def use_command(self, cmd: list[str]) -> None:
        """Runs `cmd` instead of the real supervisor for the connections accepted from now on."""
        self._emulated.set_command(cmd)

    def use_fake(self, mode: str) -> None:
        """Runs ``fake_reader.py`` in `mode` (``no-start``, ``not-started`` or ``no-verdict``)."""
        self._emulated.set_command(fake_reader_cmd(mode))

    def use_missing(self) -> None:
        """Points ``config`` at a socket path where nothing listens, as if the reader unit were not there.

        Code under test must read ``config`` when it is called, not copy it earlier.
        """
        self.config = dataclasses.replace(self.config, socket_path=str(self.path.with_name("missing.sock")))

    def use_limits(self, **changes: float) -> None:
        """Replaces fields of ``config``, e.g. ``decode_limit_s=0.5`` or ``start_timeout_s=1.0``.

        Code under test must read ``config`` when it is called, not copy it earlier.

        Args:
            **changes: ``ReaderConfig`` field names with their new values.
        """
        self.config = dataclasses.replace(self.config, **changes)

    def use_supervisor(self, child_command: list[str]) -> None:
        """Runs the real supervisor with a decode-child double.

        Args:
            child_command: The child command without the operation and descriptors, which the
                supervisor appends, e.g. ``[sys.executable, str(FAKE_DECODE_CHILD), "--mode", "hang"]``.
        """
        self._emulated.set_command(
            [sys.executable, str(READER_HARNESS), "--child-cmd", json.dumps(child_command)]
        )


@pytest.fixture(autouse=True)
def reader_socket(emulated_reader_socket: Callable[..., EmulatedReaderSocket]) -> Iterator[ReaderSocket]:
    """Starts an emulated reader socket whose default spawn command is the real supervisor.

    Autouse: every server test has one, and ``run_worker`` and ``run_worker_once`` hand its ``config``
    to the worker. A test that wants to inspect or change it asks for it by name. Its ``config`` is for
    the caller under test, and ``use_command``, ``use_fake`` and ``use_supervisor`` swap the spawned program.
    """
    from stt.server.reader_client import ReaderConfig

    global _active_reader_socket
    emulated = emulated_reader_socket([sys.executable, "-m", "stt", "server", "read"])
    socket_under_test = ReaderSocket(emulated, ReaderConfig(socket_path=str(emulated.path), poll_s=0.05))
    _active_reader_socket = socket_under_test
    yield socket_under_test
    _active_reader_socket = None


@pytest.fixture(autouse=True)
def intake_reads_through_reader_socket(reader_socket: ReaderSocket, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hands ``reader_socket.config`` to ``intake.run_once`` calls that give no ``reader``.

    The config is read when ``run_once`` is called, so ``use_missing`` and ``use_limits`` apply. A ``reader``
    the test passes wins.
    """
    from stt.server import intake

    real_run_once = intake.run_once

    def run_once(layout: Any, now: Callable[[], datetime], reader: ReaderConfig | None = None) -> int:
        return real_run_once(layout, now, reader_socket.config if reader is None else reader)

    monkeypatch.setattr(intake, "run_once", run_once)


# ------------------------------------------------ fake frames for the decoder

FAKE_FRAME_SAMPLES = 1600


class FakeFrameContainer:
    """A container whose first audio stream yields tone frames, then an invalid-data error.

    Only the frame sequence is faked; no recording exists, so no decoder interprets any bytes.
    """

    def __init__(self, good_frames: int, *, fail_after: bool) -> None:
        self._good_frames = good_frames
        self._fail_after = fail_after

    def _frames(self) -> Iterator[Any]:
        import av
        import numpy as np

        for _ in range(self._good_frames):
            frame = av.AudioFrame.from_ndarray(
                np.full((1, FAKE_FRAME_SAMPLES), 1000, dtype="<i2"), format="s16", layout="mono"
            )
            frame.sample_rate = 16000
            yield frame
        if self._fail_after:
            raise av.error.InvalidDataError(1, "injected invalid data")

    def decode(self, *, audio: int) -> Iterator[Any]:
        assert audio == 0
        return self._frames()

    def __enter__(self) -> "FakeFrameContainer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None
