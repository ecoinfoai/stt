"""Reader-instance test double for the NixOS VM test (contracts/reader.md section 9).

The VM test's specialisations replace the ``ExecStart`` of ``stt-reader@.service`` with
``python vm-reader-double.py <probe behaviour> <decode behaviour>``. The double speaks
the reader protocol on standard input itself, so it runs inside exactly the unit
settings the real supervisor and its decode child run in; what it can reach is what
that unit can reach. It never interprets the recording: it only reads a few bytes
from the descriptor it was handed, to show that the descriptor works.

Behaviours:

* ``ok``: answers like a healthy reader (probe: 2.0 s; decode: 2 s of silence).
* ``check``: tries to reach everything a compromised reader must not reach and
  reports the result in place of the verdict. A probe carries it in ``duration_s``
  (packed, see ``pack``); a decode writes it as JSON to the output pipe, which the
  worker stores as the samples of ``work/<id>/audio.wav``.
* ``hold``: says ``started`` and then waits for the caller to hang up.
* ``no-verdict``: says ``started`` and ends without a verdict.
* ``memory``: says ``started`` and allocates until the unit's memory cap kills it.
* ``not-started``: answers ``{"started": false}``.
"""

from __future__ import annotations

import json
import os
import select
import socket
import sys
from collections.abc import Callable, Sequence
from typing import Any

TARGETS_PATH = "/run/vm-double/targets.json"
CHECK_HOLD_S = 30
SILENCE = bytes(2 * 16000 * 2)
CHUNK = b"\x01" * (16 << 20)
CHUNK_LIMIT = 64
REQUEST_LIMIT = 4096
MAX_FDS = 3


def send(connection: socket.socket, message: dict[str, Any]) -> None:
    try:
        connection.send(json.dumps(message).encode("utf-8"))
    except OSError:
        # The caller hung up; there is nobody left to answer.
        pass


def wait_for_hangup(connection: socket.socket, seconds: float | None) -> None:
    poller = select.poll()
    poller.register(connection.fileno(), select.POLLIN | select.POLLHUP)
    poller.poll(None if seconds is None else seconds * 1000)


def worked(action: Callable[[], object]) -> int:
    try:
        action()
    except OSError:
        return 0
    return 1


def close_after(fd: int) -> None:
    os.close(fd)


def visible(path: str) -> int:
    """Returns 1 when `path` can be stat'ed and is not a masked node.

    ``InaccessiblePaths`` leaves a mode-0000 node at the path, which is no way in: only the node shows.
    """
    try:
        return int(os.stat(path).st_mode & 0o777 != 0)
    except OSError:
        return 0


def reach_readonly(path: str) -> int:
    return visible(path) + worked(lambda: close_after(os.open(path, os.O_RDONLY)))


def reach(path: str) -> int:
    """Counts the operations on `path` that work: stat (unmasked), open for reading and writing, list, create in, unlink."""
    return (
        reach_readonly(path)
        + worked(lambda: close_after(os.open(path, os.O_WRONLY)))
        + worked(lambda: os.listdir(path))
        + worked(lambda: close_after(os.open(os.path.join(path, "double-probe"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)))
        + worked(lambda: os.unlink(path))
    )


def data_written(directory: str) -> int:
    """Returns 1 when a file in `directory` can be created and given data (``LimitFSIZE=0`` stops the data)."""
    path = os.path.join(directory, "double-probe")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            return int(os.write(fd, b"data") > 0)
        finally:
            os.close(fd)
            os.unlink(path)
    except OSError:
        return 0


def sockets_created() -> int:
    return sum(worked(lambda family=family: socket.socket(family).close()) for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX))


def other_processes() -> int:
    return int(any(name.isdigit() and int(name) != os.getpid() for name in os.listdir("/proc")))


def recording_readable(fd: int) -> bool:
    try:
        return len(os.read(fd, 16)) > 0
    except OSError:
        return False


def check_report(fds: Sequence[int]) -> dict[str, Any]:
    """Reaches for everything the target list names, and for the network, the descriptor and the scratch places."""
    with open(TARGETS_PATH, encoding="utf-8") as handle:
        targets = json.load(handle)["groups"]
    recording = fds[0]
    groups = {group["name"]: sum(reach(path) for path in group["paths"]) for group in targets}
    groups["network"] = sockets_created()
    groups["own-fd-write"] = worked(lambda: os.write(recording, b"x")) + worked(
        lambda: close_after(os.open(f"/proc/self/fd/{recording}", os.O_WRONLY))
    )
    groups["scratch-write"] = sum(data_written(directory) for directory in ("/tmp", "/var/tmp", os.getcwd()))
    groups["other-processes"] = other_processes()
    return {
        "uid": os.getuid(),
        "own_fd_readable": recording_readable(recording),
        "control": reach_readonly(TARGETS_PATH),
        "groups": groups,
    }


def pack(report: dict[str, Any]) -> float:
    """Packs a report into a probe ``duration_s``.

    The caller accepts a finite number between 0 and 10800 and nothing else, so the report
    travels as ``N / 2**30`` with N below 2**43: bits 0-16 the UID, bit 17 whether the
    recording descriptor was readable, bits 18-25 the number of successes (at most 255), and
    from bit 26 one bit per group in report order. The quotient is exact in a double.
    """
    groups = report["groups"]
    mask = sum(1 << index for index, count in enumerate(groups.values()) if count)
    successes = min(sum(groups.values()), 255)
    packed = report["uid"] | int(report["own_fd_readable"]) << 17 | successes << 18 | mask << 26
    return packed / 2**30


def behave_ok(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    send(connection, {"started": True})
    if op == "probe":
        send(connection, {"status": "ok", "duration_s": 2.0})
        return
    with open(fds[1], "wb") as output:
        output.write(SILENCE)
    send(connection, {"status": "ok"})


def behave_check(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    send(connection, {"started": True})
    report = check_report(fds)
    if op == "probe":
        send(connection, {"status": "ok", "duration_s": pack(report)})
        return
    body = json.dumps(report).encode("utf-8")
    with open(fds[1], "wb") as output:
        # The worker takes the output as 16-bit samples, so the byte count must be even.
        output.write(body + b" " * (len(body) % 2))
    wait_for_hangup(connection, CHECK_HOLD_S)
    send(connection, {"status": "ok"})


def behave_hold(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    send(connection, {"started": True})
    wait_for_hangup(connection, None)


def behave_no_verdict(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    send(connection, {"started": True})


def behave_memory(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    send(connection, {"started": True})
    held = [bytearray(CHUNK) for _ in range(CHUNK_LIMIT)]
    # Only reached when the memory cap did not stop the allocation above.
    sys.exit(len(held))


def behave_not_started(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    send(connection, {"started": False})


BEHAVIOURS: dict[str, Callable[[str, socket.socket, Sequence[int]], None]] = {
    "ok": behave_ok,
    "check": behave_check,
    "hold": behave_hold,
    "no-verdict": behave_no_verdict,
    "memory": behave_memory,
    "not-started": behave_not_started,
}


def main(argv: Sequence[str]) -> int:
    behaviours = dict(zip(("probe", "decode"), argv, strict=True))
    connection = socket.socket(fileno=0)
    data, fds, _, _ = socket.recv_fds(connection, REQUEST_LIMIT, MAX_FDS)
    op = json.loads(data.decode("utf-8"))["op"]
    BEHAVIOURS[behaviours[op]](op, connection, fds)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
