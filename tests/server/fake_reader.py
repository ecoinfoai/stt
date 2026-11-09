"""A fake reader instance for the reader-socket tests.

Usage: ``python fake_reader.py --mode MODE``

The connection to the caller is inherited as standard input, exactly as
systemd hands it to a real reader instance. The fake receives the request
message with its descriptors (``SCM_RIGHTS``), closes them, reports how many
arrived, and then misbehaves in one fixed way:

    no-start: close the connection without sending anything.
    not-started: send ``{"started": false}``, then close.
    no-verdict: send ``{"started": true}``, then close without a verdict.

The number of received descriptors is written to the file named by the
``FAKE_READER_REPORT`` environment variable, when it is set. The fake never
reads or interprets recording content and imports only the standard library.
"""

from __future__ import annotations

import argparse
import array
import json
import os
import socket
import sys
from pathlib import Path

MESSAGE_LIMIT = 4096
MAX_FDS = 16
REPORT_ENV = "FAKE_READER_REPORT"
MODES = ("no-start", "not-started", "no-verdict")


def receive_fds(connection: socket.socket) -> list[int]:
    """Receives one request message and returns the descriptors attached to it.

    Args:
        connection: The accepted SEQPACKET connection.

    Returns:
        The received descriptors, at most ``MAX_FDS``.
    """
    _, ancillary, _, _ = connection.recvmsg(MESSAGE_LIMIT, socket.CMSG_SPACE(MAX_FDS * 4))
    received: list[int] = []
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            fds = array.array("i")
            fds.frombytes(data[: len(data) - len(data) % fds.itemsize])
            received.extend(fds)
    return received


def main(argv: list[str]) -> int:
    """Runs the chosen behaviour on the connection inherited as stdin.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=MODES, required=True)
    args = parser.parse_args(argv)

    with socket.socket(fileno=0) as connection:
        received = receive_fds(connection)
        for fd in received:
            os.close(fd)
        report = os.environ.get(REPORT_ENV)
        if report:
            Path(report).write_text(str(len(received)), encoding="utf-8")
        if args.mode == "not-started":
            connection.send(json.dumps({"started": False}).encode("utf-8"))
        elif args.mode == "no-verdict":
            connection.send(json.dumps({"started": True}).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
