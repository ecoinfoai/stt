"""Runs the reader supervisor with an injectable child command.

Usage: ``python reader_harness.py [--child-cmd JSON] [--request-timeout-s S] [--report-modules PATH]``

The connection is inherited as standard input, exactly as systemd hands it to
a ``stt-reader@.service`` instance. Without ``--child-cmd`` the supervisor uses
its default, the real decode child. ``--report-modules`` writes, as JSON, the
loaded top-level modules that are neither standard library nor ``stt`` and
were not already loaded when the harness started.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str]) -> int:
    """Runs the supervisor on standard input.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        The supervisor's exit code.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--child-cmd")
    parser.add_argument("--request-timeout-s", type=float)
    parser.add_argument("--report-modules")
    args = parser.parse_args(argv)

    before = set(sys.modules)
    from stt.server import reader

    kwargs: dict[str, object] = {}
    if args.child_cmd is not None:
        kwargs["child_command"] = json.loads(args.child_cmd)
    if args.request_timeout_s is not None:
        kwargs["request_timeout_s"] = args.request_timeout_s
    code = reader.serve(**kwargs)
    if args.report_modules:
        loaded = {name.split(".")[0] for name in set(sys.modules) - before}
        foreign = sorted(name for name in loaded if name not in sys.stdlib_module_names and name != "stt")
        with open(args.report_modules, "w", encoding="utf-8") as handle:
            json.dump(foreign, handle)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
