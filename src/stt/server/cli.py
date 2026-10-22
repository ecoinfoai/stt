"""The ``stt server`` command group: ``intake``, ``work`` and ``sweep`` under their stage locks, and ``fetch-models``."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    from stt.server.layout import Layout

DEFAULT_ROOT = Path("/var/lib/stt")
DEFAULT_MODELS = Path("/var/lib/stt-models")

EXIT_FETCH_FAILED = 1
EXIT_USAGE = 2
EXIT_BUSY = 75

_BUSY_MESSAGES = {
    "intake": "Another intake run is in progress.",
    "work": "Another worker run is in progress.",
    "sweep": "Another sweep run is in progress.",
}


def build_parser() -> argparse.ArgumentParser:
    """Builds the ``stt server`` argument parser.

    Returns:
        A parser with the ``intake``, ``work``, ``sweep`` and ``fetch-models`` subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="stt server",
        description="Server-side transcription stages; each run drains its queue and exits.",
    )
    commands = parser.add_subparsers(dest="command", metavar="{intake,work,sweep,fetch-models}", required=True)

    intake_parser = commands.add_parser(
        "intake", help="process every request in the inbox once", description="Process every request in the inbox once."
    )
    work_parser = commands.add_parser(
        "work", help="run queued jobs one at a time until the queue is empty",
        description="Run queued jobs one at a time until the queue is empty.",
    )
    sweep_parser = commands.add_parser(
        "sweep",
        help="delete what is due, expire old records and clear a dead worker's traces",
        description="Delete what is due, expire old records and clear a dead worker's traces.",
    )
    fetch_parser = commands.add_parser(
        "fetch-models",
        help="download the pinned models into the model folder",
        description="Download each pinned model at its pinned commit into the model folder and record the list in "
        "DIR/.complete; do nothing when that list is already recorded. The only command that needs network access "
        "and the HF_TOKEN environment variable.",
    )
    fetch_parser.add_argument("--models", type=Path, required=True, help="model cache folder to fill")
    for sub in (intake_parser, work_parser, sweep_parser):
        sub.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="state root (default: %(default)s)")
    work_parser.add_argument(
        "--models", type=Path, default=DEFAULT_MODELS,
        help="model cache folder, used offline by the engine (default: %(default)s)",
    )
    work_parser.add_argument(
        "--gpu-lock", type=Path, required=True,
        help="existing lock file shared with the command line's STT_GPU_LOCK",
    )
    return parser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_models(args: argparse.Namespace) -> int:
    from huggingface_hub import snapshot_download

    from stt.server import models

    try:
        models.fetch(args.models, os.environ.get("HF_TOKEN") or None, snapshot_download)
    except models.FetchError as error:
        print(error, file=sys.stderr)
        return EXIT_FETCH_FAILED
    return 0


def _work(args: argparse.Namespace, layout: Layout) -> int:
    from stt.gpu_lock import MissingGpuLockError
    from stt.server import worker

    try:
        return worker.run(layout, worker.DEFAULT_ENGINE_CMD, _utc_now, models_dir=args.models, gpu_lock=args.gpu_lock)
    except MissingGpuLockError as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE


def _intake(args: argparse.Namespace, layout: Layout) -> int:
    from stt.server import intake

    intake.run_once(layout, _utc_now)
    return 0


def _sweep(args: argparse.Namespace, layout: Layout) -> int:
    from stt.server import sweep

    sweep.run_once(layout, _utc_now)
    return 0


# Stage modules load inside the stage functions: they need the optional `server`
# extra (jsonschema), which the other `stt` commands must not require. The layout
# module loads inside `main` because it needs fcntl, which `stt` must not require.
_STAGES: dict[str, tuple[Callable[[argparse.Namespace, Layout], int], Callable[[Layout], Path]]] = {
    "intake": (_intake, lambda layout: layout.intake_lock),
    "work": (_work, lambda layout: layout.worker_lock),
    "sweep": (_sweep, lambda layout: layout.sweep_lock),
}


def main(argv: Sequence[str]) -> int:
    """Runs one ``stt server`` subcommand.

    Args:
        argv: Arguments after ``server``.

    Returns:
        0 when the stage finished or the models are in place, 1 when a model download failed, 2 for a usage
        error or a missing GPU lock file, 75 when another run of the same stage holds its lock.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else EXIT_USAGE
    if args.command == "fetch-models":
        return _fetch_models(args)
    from stt.server.layout import Layout, try_hold

    layout = Layout(args.root)
    layout.ensure_layout()
    stage, lock_path = _STAGES[args.command]
    held = try_hold(lock_path(layout))
    if held is None:
        print(_BUSY_MESSAGES[args.command], file=sys.stderr)
        return EXIT_BUSY
    with held:
        return stage(args, layout)
