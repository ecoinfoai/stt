"""Job state changes and the responses that follow them.

Every function here must be called with ``state.lock`` held by the caller.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from stt.server.layout import Layout, atomic_write_text
from stt.server.records import Failure, Job, load_audio, load_jobs, queued_in_order, save_job
from stt.server.responses import job_status

_RESPONSE_MODE = 0o640
_MARKER_MODE = 0o600

EndState = Literal["done", "failed", "deleted"]


def write_response(layout: Layout, doc: dict[str, Any]) -> None:
    """Writes `doc` to ``outbox/<request_id>.json`` atomically with mode 0640."""
    atomic_write_text(layout.outbox / f"{doc['request_id']}.json", json.dumps(doc, indent=2), _RESPONSE_MODE)


def marker_path(layout: Layout, job: Job) -> Path:
    """Returns the queue marker path ``queue/<seq:08d>-<request_id>`` of `job`."""
    return layout.queue / f"{job.seq:08d}-{job.request_id}"


def create_marker(layout: Layout, job: Job) -> None:
    """Creates the empty queue marker of `job` that wakes the worker."""
    marker_path(layout, job).touch(mode=_MARKER_MODE)


def write_job_response(layout: Layout, job: Job, now: datetime) -> None:
    """Writes the response of one job from the records on disk."""
    write_response(layout, job_status(job, load_audio(layout, job.audio_id), load_jobs(layout), now))


def rewrite_queued_responses(layout: Layout, now: datetime) -> None:
    """Rewrites the response of every ``queued`` job so queue positions are current."""
    jobs = load_jobs(layout)
    for job in queued_in_order(jobs):
        write_response(layout, job_status(job, load_audio(layout, job.audio_id), jobs, now))


def start(layout: Layout, job: Job, now: datetime) -> Job:
    """Moves a ``queued`` job to ``running``.

    Args:
        layout: The state root.
        job: The queued job.
        now: Start time.

    Returns:
        The saved ``running`` job.
    """
    started = replace(job, state="running", started_at=now)
    save_job(layout, started)
    marker_path(layout, job).unlink(missing_ok=True)
    write_job_response(layout, started, now)
    rewrite_queued_responses(layout, now)
    return started


def finish(layout: Layout, job: Job, state: EndState, now: datetime, failure: Failure | None = None) -> Job:
    """Moves a job to an end state; the only way a job reaches one.

    Args:
        layout: The state root.
        job: The ``queued`` or ``running`` job.
        state: ``done``, ``failed`` or ``deleted``.
        now: Finish time.
        failure: Why the job failed; only for ``failed``.

    Returns:
        The saved end-state job.
    """
    finished = replace(job, state=state, finished_at=now, failure=failure)
    save_job(layout, finished)
    marker_path(layout, job).unlink(missing_ok=True)
    write_job_response(layout, finished, now)
    rewrite_queued_responses(layout, now)
    return finished
