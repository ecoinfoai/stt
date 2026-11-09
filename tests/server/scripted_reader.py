"""A scripted fake reader instance for the reader client tests.

Usage: ``python scripted_reader.py --script JSON [--end MODE] [--marker PATH] [--skip-request]``

The connection to the caller is inherited as standard input, exactly as
systemd hands it to a real reader instance. The fake receives the request
message with its descriptors (``SCM_RIGHTS``) and keeps them open (with
``--skip-request`` it waits for the request but leaves it unread, so that closing
the connection resets it), then plays the script: a JSON list of steps, each one of

    {"send": TEXT}: send TEXT (UTF-8) as one message.
    {"send_fd": TEXT}: send TEXT with a descriptor of ``/dev/null`` attached.
    {"send_pad": N}: send N bytes of ``x`` as one message.
    {"sleep": SECONDS}: wait.

After the script, ``--end close`` (default) closes the connection; ``--end
hold`` waits until the caller hangs up, then records to ``--marker`` (a JSON
object): ``eof`` (the caller closed its end), ``extra_messages`` (messages the
caller sent after its request) and ``pipe`` (``"closed"`` when writing to the
request's pipe descriptor fails with a broken pipe, ``"open"`` when it
succeeds, ``null`` when the request carried no pipe).

The fake never reads or interprets recording content and imports only the
standard library and its sibling ``fake_reader.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import socket
import stat
import sys
import time
from typing import Any

from fake_reader import MESSAGE_LIMIT, receive_fds

HOLD_LIMIT_S = 30.0


def await_request(connection: socket.socket) -> list[int]:
    """Waits until the request has arrived, without receiving it.

    Args:
        connection: The accepted SEQPACKET connection.

    Returns:
        An empty list of descriptors.
    """
    select.select([connection], [], [], HOLD_LIMIT_S)
    return []


def play(connection: socket.socket, script: list[dict[str, Any]]) -> None:
    """Plays the script steps on the connection.

    Args:
        connection: The accepted SEQPACKET connection.
        script: The steps to play.
    """
    for step in script:
        if "send" in step:
            connection.send(step["send"].encode("utf-8"))
        elif "send_fd" in step:
            with open(os.devnull, "rb") as handle:
                socket.send_fds(connection, [step["send_fd"].encode("utf-8")], [handle.fileno()])
        elif "send_pad" in step:
            connection.send(b"x" * step["send_pad"])
        else:
            time.sleep(step["sleep"])


def hold(connection: socket.socket, fds: list[int]) -> dict[str, Any]:
    """Waits for the caller to hang up and probes the request's pipe.

    Args:
        connection: The accepted SEQPACKET connection.
        fds: The descriptors of the request.

    Returns:
        The marker content.
    """
    extra = 0
    eof = False
    connection.settimeout(HOLD_LIMIT_S)
    try:
        while True:
            data = connection.recv(MESSAGE_LIMIT)
            if not data:
                eof = True
                break
            extra += 1
    except OSError:
        pass
    pipe_state: str | None = None
    for fd in fds:
        if stat.S_ISFIFO(os.fstat(fd).st_mode):
            try:
                os.write(fd, b"\0")
            except BrokenPipeError:
                pipe_state = "closed"
            else:
                pipe_state = "open"
    return {"eof": eof, "extra_messages": extra, "pipe": pipe_state}


def main(argv: list[str]) -> int:
    """Plays the script on the connection inherited as stdin.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", default="[]")
    parser.add_argument("--end", choices=("close", "hold"), default="close")
    parser.add_argument("--marker")
    parser.add_argument("--skip-request", action="store_true")
    args = parser.parse_args(argv)

    with socket.socket(fileno=0) as connection:
        fds = await_request(connection) if args.skip_request else receive_fds(connection)
        play(connection, json.loads(args.script))
        if args.end == "hold":
            result = hold(connection, fds)
            if args.marker:
                with open(args.marker, "w", encoding="utf-8") as marker:
                    json.dump(result, marker)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
