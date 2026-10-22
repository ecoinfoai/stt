"""The ``stt server`` command group: ``intake`` and ``work``, each under its stage lock."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from stt.server.layout import Layout, try_hold

DEFAULT_ROOT = Path("/var/lib/stt")
DEFAULT_MODELS = Path("/var/lib/stt-models")

EXIT_USAGE = 2
EXIT_BUSY = 75

_BUSY_MESSAGES = {
    "intake": "Another intake run is in progress.",
    "work": "Another worker run is in progress.",
}


def build_parser() -> argparse.ArgumentParser:
    """Builds the ``stt server`` argument parser.

    Returns:
        A parser with the ``intake`` and ``work`` subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="stt server",
        description="Server-side transcription stages; each run drains its queue and exits.",
    )
    commands = parser.add_subparsers(dest="command", metavar="{intake,work}", required=True)

    intake_parser = commands.add_parser(
        "intake", help="process every request in the inbox once", description="Process every request in the inbox once."
    )
    work_parser = commands.add_parser(
        "work", help="run queued jobs one at a time until the queue is empty",
        description="Run queued jobs one at a time until the queue is empty.",
    )
    for sub in (intake_parser, work_parser):
        sub.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="state root (default: %(default)s)")
    work_parser.add_argument(
        "--models", type=Path, default=DEFAULT_MODELS,
        help="model cache folder, used offline by the engine (default: %(default)s)",
    )
    return parser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _work(args: argparse.Namespace, layout: Layout) -> int:
    from stt.server import worker

    return worker.run(layout, worker.DEFAULT_ENGINE_CMD, _utc_now, models_dir=args.models)


def _intake(args: argparse.Namespace, layout: Layout) -> int:
    from stt.server import intake

    intake.run_once(layout, _utc_now)
    return 0


# Stage modules load inside the stage functions: they need the optional `server`
# extra (jsonschema), which the other `stt` commands must not require.
_STAGES: dict[str, tuple[Callable[[argparse.Namespace, Layout], int], Callable[[Layout], Path]]] = {
    "intake": (_intake, lambda layout: layout.intake_lock),
    "work": (_work, lambda layout: layout.worker_lock),
}


def main(argv: Sequence[str]) -> int:
    """Runs one ``stt server`` subcommand.

    Args:
        argv: Arguments after ``server``.

    Returns:
        0 when the stage finished, 2 for a usage error, 75 when another run of the
        same stage holds its lock.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    layout = Layout(args.root)
    layout.ensure_layout()
    stage, lock_path = _STAGES[args.command]
    held = try_hold(lock_path(layout))
    if held is None:
        print(_BUSY_MESSAGES[args.command], file=sys.stderr)
        return EXIT_BUSY
    with held:
        return stage(args, layout)
