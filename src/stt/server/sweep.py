"""Sweep stage: deletes what is due, expires old records and clears a dead worker's traces.

The caller holds ``sweep.lock``. ``state.lock`` is held here for the whole run; a run is short.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from stt.server import lifecycle
from stt.server.layout import Layout, hold, try_hold
from stt.server.records import load_audios


def _log(request_id: str, state: str) -> None:
    print(f"{request_id} {state}", flush=True)


def _delete_due_audio(layout: Layout, now: datetime) -> None:
    for audio in load_audios(layout):
        cause = lifecycle.due_cause(audio, now)
        if cause is not None:
            for failed in lifecycle.delete_audio(layout, audio.audio_id, cause, now):
                _log(failed.request_id, "failed audio_deleted")


def _clear_dead_worker(layout: Layout, now: datetime) -> None:
    """Fails the jobs a dead worker left ``running``; does nothing while a worker holds ``worker.lock``."""
    alive_check = try_hold(layout.worker_lock)
    if alive_check is None:
        return
    with alive_check:
        for interrupted in lifecycle.clear_dead_worker(layout, now):
            _log(interrupted.request_id, "failed interrupted")


def run_once(layout: Layout, now: Callable[[], datetime]) -> None:
    """Runs one sweep.

    First, when no worker is alive, cleans up after a dead one, so a job it left
    ``running`` ends as ``interrupted`` with its published result removed even if its
    recording is also due. Then deletes recordings whose scheduled time or hard
    deadline has passed (failing the jobs still waiting for them), and last removes
    expired responses, results and records and stale leftovers.

    Args:
        layout: The state root.
        now: Clock returning the current UTC time.
    """
    moment = now()
    with hold(layout.state_lock):
        _clear_dead_worker(layout, moment)
        _delete_due_audio(layout, moment)
        lifecycle.purge_expired(layout, moment)
