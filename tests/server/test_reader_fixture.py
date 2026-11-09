"""Self-test of the emulated reader socket and ``fake_reader.py``."""

from __future__ import annotations

import array
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from .conftest import EmulatedReaderSocket, fake_reader_cmd


def _exchange(path: Path, fd_count: int) -> list[dict[str, Any]]:
    """Connects, sends a probe-like request with `fd_count` descriptors, reads until EOF."""
    fds = [os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC) for _ in range(fd_count)]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
            client.settimeout(5)
            client.connect(str(path))
            body = json.dumps({"op": "probe", "limit_s": 1}).encode("utf-8")
            client.sendmsg([body], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))])
            messages = []
            while data := client.recv(4096):
                messages.append(json.loads(data))
            return messages
    finally:
        for fd in fds:
            os.close(fd)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("no-start", []), ("not-started", [{"started": False}]), ("no-verdict", [{"started": True}])],
)
def test_fake_reader_modes(emulated_reader_socket: Any, mode: str, expected: list[dict[str, Any]]) -> None:
    reader = emulated_reader_socket(fake_reader_cmd(mode))

    assert _exchange(reader.path, 1) == expected
    reader.wait_for_exits(1)
    assert reader.spawn_count == 1
    assert reader.fd_counts() == [1]


def test_each_connection_gets_its_own_process_and_descriptor_count(emulated_reader_socket: Any) -> None:
    reader = emulated_reader_socket(fake_reader_cmd("no-start"))

    _exchange(reader.path, 1)
    _exchange(reader.path, 2)
    reader.wait_for_exits(2)

    assert reader.spawn_count == 2
    assert reader.fd_counts() == [1, 2]


def test_close_fails_when_a_spawned_process_is_still_running(emulated_reader_socket: Any) -> None:
    reader: EmulatedReaderSocket = emulated_reader_socket([sys.executable, "-c", "import time; time.sleep(60)"])
    client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    client.connect(str(reader.path))
    try:
        for _ in range(200):
            if reader.spawn_count:
                break
            time.sleep(0.01)
        with pytest.raises(AssertionError, match="still running"):
            reader.close(grace_s=0.2)
    finally:
        client.close()
