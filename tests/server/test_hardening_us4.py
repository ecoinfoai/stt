"""US4 of the reader hardening: five small fixes.

The reader supervisor ignores SIGINT, so a decode child that signals its parent cannot interrupt it. The decoded
temporary audio is written through the descriptor that created it, never by reopening its path. The two Python
children started with ``-m`` leave the current folder out of their module search path.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

import pytest

from stt.server import reader, worker

from .test_reader import PROBE_BODY, Descriptors, converse, fake_child, supervisor_command

SIGNAL_TIMEOUT_S = 30
PLANTED_EXIT_CODE = 99
UNIT_UMASK = 0o027


@pytest.fixture
def descriptors(tmp_path: Path) -> Any:
    pool = Descriptors(tmp_path)
    yield pool
    pool.close_all()


def test_hardening_us4_1_a_child_that_sends_sigint_to_the_supervisor_does_not_interrupt_it(
    descriptors: Descriptors,
) -> None:
    disposition_before = signal.getsignal(signal.SIGINT)

    run, messages = converse(supervisor_command(fake_child("sigint-parent")), descriptors, PROBE_BODY, ("file",))

    assert messages[-1] == {"status": "ok", "duration_s": 1.5}
    assert run.code == 0
    assert b"Traceback" not in run.stderr
    assert b"KeyboardInterrupt" not in run.stderr
    assert run.stderr == b""
    assert signal.getsignal(signal.SIGINT) == disposition_before


@pytest.fixture
def unit_umask() -> Any:
    previous = os.umask(UNIT_UMASK)
    yield
    os.umask(previous)


def _wave_open_after_deleting(path: Path, real_open: Any) -> Any:
    """Wraps ``wave.open`` so that its first call finds the audio path already deleted."""
    calls: list[int] = []

    def opened(*args: Any, **kwargs: Any) -> Any:
        if not calls:
            os.unlink(path)
        calls.append(1)
        return real_open(*args, **kwargs)

    return opened


def test_hardening_us4_3_a_path_deleted_right_after_it_was_created_does_not_reappear(
    tmp_path: Path, unit_umask: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "audio.wav"
    monkeypatch.setattr(wave, "open", _wave_open_after_deleting(path, wave.open))

    worker._write_audio(tmp_path, bytes(3200))

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_hardening_us4_3_the_decoded_audio_is_a_private_wav_of_one_channel_two_bytes_and_16_khz(
    tmp_path: Path, unit_umask: None
) -> None:
    samples = bytes(range(256)) * 12

    worker._write_audio(tmp_path, samples)

    path = tmp_path / "audio.wav"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with wave.open(str(path), "rb") as handle:
        assert (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) == (1, 2, 16000)
        assert handle.readframes(handle.getnframes()) == samples


PLANTED_MODULE = f"import sys\nsys.exit({PLANTED_EXIT_CODE})\n"


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    folder = tmp_path / "planted"
    folder.mkdir()
    (folder / "json.py").write_text(PLANTED_MODULE, encoding="utf-8")
    return folder


def _run_in(folder: Path, command: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command, cwd=folder, stdin=subprocess.DEVNULL, capture_output=True, timeout=SIGNAL_TIMEOUT_S, check=False
    )


def test_hardening_us4_5_the_decode_child_does_not_read_a_module_planted_in_the_current_folder(planted: Path) -> None:
    run = _run_in(planted, [*reader.DEFAULT_CHILD_COMMAND])

    assert run.returncode != PLANTED_EXIT_CODE


def test_hardening_us4_5_the_engine_does_not_read_a_module_planted_in_the_current_folder(planted: Path) -> None:
    run = _run_in(planted, [*worker.DEFAULT_ENGINE_CMD])

    assert run.returncode != PLANTED_EXIT_CODE


def test_hardening_us4_5_the_console_script_does_not_read_a_module_planted_in_the_current_folder(
    planted: Path,
) -> None:
    script = Path(sys.executable).parent / "stt"
    if not script.exists():
        pytest.skip("the console script stt is not installed next to this interpreter")

    run = _run_in(planted, [str(script), "--help"])

    assert run.returncode == 0
