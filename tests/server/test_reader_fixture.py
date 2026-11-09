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

from .conftest import EmulatedReaderSocket, ReceivedFd, fake_reader_cmd

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
