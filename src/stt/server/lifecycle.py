"""Job state changes and the responses that follow them.

Every function here must be called with ``state.lock`` held by the caller.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal

from stt.server.deadlines import parse_time
from stt.server.layout import Layout, atomic_write_text, is_request_name, remove_tree
from stt.server.records import (
    Audio,
    DeletedBy,
    Failure,
    Job,
    load_audio,
    load_audios,
    load_jobs,
    queued_in_order,
    save_audio,
    save_job,
)
from stt.server.responses import job_status

_RESPONSE_MODE = 0o640
_MARKER_MODE = 0o600

EndState = Literal["done", "failed", "deleted"]
#: Wraps the cleanup of one item; given a log label (a job or recording id, or a
#: fixed name for a path). The sweep's guard logs and swallows the item's error.
Guard = Callable[[str], AbstractContextManager[None]]

_AUDIO_DELETED = Failure("audio_deleted", "The recording was deleted before the job finished.")
_STALE_AFTER = timedelta(hours=1)
_LOG_INBOX = "removed an inbox file older than one hour"
_LOG_TEMP = "removed a stale temporary file or folder"
_LOG_AUDIO_FOLDER = "removed a recording folder without a record"
_INTERRUPTED = Failure("interrupted", "The worker stopped before the job finished.")
_LOG_MARKER = "removed a queue marker of a job that is not queued"


def unguarded(label: str) -> AbstractContextManager[None]:
    """A guard that lets every error propagate."""
    return nullcontext()


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


def is_due(audio: Audio, now: datetime) -> bool:
    """Returns whether the daily purge must delete `audio` at `now`: its ``delete_at`` has come and it still exists."""
    return audio.deleted_at is None and now >= audio.delete_at


def write_users_responses(layout: Layout, audio: Audio, now: datetime) -> None:
    """Rewrites the response of every job that uses `audio`."""
    for job in load_jobs(layout):
        if job.request_id in audio.users:
            write_job_response(layout, job, now)


def _remove_audio(layout: Layout, audio: Audio, deleted_by: DeletedBy, now: datetime) -> Audio:
    """Deletes the file folder, keeps the record with the deletion marks, refreshes the users' responses."""
    folder = layout.audio / audio.audio_id
    if folder.exists():
        shutil.rmtree(folder)
    removed = replace(audio, deleted_at=now, deleted_by=deleted_by)
    save_audio(layout, removed)
    write_users_responses(layout, removed, now)
    return removed


def _remove_while_engine_may_write(folder: Path) -> None:
    """Removes `folder`, tolerating a vanished folder or one a live engine refills meanwhile.

    A still-running engine may write into its folder until the worker kills it, and the
    worker removes the folder again after the kill, so a partial removal here is fine.
    Any other error propagates.
    """
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        # intentional-skip: the folder is already gone.
        pass
    except OSError as error:
        if error.errno != errno.ENOTEMPTY:
            raise
        # intentional-skip: the worker removes the refilled folder again after the kill.


def finish(layout: Layout, job: Job, state: EndState, now: datetime, failure: Failure | None = None) -> Job:
    """Moves a job to an end state; the only way a job reaches one.

    Also removes the job's hints, queue marker, engine work folder and result staging
    folder. The recording stays: only a delete request or the daily purge deletes it. A
    job that already ended (``done`` or ``failed``, on its way to ``deleted``) keeps its
    ``finished_at``.

    Args:
        layout: The state root.
        job: The job to end.
        state: ``done``, ``failed`` or ``deleted``.
        now: Finish time.
        failure: Why the job failed; only for ``failed``.

    Returns:
        The saved end-state job.
    """
    finished_at = now if job.finished_at is None else job.finished_at
    finished = replace(job, state=state, finished_at=finished_at, failure=failure, hints=None)
    save_job(layout, finished)
    marker_path(layout, job).unlink(missing_ok=True)
    _remove_while_engine_may_write(layout.work / job.request_id)
    _remove_while_engine_may_write(layout.outbox / f".{job.request_id}.tmp")
    write_job_response(layout, finished, now)
    rewrite_queued_responses(layout, now)
    return finished


def purge_audio(layout: Layout, audio_id: str, now: datetime) -> list[Job]:
    """Deletes a recording at its daily purge and fails the jobs still waiting for it.

    Only the file folder goes; the audio record stays so responses can report
    ``deleted_at``. A ``running`` job is failed here too; stopping its engine is
    the worker's business.

    Args:
        layout: The state root.
        audio_id: The recording to delete, not yet deleted.
        now: Deletion time.

    Returns:
        The jobs that were failed with ``audio_deleted``.
    """
    audio = _remove_audio(layout, load_audio(layout, audio_id), "purge", now)
    waiting = [
        job for job in load_jobs(layout) if job.request_id in audio.users and job.state in ("queued", "running")
    ]
    return [finish(layout, job, "failed", now, _AUDIO_DELETED) for job in waiting]


def delete_everything(layout: Layout, job: Job, now: datetime) -> None:
    """Answers a delete request: removes `job`'s recording and ends every job that uses it as ``deleted``.

    Each such job loses its result and hints; ``result_deleted_at`` is set where a
    result existed. A running job's engine is stopped by the worker, which sees the
    job is no longer ``running``. A recording the daily purge already deleted keeps
    its purge marks.

    Args:
        layout: The state root.
        job: The target job, in any state but ``deleted``.
        now: Deletion time.
    """
    audio = load_audio(layout, job.audio_id)
    if audio.deleted_at is None:
        audio = _remove_audio(layout, audio, "request", now)
    for user in load_jobs(layout):
        if user.request_id in audio.users:
            result = layout.outbox / user.request_id
            if result.exists():
                shutil.rmtree(result)
            marked = replace(user, deleted_at=now, result_deleted_at=now if user.state == "done" else None)
            finish(layout, marked, "deleted", now)


