"""Decodes a recording to 16 kHz mono ``s16`` audio.

The one decoder of stt: the reader child writes its bytes to a descriptor and the
command line turns them into the float32 samples the Whisper model takes. The steps
mirror ``faster_whisper.audio.decode_audio`` (resampler, ``AudioFifo`` chunking,
``pts`` reset, skipping frames that fail with ``InvalidDataError``), whose own
version cannot run with PyAV 19, so the output bytes are the same.

PyAV is imported inside the functions: the server's intake and worker stages import
this package and must never load it.
"""

from __future__ import annotations

import gc
import io
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    import numpy as np

SAMPLE_RATE = 16000
_FIFO_SAMPLES = 500_000
_BYTES_PER_SAMPLE = 2
# PyAV 19 removed ``metadata_errors`` and decodes invalid UTF-8 metadata on its own.
_METADATA_ERRORS_REMOVED_IN = 19


def open_container(source: BinaryIO | str, *, format_whitelist: str | None = None) -> Any:
    """Opens a recording for reading.

    Args:
        source: A path or a binary file object.
        format_whitelist: FFmpeg demuxer names (comma separated) the container may use;
            None allows every format.

    Returns:
        An open PyAV input container.
    """
    import av

    options: dict[str, Any] = {}
    if int(av.__version__.split(".")[0]) < _METADATA_ERRORS_REMOVED_IN:
        options["metadata_errors"] = "ignore"
    if format_whitelist is not None:
        options["container_options"] = {"format_whitelist": format_whitelist}
    return av.open(source, mode="r", **options)


def _valid_frames(frames: Iterator[Any]) -> Iterator[Any]:
    import av

    while True:
        try:
            yield next(frames)
        except StopIteration:
            return
        except av.error.InvalidDataError:
            continue


def _write_resampled(resampler: Any, frame: Any, output: BinaryIO) -> None:
    for resampled in resampler.resample(frame):
        output.write(bytes(resampled.planes[0])[: resampled.samples * _BYTES_PER_SAMPLE])


def write_s16(container: Any, output: BinaryIO) -> None:
    """Decodes the first audio stream and writes it as 16 kHz mono ``s16`` little-endian bytes.

    Args:
        container: An open PyAV input container.
        output: Binary file object that receives the samples.
    """
    import av

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    fifo = av.audio.fifo.AudioFifo()
    for frame in _valid_frames(container.decode(audio=0)):
        frame.pts = None
        fifo.write(frame)
        if fifo.samples >= _FIFO_SAMPLES:
            _write_resampled(resampler, fifo.read(), output)
    if fifo.samples > 0:
        _write_resampled(resampler, fifo.read(), output)
    _write_resampled(resampler, None, output)


def decode_samples(path: Path) -> "np.ndarray":
    """Decodes a media file to the samples faster-whisper's ``decode_audio`` returns.

    Args:
        path: The media file; any format FFmpeg can open.

    Returns:
        Mono float32 samples at 16 kHz, in [-1, 1).

    Raises:
        FileNotFoundError: If the file does not exist.
        av.error.InvalidDataError: If FFmpeg cannot read the file.
    """
    import numpy as np

    buffer = io.BytesIO()
    with open_container(str(path)) as container:
        write_s16(container, buffer)
    # The resampler's native objects are only freed by a collection (faster-whisper issue 390).
    gc.collect()
    return np.frombuffer(buffer.getbuffer(), dtype="<i2").astype(np.float32) / 32768.0
