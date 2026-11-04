"""Sweep stage: runs the daily purge, expires old records and clears a dead worker's traces.

The caller holds ``sweep.lock``. ``state.lock`` is held here for the whole run; a run is short.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Callable, Iterator

from stt.server import lifecycle
from stt.server.layout import Layout, describe_error, hold, try_hold
from stt.server.records import load_audios


def _log(request_id: str, state: str) -> None:
    print(f"{request_id} {state}", flush=True)


class _Faults:
    """Counts the items one sweep could not clean, logging one fixed line for each."""

    def __init__(self) -> None:
        self.count = 0

    @contextmanager
    def guard(self, label: str) -> Iterator[None]:
        """Logs and swallows any error of one item, so the rest of the daily purge still runs."""
        try:
            yield
        except Exception as error:
            self.count += 1
            print(f"{label} cleanup failed {describe_error(error)}", flush=True)


def _purge_due_audio(layout: Layout, now: datetime, guard: lifecycle.Guard) -> None:
    for audio in load_audios(layout):
        if lifecycle.is_due(audio, now):
            with guard(audio.audio_id):
                for failed in lifecycle.purge_audio(layout, audio.audio_id, now):
                    _log(failed.request_id, "failed audio_deleted")


def _clear_dead_worker(layout: Layout, now: datetime, guard: lifecycle.Guard) -> None:
    """Fails the jobs a dead worker left ``running``; does nothing while a worker holds ``worker.lock``."""
    alive_check = try_hold(layout.worker_lock)
    if alive_check is None:
        # intentional-skip: a live worker owns its running job and work folder.
        return
    with alive_check:
        for interrupted in lifecycle.clear_dead_worker(layout, now, guard):
            _log(interrupted.request_id, "failed interrupted")


def run_once(layout: Layout, now: Callable[[], datetime]) -> int:
    """Runs one sweep.

    First, when no worker is alive, cleans up after a dead one, so a job it left
    ``running`` ends as ``interrupted`` with its published result removed even if its
    recording is also due. Then deletes the recordings whose daily purge has come
    (failing the jobs still waiting for them), removes expired responses, results and
    records and stale leftovers, and last removes queue markers of jobs no longer
    waiting. An item that fails is logged and skipped; the others still run.

    Args:
        layout: The state root.
        now: Clock returning the current UTC time.

    Returns:
        The number of items that could not be cleaned; 0 if none.
    """
    moment = now()
    faults = _Faults()
    with hold(layout.state_lock):
        with faults.guard("dead worker"):
            _clear_dead_worker(layout, moment, faults.guard)
        with faults.guard("daily purge"):
            _purge_due_audio(layout, moment, faults.guard)
        with faults.guard("expiry"):
            lifecycle.purge_expired(layout, moment, faults.guard)
        with faults.guard("queue"):
            lifecycle.clear_stale_markers(layout, faults.guard)
    return faults.count
