"""Reader-instance test double for the NixOS VM test (contracts/reader.md section 9).

The VM test's specialisations replace the ``ExecStart`` of ``stt-reader@.service`` with
``python vm-reader-double.py <probe behaviour> <decode behaviour>``. The double speaks
the reader protocol on standard input itself, so it runs inside exactly the unit
settings the real supervisor and its decode child run in; what it can reach is what
that unit can reach. It never interprets the recording: it only reads a few bytes
from the descriptor it was handed, to show that the descriptor works.

Behaviours:

* ``ok``: answers a probe like a healthy reader (2.0 s); it has no decode form.
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
CHUNK = b"\x01" * (16 << 20)
CHUNK_LIMIT = 64
REQUEST_LIMIT = 4096
MAX_FDS = 3


def send(connection: socket.socket, message: dict[str, Any]) -> None:
    """Sends one protocol message; a caller that already hung up gets nothing."""
    try:
        connection.send(json.dumps(message).encode("utf-8"))
    except OSError:
        # The caller hung up; there is nobody left to answer.
        pass


def wait_for_hangup(connection: socket.socket, seconds: float | None) -> None:
    """Returns when the caller closes the connection or sends anything, or after `seconds` (None: never)."""
    poller = select.poll()
    poller.register(connection.fileno(), select.POLLIN | select.POLLHUP)
    poller.poll(None if seconds is None else seconds * 1000)


def worked(action: Callable[[], object]) -> int:
    """Returns 1 when `action` ran without an OSError, else 0."""
    try:
        action()
    except OSError:
        return 0
    return 1


def close_after(fd: int) -> None:
    """Closes a descriptor that was opened only to show it could be."""
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
    """Counts the read-only ways in to `path`: an unmasked stat and an open for reading."""
    return visible(path) + worked(lambda: close_after(os.open(path, os.O_RDONLY)))


def created_in(directory: str) -> int:
    """Returns 1 when a file can be created in `directory`; the double removes the file it made."""
    path = os.path.join(directory, "double-probe")
    try:
        close_after(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError:
        return 0
    try:
        os.unlink(path)
    except OSError:
        pass
    return 1


def reach(path: str) -> int:
    """Counts the operations on `path` that work: stat (unmasked), open for reading, open for writing
    (without truncating), list, create a file inside.

    Nothing that exists is changed or removed, so a weakened unit cannot damage later steps. Being able to
    delete a target is the same permission as creating a file in its directory, which the directory
    targets cover.
    """
    return (
        reach_readonly(path)
        + worked(lambda: close_after(os.open(path, os.O_WRONLY)))
        + worked(lambda: os.listdir(path))
        + created_in(path)
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
    """Counts the socket families a new socket can be created in."""
    return sum(worked(lambda family=family: socket.socket(family).close()) for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX))


def other_processes() -> int:
    """Returns 1 when any process besides this one is visible in /proc."""
    return int(any(name.isdigit() and int(name) != os.getpid() for name in os.listdir("/proc")))


def visible_processes() -> int:
    """Counts the processes (thread group leaders) visible in /proc, this one included."""
    return sum(name.isdigit() for name in os.listdir("/proc"))


def recording_readable(fd: int) -> bool:
    """Returns whether a few bytes can be read from the recording descriptor (they are not looked at)."""
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
        "visible_pids": visible_processes(),
        "groups": groups,
    }


def pack(report: dict[str, Any]) -> float:
    """Packs a report into a probe ``duration_s``.

    The caller accepts a finite number between 0 and 10800 and nothing else, so the report
    travels as ``N / 2**30`` with N below 2**43: bits 0-15 the UID (dynamic UIDs are below 65520),
    bit 16 whether the recording descriptor was readable, bits 17-21 the number of successes (at most
    31), bit 22 whether the control reach worked (``control == 2``), bits 23-25 the number of
    processes visible in /proc (at most 7), and from bit 26 one bit per group in report order.
    The quotient is exact in a double.
    """
    groups = report["groups"]
    mask = sum(1 << index for index, count in enumerate(groups.values()) if count)
    successes = min(sum(groups.values()), 31)
    control = int(report["control"] == 2)
    visible = min(report["visible_pids"], 7)
    packed = report["uid"] | int(report["own_fd_readable"]) << 16 | successes << 17 | control << 22 | visible << 23 | mask << 26
    return packed / 2**30


def behave_ok(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    """Answers the probe like a healthy reader."""
    if op != "probe":
        raise ValueError("the ok behaviour serves the length probe only")
    send(connection, {"started": True})
    send(connection, {"status": "ok", "duration_s": 2.0})


def behave_check(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    """Reports what the instance could reach, in place of the verdict."""
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
    """Says started, then waits for the caller to hang up."""
    send(connection, {"started": True})
    wait_for_hangup(connection, None)


def behave_no_verdict(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    """Says started and ends."""
    send(connection, {"started": True})


def behave_memory(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    """Says started, then allocates until the memory cap kills the instance."""
    send(connection, {"started": True})
    held = [bytearray(CHUNK) for _ in range(CHUNK_LIMIT)]
    # Only reached when the memory cap did not stop the allocation above.
    sys.exit(len(held))


def behave_not_started(op: str, connection: socket.socket, fds: Sequence[int]) -> None:
    """Refuses the request."""
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
