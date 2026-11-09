"""A decode child double that holds every decode until the test releases it.

Usage: ``python gated_decode_child.py --started PATH --release PATH OP RECORDING_FD OUTPUT_FD``

The supervisor appends ``OP RECORDING_FD OUTPUT_FD`` to the child command, exactly as it does for the real
child. A ``probe`` is handed to the real child at once. A ``decode`` first creates the ``--started`` file, then
waits until the ``--release`` file exists (at most ``MAX_WAIT_S``, then it exits with code 1) and only then
runs the real child. A test uses it to keep a decode in flight while another read happens.

The double never reads or interprets recording content; the real child does that, as in production.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

MAX_WAIT_S = 30.0
_POLL_S = 0.02


def main(argv: list[str]) -> int:
    """Runs the double.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        The real child's exit code, or 1 when the release did not come in time.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--started", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("op")
    parser.add_argument("recording_fd")
    parser.add_argument("output_fd")
    args = parser.parse_args(argv)

    if args.op == "decode":
        args.started.touch()
        deadline = time.monotonic() + MAX_WAIT_S
        while not args.release.exists():
            if time.monotonic() >= deadline:
                return 1
            time.sleep(_POLL_S)

    from stt.server import reader

    return reader.main(["child", args.op, args.recording_fd, args.output_fd])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
