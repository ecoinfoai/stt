"""Self-test of the emulated reader socket and ``fake_reader.py``."""

from __future__ import annotations

import array
import json
import os
import socket
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from .conftest import EmulatedReaderSocket, ReaderSocket, ReceivedFd, fake_reader_cmd, make_wav

FAKE_DECODE_CHILD = Path(__file__).with_name("fake_decode_child.py")
GATED_DECODE_CHILD = Path(__file__).with_name("gated_decode_child.py")

Factory = Callable[..., EmulatedReaderSocket]

_ECHO_IF_TWO_FDS = """
import array, json, socket
c = socket.socket(fileno=0)
_, anc, _, _ = c.recvmsg(4096, socket.CMSG_SPACE(64))
fds = array.array("i")
for _, _, data in anc:
    fds.frombytes(data[: len(data) - len(data) % fds.itemsize])
if len(fds) == 2:
    c.send(json.dumps({"started": True}).encode())
"""

_LEAVE_GRANDCHILD = """
import subprocess, sys
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
"""


def _exchange(path: Path, fds: list[int]) -> list[dict[str, Any]]:
    """Connects, sends a probe-like request carrying `fds`, reads until EOF."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.settimeout(5)
        client.connect(str(path))
        body = json.dumps({"op": "probe", "limit_s": 1}).encode("utf-8")
        client.sendmsg([body], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))])
        messages = []
        while data := client.recv(4096):
            messages.append(json.loads(data))
        return messages


def _devnull_fds(count: int) -> list[int]:
    return [os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC) for _ in range(count)]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("no-start", []), ("not-started", [{"started": False}]), ("no-verdict", [{"started": True}])],
)
def test_fake_reader_modes(emulated_reader_socket: Factory, mode: str, expected: list[dict[str, Any]]) -> None:
    reader = emulated_reader_socket(fake_reader_cmd(mode))
    fds = _devnull_fds(1)
    try:
        assert _exchange(reader.path, fds) == expected
    finally:
        for fd in fds:
            os.close(fd)

    reader.wait_for_exits(1)
    assert reader.spawn_count == 1
    assert reader.fd_counts() == [1]


def test_each_connection_gets_its_own_process_and_descriptor_count(emulated_reader_socket: Factory) -> None:
    reader = emulated_reader_socket(fake_reader_cmd("no-start"))
    for count in (1, 2):
        fds = _devnull_fds(count)
        try:
            _exchange(reader.path, fds)
        finally:
            for fd in fds:
                os.close(fd)

    reader.wait_for_exits(2)

    assert reader.spawn_count == 2
    assert reader.fd_counts() == [1, 2]


def test_descriptors_are_recorded_by_the_fixture_and_still_reach_the_program(
    emulated_reader_socket: Factory, tmp_path: Path
) -> None:
    reader = emulated_reader_socket([sys.executable, "-c", _ECHO_IF_TWO_FDS])
    recording = tmp_path / "audio.bin"
    recording.write_bytes(b"x")
    file_fd = os.open(recording, os.O_RDONLY)
    pipe_read, pipe_write = os.pipe()
    try:
        messages = _exchange(reader.path, [file_fd, pipe_write])
        file_info, pipe_info = os.fstat(file_fd), os.fstat(pipe_write)
    finally:
        for fd in (file_fd, pipe_read, pipe_write):
            os.close(fd)

    reader.wait_for_exits(1)

    assert messages == [{"started": True}]
    assert reader.received() == [
        (
            ReceivedFd(stat.S_IFREG, file_info.st_dev, file_info.st_ino),
            ReceivedFd(stat.S_IFIFO, pipe_info.st_dev, pipe_info.st_ino),
        )
    ]


def test_a_connection_that_sends_nothing_is_recorded_as_none(emulated_reader_socket: Factory) -> None:
    reader = emulated_reader_socket(fake_reader_cmd("no-start"), peek_timeout_s=0.05)

    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.connect(str(reader.path))
        deadline = time.monotonic() + 5
        while not reader.spawn_count and time.monotonic() < deadline:
            time.sleep(0.01)

    reader.wait_for_exits(1)
    assert reader.received() == [None]
    assert reader.fd_counts() == [None]


def test_close_fails_when_a_spawned_process_is_still_running(emulated_reader_socket: Factory) -> None:
    reader = emulated_reader_socket([sys.executable, "-c", "import time; time.sleep(60)"], peek_timeout_s=0.05)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.connect(str(reader.path))
        deadline = time.monotonic() + 5
        while not reader.spawn_count and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(AssertionError, match="still running"):
            reader.close(grace_s=0.2)


def test_close_fails_when_a_child_of_the_spawned_process_outlives_it(emulated_reader_socket: Factory) -> None:
    reader = emulated_reader_socket([sys.executable, "-c", _LEAVE_GRANDCHILD], peek_timeout_s=0.05)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.connect(str(reader.path))
        deadline = time.monotonic() + 5
        while not reader.spawn_count and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.5)
        with pytest.raises(AssertionError, match="still running"):
            reader.close(grace_s=0.2)


def test_a_spawn_failure_is_reported_instead_of_killing_the_accept_thread(emulated_reader_socket: Factory) -> None:
    reader = emulated_reader_socket(["/nonexistent/stt-reader"], peek_timeout_s=0.05)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.connect(str(reader.path))
        with pytest.raises(AssertionError, match="failed to serve"):
            reader.wait_for_exits(1, timeout_s=2)
    with pytest.raises(AssertionError, match="failed to serve"):
        reader.close()


def test_reader_socket_runs_the_real_supervisor_by_default(reader_socket: ReaderSocket, tmp_path: Path) -> None:
    from stt.server import reader_client

    recording = make_wav(tmp_path / "tone.wav", 2.0, silent=False)

    result = reader_client.probe(reader_socket.config, recording)

    reader_socket.wait_for_exits(1)
    assert result.outcome == "ok"
    assert result.duration_s == pytest.approx(2.0, abs=0.05)
    assert reader_socket.spawn_count == 1
    assert reader_socket.fd_counts() == [1]
    assert reader_socket.config.socket_path == str(reader_socket.path)


def test_reader_socket_use_fake_changes_the_program_for_later_connections(
    reader_socket: ReaderSocket, tmp_path: Path
) -> None:
    from stt.server import reader_client

    recording = make_wav(tmp_path / "tone.wav", 1.0)
    reader_socket.use_fake("not-started")

    result = reader_client.probe(reader_socket.config, recording)

    reader_socket.wait_for_exits(1)
    assert result.outcome == "unavailable"


def test_reader_socket_use_supervisor_injects_a_decode_child_double(
    reader_socket: ReaderSocket, tmp_path: Path
) -> None:
    from stt.server import reader_client

    recording = make_wav(tmp_path / "tone.wav", 1.0)
    reader_socket.use_supervisor([sys.executable, str(FAKE_DECODE_CHILD), "--mode", "exit-error"])

    result = reader_client.probe(reader_socket.config, recording)

    reader_socket.wait_for_exits(1)
    assert result.outcome == "failed"


def test_reader_socket_gated_decode_child_decodes_once_released(reader_socket: ReaderSocket, tmp_path: Path) -> None:
    from stt.server import reader_client

    recording = make_wav(tmp_path / "tone.wav", 1.0, silent=False)
    started, release = tmp_path / "decode.started", tmp_path / "decode.release"
    release.touch()
    reader_socket.use_supervisor(
        [
            sys.executable,
            str(GATED_DECODE_CHILD),
            "--started", str(started),
            "--release", str(release),
            "--max-wait-s", "5",
        ]
    )

    result = reader_client.decode(reader_socket.config, recording, job_running=lambda: True, job_limit_s=30)

    reader_socket.wait_for_exits(1)
    assert result.outcome == "ok"
    assert len(result.audio) == 16000 * 2
    assert started.exists()
