"""A fake decode child for the reader supervisor tests.

Usage: ``python fake_decode_child.py [--marker PATH] [--once PATH] --mode MODE [--payload TEXT] [--count N] OP RECORDING_FD OUTPUT_FD``

The supervisor appends ``OP RECORDING_FD OUTPUT_FD`` to the child command it is
given, exactly as it does for the real child. The fake fails or succeeds in one
fixed way:

    exit-error: exit with code 1.
    hang: sleep until killed.
    sigkill: kill itself with SIGKILL.
    sigsys: kill itself with SIGSYS, as the system call filter does.
    early-stop: write ``--count`` zero bytes (default one second of silence) to the output
        descriptor, then exit with the early-stop code (``reader.EARLY_STOP_EXIT``).
    write-raw: write ``--payload`` (UTF-8) to the output descriptor, exit 0.
    write-raw-then-error: as write-raw, then exit with code 1.
    write-bytes: write ``--count`` zero bytes to the output descriptor, exit 0.
    verify-spawn: exit 0 after writing a valid probe result, but only when the
        process was spawned as the contract requires (argument list, only the
        two named descriptors open, standard streams on ``/dev/null``, same
        process group as the supervisor); otherwise exit with code 1.

With ``--marker`` the fake first writes its process id to that file, so a test
can tell whether the supervisor started it at all.

With ``--once PATH`` only the first process to create PATH behaves as ``--mode``
says; every later one succeeds: a probe writes a valid result, a decode writes
``--count`` zero bytes (default one second of silence). A test uses this to make
one read in a series fail and let the others work.

The fake never reads or interprets recording content: it only inspects
descriptor identities and writes to the output descriptor.
"""

from __future__ import annotations

import argparse
import os
import resource
import signal
import stat
import sys
import time

from stt.server.reader import EARLY_STOP_EXIT

MODES = ("exit-error", "hang", "sigkill", "sigsys", "early-stop", "write-raw", "write-raw-then-error", "write-bytes", "verify-spawn")
VALID_RESULT = b'{"duration_s": 1.5}'
ONE_SECOND_OF_SILENCE_BYTES = 32000


def write_all(fd: int, data: bytes) -> None:
    """Writes all of `data` to `fd`, retrying partial writes.

    Args:
        fd: Destination descriptor.
        data: The bytes to write.
    """
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def claim_first(path: str) -> bool:
    """Tells whether this process is the first to claim `path`.

    Args:
        path: A file that is created exclusively.

    Returns:
        True for the one process that created the file, False for every later one.
    """
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return False
    return True


def succeed(args: argparse.Namespace) -> int:
    """Writes a valid result for the operation and ends successfully.

    Args:
        args: The parsed command line.

    Returns:
        The process exit code, 0.
    """
    if args.op == "probe":
        write_all(args.output_fd, VALID_RESULT)
    else:
        write_all(args.output_fd, bytes(args.count or ONE_SECOND_OF_SILENCE_BYTES))
    return 0


def spawned_as_contracted(args: argparse.Namespace) -> bool:
    """Checks the way the supervisor started this process.

    Args:
        args: The parsed command line.

    Returns:
        True when the descriptors, standard streams and process group match the reader contract.
    """
    if args.op not in ("probe", "decode"):
        return False
    devnull = os.stat(os.devnull)
    for fd in (0, 1, 2):
        info = os.fstat(fd)
        if not (stat.S_ISCHR(info.st_mode) and info.st_rdev == devnull.st_rdev):
            return False
    open_fds = {int(name) for name in os.listdir("/proc/self/fd")}
    # os.listdir holds a descriptor of its own while listing; it no longer exists once the call returned.
    open_fds = {fd for fd in open_fds if os.path.exists(f"/proc/self/fd/{fd}")}
    if open_fds != {0, 1, 2, args.recording_fd, args.output_fd}:
        return False
    return os.getpgrp() == os.getpgid(os.getppid())


def main(argv: list[str]) -> int:
    """Runs the chosen behaviour.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--marker")
    parser.add_argument("--once")
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--payload", default="")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("op")
    parser.add_argument("recording_fd", type=int)
    parser.add_argument("output_fd", type=int)
    args = parser.parse_args(argv)

    if args.marker:
        with open(args.marker, "w", encoding="utf-8") as marker:
            marker.write(str(os.getpid()))
    if args.once is not None and not claim_first(args.once):
        return succeed(args)
    if args.mode == "exit-error":
        return 1
    if args.mode == "hang":
        time.sleep(3600)
        return 0
    if args.mode == "sigkill":
        os.kill(os.getpid(), signal.SIGKILL)
        return 0
    if args.mode == "sigsys":
        # No core dump of this deliberate kill on the host running the tests.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.kill(os.getpid(), signal.SIGSYS)
        return 0
    if args.mode == "early-stop":
        write_all(args.output_fd, bytes(args.count or ONE_SECOND_OF_SILENCE_BYTES))
        return EARLY_STOP_EXIT
    if args.mode == "write-raw":
        write_all(args.output_fd, args.payload.encode("utf-8"))
        return 0
    if args.mode == "write-raw-then-error":
        write_all(args.output_fd, args.payload.encode("utf-8"))
        return 1
    if args.mode == "write-bytes":
        write_all(args.output_fd, bytes(args.count))
        return 0
    if not spawned_as_contracted(args):
        return 1
    write_all(args.output_fd, VALID_RESULT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
