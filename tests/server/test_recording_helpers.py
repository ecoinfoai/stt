"""Self-test of the test-recording helpers and the ``sigint-parent`` fake child.

Each helper's output is checked once for its stream kinds and codec names; nothing here
tests the reader.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import (
    make_adpcm_wav,
    make_amr_nb_3gp,
    make_faststart_m4a,
    make_mov_ima4,
    make_mp2,
    make_mp3_with_cover,
    make_mp4_video_only,
    make_mp4_with_dolby_audio,
    make_mp4_with_two_audio_streams,
    make_mp4_with_video,
    stream_kinds_and_codecs,
    truncate_after_mdat_header,
    truncate_to_ratio,
)

FAKE_DECODE_CHILD = Path(__file__).with_name("fake_decode_child.py")


@pytest.mark.parametrize(
    ("video_encoder", "video_decoder"), [("libx264", "h264"), ("mpeg4", "mpeg4")]
)
def test_helper_mp4_with_video(tmp_path: Path, video_encoder: str, video_decoder: str) -> None:
    path = make_mp4_with_video(tmp_path / "talk.mp4", video_codec=video_encoder)

    assert stream_kinds_and_codecs(path) == [("audio", "aac"), ("video", video_decoder)]


def test_helper_mp3_with_cover(tmp_path: Path) -> None:
    import av

    path = make_mp3_with_cover(tmp_path / "talk.mp3")

    assert stream_kinds_and_codecs(path) == [("audio", "mp3float"), ("video", "png")]
    with av.open(str(path)) as container:
        assert container.streams[1].disposition & av.stream.Disposition.attached_pic


def test_helper_mp4_video_only(tmp_path: Path) -> None:
    path = make_mp4_video_only(tmp_path / "screen.mp4")

    assert stream_kinds_and_codecs(path) == [("video", "h264")]


def test_helper_faststart_m4a_has_index_first(tmp_path: Path) -> None:
    path = make_faststart_m4a(tmp_path / "talk.m4a")

    assert stream_kinds_and_codecs(path) == [("audio", "aac")]
    data = path.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")


def test_helper_mp4_with_two_audio_streams(tmp_path: Path) -> None:
    path = make_mp4_with_two_audio_streams(tmp_path / "two.mp4")

    assert stream_kinds_and_codecs(path) == [("audio", "aac"), ("audio", "aac")]


@pytest.mark.parametrize("codec", ["adpcm_ima_wav", "adpcm_ms", "adpcm_yamaha"])
def test_helper_adpcm_wav(tmp_path: Path, codec: str) -> None:
    path = make_adpcm_wav(tmp_path / "talk.wav", codec)

    assert stream_kinds_and_codecs(path) == [("audio", codec)]


@pytest.mark.parametrize("suffix", [".mp3", ".wav"])
def test_helper_mp2(tmp_path: Path, suffix: str) -> None:
    path = make_mp2(tmp_path / f"talk{suffix}")

    assert stream_kinds_and_codecs(path) == [("audio", "mp2")]


def test_helper_mp2_rejects_other_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\.mp3 or \.wav"):
        make_mp2(tmp_path / "talk.m4a")


@pytest.mark.parametrize("codec", ["ac3", "eac3"])
def test_helper_mp4_with_dolby_audio(tmp_path: Path, codec: str) -> None:
    path = make_mp4_with_dolby_audio(tmp_path / "talk.mp4", codec)

    assert stream_kinds_and_codecs(path) == [("audio", codec)]


def test_helper_amr_nb_3gp(tmp_path: Path) -> None:
    path = make_amr_nb_3gp(tmp_path / "talk.m4a")

    assert stream_kinds_and_codecs(path) == [("audio", "amrnb")]


def test_helper_mov_ima4_named_m4a(tmp_path: Path) -> None:
    path = make_mov_ima4(tmp_path / "talk.m4a")

    assert stream_kinds_and_codecs(path) == [("audio", "adpcm_ima_qt")]


def test_helper_truncate_to_ratio_keeps_the_given_share(tmp_path: Path) -> None:
    path = make_mp3_with_cover(tmp_path / "talk.mp3")
    size = path.stat().st_size

    truncate_to_ratio(path, 0.5)

    assert path.stat().st_size == size // 2


@pytest.mark.parametrize("ratio", [0.0, 1.0, -0.1, 1.5])
def test_helper_truncate_to_ratio_rejects_a_ratio_outside_the_open_interval(tmp_path: Path, ratio: float) -> None:
    path = make_mp3_with_cover(tmp_path / "talk.mp3")

    with pytest.raises(ValueError, match="strictly between"):
        truncate_to_ratio(path, ratio)


def test_helper_truncate_after_mdat_header_ends_at_the_media_data(tmp_path: Path) -> None:
    path = make_faststart_m4a(tmp_path / "talk.m4a")
    mdat_at = path.read_bytes().index(b"mdat") - 4

    truncate_after_mdat_header(path)

    assert path.stat().st_size == mdat_at + 8
    assert path.read_bytes()[-4:] == b"mdat"


def test_helper_truncate_after_mdat_header_rejects_a_file_without_mdat(tmp_path: Path) -> None:
    path = tmp_path / "not-mp4.m4a"
    path.write_bytes(b"plain text, not a recording\n")

    with pytest.raises(ValueError, match="no top-level mdat box"):
        truncate_after_mdat_header(path)


_SIGINT_PARENT = """
import os, signal, subprocess, sys
seen = []
signal.signal(signal.SIGINT, lambda *_: seen.append(1))
recording_r, recording_w = os.pipe()
output_r, output_w = os.pipe()
child = subprocess.run(
    [sys.executable, sys.argv[1], "--mode", "sigint-parent", "probe", str(recording_r), str(output_w)],
    pass_fds=(recording_r, output_w),
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
os.close(output_w)
print(child.returncode, len(seen), os.read(output_r, 100).decode())
"""


def test_fake_decode_child_sigint_parent_signals_the_parent_then_succeeds() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _SIGINT_PARENT, str(FAKE_DECODE_CHILD)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert result.stdout.strip() == '0 1 {"duration_s": 1.5}'
