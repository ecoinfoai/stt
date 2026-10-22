"""Job state changes and the responses that follow them.

Every function here must be called with ``state.lock`` held by the caller.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from stt.server import deadlines
from stt.server.layout import Layout, atomic_write_text
from stt.server.records import (
    Audio,
    DeletedBy,
    Failure,
    Job,
    load_audio,
    load_jobs,
    queued_in_order,
    save_audio,
    save_job,
)
from stt.server.responses import job_status

_RESPONSE_MODE = 0o640
_MARKER_MODE = 0o600

EndState = Literal["done", "failed", "deleted"]

_AUDIO_DELETED_DETAIL = "The recording was deleted before the job finished."
_INTERRUPTED = Failure("interrupted", "The worker stopped before the job finished.")
_DELETED_BY: dict[str, DeletedBy] = {
    "midnight": "schedule",
    "retention_limit": "schedule",
    "deleted_on_request": "request",
}


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


def due_cause(audio: Audio, now: datetime) -> str | None:
    """Returns why `audio` is already due for deletion at `now`, or None if it is not.

    Args:
        audio: The recording's record.
        now: The current time.

    Returns:
        ``midnight`` once the policy time has passed (and is not later than the hard
        deadline), ``retention_limit`` once the hard deadline has passed first.
    """
    if now < deadlines.audio_delete_at(audio.policy, audio.hard_deadline):
        return None
    if "at" in audio.policy and audio.policy["at"] <= audio.hard_deadline:
        return "midnight"
    return "retention_limit"


def _remove_audio(layout: Layout, audio: Audio, deleted_by: DeletedBy, now: datetime) -> Audio:
    """Deletes the file folder, keeps the record with the deletion marks, refreshes the users' responses."""
    folder = layout.audio / audio.audio_id
    if folder.exists():
        shutil.rmtree(folder)
    removed = replace(audio, deleted_at=now, deleted_by=deleted_by)
    save_audio(layout, removed)
    for job in load_jobs(layout):
        if job.request_id in audio.users:
            write_job_response(layout, job, now)
    return removed


def finish(layout: Layout, job: Job, state: EndState, now: datetime, failure: Failure | None = None) -> Job:
    """Moves a job to an end state; the only way a job reaches one.

    Also removes the job's hints, and deletes its recording when the recording's
    policy is ``after_job`` for this very job.

    Args:
        layout: The state root.
        job: The ``queued`` or ``running`` job.
        state: ``done``, ``failed`` or ``deleted``.
        now: Finish time.
        failure: Why the job failed; only for ``failed``.

    Returns:
        The saved end-state job.
    """
    finished = replace(job, state=state, finished_at=now, failure=failure, hints=None)
    save_job(layout, finished)
    marker_path(layout, job).unlink(missing_ok=True)
    audio = load_audio(layout, job.audio_id)
    if audio.deleted_at is None and dict(audio.policy) == {"after_job": job.request_id}:
        _remove_audio(layout, audio, "after_job", now)
    else:
        write_job_response(layout, finished, now)
    rewrite_queued_responses(layout, now)
    return finished


def delete_audio(layout: Layout, audio_id: str, cause: str, now: datetime) -> list[Job]:
    """Deletes a recording and fails the jobs still waiting for it.

    Only the file folder goes; the audio record stays so responses can report
    ``deleted_at``. A ``running`` job is failed here too; stopping its engine is
    the worker's business.

    Args:
        layout: The state root.
        audio_id: The recording to delete.
        cause: ``midnight``, ``retention_limit`` or ``deleted_on_request``.
        now: Deletion time.

    Returns:
        The jobs that were failed with ``audio_deleted``.
    """
    audio = _remove_audio(layout, load_audio(layout, audio_id), _DELETED_BY[cause], now)
    failure = Failure("audio_deleted", _AUDIO_DELETED_DETAIL, cause)
    waiting = [job for job in load_jobs(layout) if job.request_id in audio.users and job.state in ("queued", "running")]
    return [finish(layout, job, "failed", now, failure) for job in waiting]


def clear_dead_worker(layout: Layout, now: datetime) -> list[Job]:
    """Cleans up after a worker that died; the caller knows no worker is alive.

    Running jobs become ``failed(interrupted)`` and lose a result published just
    before the death. Every ``work/*`` entry and every ``outbox/.*.tmp`` staging
    folder is removed.

    Args:
        layout: The state root.
        now: Cleanup time.

    Returns:
        The jobs that were failed with ``interrupted``.
    """
    interrupted = []
    for job in load_jobs(layout):
        if job.state == "running":
            shutil.rmtree(layout.outbox / job.request_id, ignore_errors=True)
            interrupted.append(finish(layout, job, "failed", now, _INTERRUPTED))
    for entry in layout.work.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for staging in layout.outbox.glob(".*.tmp"):
        if staging.is_dir():
            shutil.rmtree(staging)
    return interrupted
