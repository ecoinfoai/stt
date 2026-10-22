"""Pure builders for response documents (``requests/outbox/<request_id>.json``).

Every builder returns a dict valid against ``response.schema.json``. Responses
never carry the requester, hints or transcript text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from stt.server import deadlines
from stt.server.deadlines import format_time
from stt.server.records import Audio, Failure, Job, queue_position

_SCHEMA_VERSION = 1


def _failure(failure: Failure) -> dict[str, str]:
    return {"reason": failure.reason, "detail": failure.detail}


def _audio(audio: Audio) -> dict[str, str]:
    doc = {"delete_at": format_time(audio.delete_at)}
    if audio.deleted_at is not None:
        doc["deleted_at"] = format_time(audio.deleted_at)
    return doc


def _result(job: Job) -> dict[str, str]:
    doc = {"transcript": f"{job.request_id}/transcript.txt", "data": f"{job.request_id}/result.json"}
    if job.result_deleted_at is not None:
        doc["deleted_at"] = format_time(job.result_deleted_at)
    return doc


def _with_duplicate(doc: dict[str, Any], duplicate_refused_at: datetime | None) -> dict[str, Any]:
    if duplicate_refused_at is not None:
        doc["duplicate_refused_at"] = format_time(duplicate_refused_at)
    return doc


def job_status(job: Job, audio: Audio, jobs: Iterable[Job], now: datetime) -> dict[str, Any]:
    """Builds the status document of an accepted transcribe job.

    Args:
        job: The job to describe.
        audio: The recording the job uses.
        jobs: Every job record, used for the place in line.
        now: Becomes ``updated_at``.
    """
    doc: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "transcribe",
        "request_id": job.request_id,
        "state": job.state,
    }
    position = queue_position(job, jobs)
    if position is not None:
        doc["queue_position"] = position
    doc["accepted_at"] = format_time(job.accepted_at)
    if job.started_at is not None:
        doc["started_at"] = format_time(job.started_at)
    if job.finished_at is not None:
        doc["finished_at"] = format_time(job.finished_at)
    doc["updated_at"] = format_time(now)
    doc["expires_at"] = format_time(job.expires_at)
    doc["audio"] = _audio(audio)
    # A deleted job keeps its result entry only if it had a result to delete.
    if job.state == "done" or (job.state == "deleted" and job.result_deleted_at is not None):
        doc["result"] = _result(job)
    if job.state == "failed" and job.failure is not None:
        doc["failure"] = _failure(job.failure)
    if job.state == "deleted" and job.deleted_at is not None:
        doc["deleted"] = {"at": format_time(job.deleted_at)}
    return _with_duplicate(doc, job.duplicate_refused_at)


def rejected_status(
    request_id: str,
    reason: str,
    detail: str,
    now: datetime,
    duplicate_refused_at: datetime | None = None,
) -> dict[str, Any]:
    """Builds the ``failed`` status of a transcribe request rejected at intake (no ``accepted_at``)."""
    doc: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "transcribe",
        "request_id": request_id,
        "state": "failed",
        "finished_at": format_time(now),
        "updated_at": format_time(now),
        "expires_at": format_time(deadlines.next_purge(now)),
        "failure": {"reason": reason, "detail": detail},
    }
    return _with_duplicate(doc, duplicate_refused_at)


def delete_outcome(
    request_id: str,
    target: str,
    outcome: str,
    now: datetime,
    duplicate_refused_at: datetime | None = None,
) -> dict[str, Any]:
    """Builds the answer to a delete request."""
    doc: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "delete",
        "request_id": request_id,
        "target": target,
        "outcome": outcome,
        "processed_at": format_time(now),
        "expires_at": format_time(deadlines.next_purge(now)),
    }
    return _with_duplicate(doc, duplicate_refused_at)


def refusal(
    request_id: str,
    detail: str,
    now: datetime,
    duplicate_refused_at: datetime | None = None,
) -> dict[str, Any]:
    """Builds the answer to a request file that could not be used (``invalid_request``)."""
    doc: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "invalid",
        "request_id": request_id,
        "failure": {"reason": "invalid_request", "detail": detail},
        "processed_at": format_time(now),
        "expires_at": format_time(deadlines.next_purge(now)),
    }
    return _with_duplicate(doc, duplicate_refused_at)