def clear_dead_worker(layout: Layout, now: datetime, guard: Guard = unguarded) -> list[Job]:
    """Cleans up after a worker that died; the caller knows no worker is alive.

    Running jobs become ``failed(interrupted)`` and lose a result published just
    before the death. Every ``work/*`` entry and every ``outbox/.*.tmp`` staging
    folder is removed.

    Args:
        layout: The state root.
        now: Cleanup time.
        guard: Wraps each job and each folder.

    Returns:
        The jobs that were failed with ``interrupted``.
    """
    interrupted = []
    for job in load_jobs(layout):
        if job.state == "running":
            with guard(job.request_id):
                _remove(layout.outbox / job.request_id)
                interrupted.append(finish(layout, job, "failed", now, _INTERRUPTED))
    for entry in layout.work.iterdir():
        with guard("work entry"):
            _remove(entry)
    for staging in layout.outbox.glob(".*.tmp"):
        if staging.is_dir():
            with guard("staging folder"):
                _remove(staging)
    return interrupted


def _remove(path: Path) -> None:
    """Removes a file, link or folder; one that is already gone is fine."""
    try:
        if path.is_dir() and not path.is_symlink():
            remove_tree(path)
        else:
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        # intentional-skip: removed meanwhile (intake, or the caller renaming its file), which is the goal.
        return


def _is_stale(path: Path, now: datetime) -> bool:
    """True once `path` was last changed more than an hour before `now`, by inode change time; False if gone."""
    try:
        changed = os.lstat(path).st_ctime
    except FileNotFoundError:
        # intentional-skip: removed meanwhile, so nothing is left to clean.
        return False
    return now - datetime.fromtimestamp(changed, now.tzinfo) > _STALE_AFTER


def _remove_stale(paths: list[Path], now: datetime, message: str, label: str, guard: Guard) -> None:
    for path in paths:
        with guard(label):
            if _is_stale(path, now):
                _remove(path)
                print(message, flush=True)


def _purge_records(layout: Layout, now: datetime, guard: Guard) -> None:
    """Removes the response, result and record of every expired job, then audio records nobody uses."""
    jobs = load_jobs(layout)
    for job in jobs:
        if job.expires_at <= now:
            with guard(job.request_id):
                _remove(layout.outbox / job.request_id)
                (layout.outbox / f"{job.request_id}.json").unlink(missing_ok=True)
                shutil.rmtree(layout.jobs / job.request_id)
    alive = {job.request_id for job in jobs if job.expires_at > now}
    for audio in load_audios(layout):
        if alive.isdisjoint(audio.users):
            with guard(audio.audio_id):
                (layout.audio / f"{audio.audio_id}.json").unlink()
    for response in layout.outbox.glob("*.json"):
        if is_request_name(response.name) and not (layout.jobs / response.stem / "job.json").exists():
            with guard(response.stem):
                if parse_time(json.loads(response.read_text(encoding="utf-8"))["expires_at"]) <= now:
                    response.unlink()


def purge_expired(layout: Layout, now: datetime, guard: Guard) -> None:
    """Removes everything whose time has come; the caller holds ``state.lock``.

    Expired jobs lose their response, result and record; a recording's record goes
    with its last user. A response without a job record (a rejection or a delete
    answer) goes at its own ``expires_at``. Inbox files, temporary files and folders
    and recording folders without a record go once an hour has passed since their
    last change (ctime); each such removal logs one fixed line without any name. The
    inbox goes last, since its files belong to another user and may resist removal.

    Args:
        layout: The state root.
        now: The current time.
        guard: Wraps each job, record and path.
    """
    _purge_records(layout, now, guard)
    temporary = [
        *layout.outbox.glob(".*.tmp"),
        *layout.jobs.glob("*/.*.tmp"),
        *layout.audio.glob(".*.tmp"),
    ]
    _remove_stale(temporary, now, _LOG_TEMP, "temporary entry", guard)
    unrecorded = [
        folder
        for folder in layout.audio.iterdir()
        if folder.is_dir() and not (layout.audio / f"{folder.name}.json").exists()
    ]
    _remove_stale(unrecorded, now, _LOG_AUDIO_FOLDER, "recording folder", guard)
    _remove_stale(list(layout.inbox.iterdir()), now, _LOG_INBOX, "inbox entry", guard)


def clear_stale_markers(layout: Layout, guard: Guard) -> None:
    """Removes every queue marker whose job is gone or no longer ``queued``; the caller holds ``state.lock``.

    Such a marker is left when a process dies between saving a job's new state
    and removing the marker; left alone, it would wake the worker forever.

    Args:
        layout: The state root.
        guard: Wraps each marker.
    """
    waiting = {marker_path(layout, job).name for job in load_jobs(layout) if job.state == "queued"}
    for marker in layout.queue.iterdir():
        if marker.name not in waiting:
            with guard("queue marker"):
                _remove(marker)
                print(_LOG_MARKER, flush=True)
