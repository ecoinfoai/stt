"""Internal Job and Audio records: frozen dataclasses, JSON round-trip, atomic save and load.

Times are UTC-aware ``datetime`` in memory and contract time strings on disk.
Records are trusted internal data written by stt itself, so loading does no
validation beyond what decoding needs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Literal

from stt.server.deadlines import format_time, parse_time
from stt.server.layout import Layout, atomic_write_text

JobState = Literal["queued", "running", "done", "failed", "deleted"]
DeletedBy = Literal["purge", "request"]

_RECORD_MODE = 0o600
_JOB_TIME_FIELDS = (
    "accepted_at",
    "expires_at",
    "started_at",
    "finished_at",
    "result_deleted_at",
    "deleted_at",
    "duplicate_refused_at",
)
_JOB_FOLDER_MODE = 0o700


@dataclass(frozen=True)
class Failure:
    """Why a job failed.

    Attributes:
        reason: Failure reason code.
        detail: Fixed English sentence for the reason.
    """

    reason: str
    detail: str


@dataclass(frozen=True)
class Job:
    """An accepted transcribe request."""

    request_id: str
    requested_by: str
    seq: int
    accepted_at: datetime
    audio_id: str
    language: str
    diarize: bool
    hints: tuple[str, ...] | None
    duration_s: float
    state: JobState
    expires_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure: Failure | None = None
    result_deleted_at: datetime | None = None
    deleted_at: datetime | None = None
    duplicate_refused_at: datetime | None = None


@dataclass(frozen=True)
class Audio:
    """A received recording.

    Attributes:
        delete_at: 23:58 Asia/Seoul on the Seoul date of ``received_at`` (already past for a receipt
            between 23:58 and 24:00); a delete request may come first.
        users: Request ids of the jobs that use this recording.
        deleted_by: ``purge`` (the daily purge) or ``request`` (a delete request).
    """

    audio_id: str
    original_name: str
    owner: str
    received_at: datetime
    delete_at: datetime
    users: tuple[str, ...]
    deleted_at: datetime | None = None
    deleted_by: DeletedBy | None = None


def _dump_time(t: datetime | None) -> str | None:
    return None if t is None else format_time(t)


def _load_time(text: str | None) -> datetime | None:
    return None if text is None else parse_time(text)


def _job_to_dict(job: Job) -> dict[str, Any]:
    data = asdict(job)
    for key in _JOB_TIME_FIELDS:
        data[key] = _dump_time(getattr(job, key))
    data["hints"] = None if job.hints is None else list(job.hints)
    return data


def _job_from_dict(data: dict[str, Any]) -> Job:
    failure = data["failure"]
    hints = data["hints"]
    return Job(
        request_id=data["request_id"],
        requested_by=data["requested_by"],
        seq=data["seq"],
        accepted_at=parse_time(data["accepted_at"]),
        audio_id=data["audio_id"],
        language=data["language"],
        diarize=data["diarize"],
        hints=None if hints is None else tuple(hints),
        duration_s=data["duration_s"],
        state=data["state"],
        expires_at=parse_time(data["expires_at"]),
        started_at=_load_time(data["started_at"]),
        finished_at=_load_time(data["finished_at"]),
        failure=None if failure is None else Failure(**failure),
        result_deleted_at=_load_time(data["result_deleted_at"]),
        deleted_at=_load_time(data["deleted_at"]),
        duplicate_refused_at=_load_time(data["duplicate_refused_at"]),
    )


def _audio_to_dict(audio: Audio) -> dict[str, Any]:
    return {
        "audio_id": audio.audio_id,
        "original_name": audio.original_name,
        "owner": audio.owner,
        "received_at": format_time(audio.received_at),
        "delete_at": format_time(audio.delete_at),
        "users": list(audio.users),
        "deleted_at": _dump_time(audio.deleted_at),
        "deleted_by": audio.deleted_by,
    }


def _audio_from_dict(data: dict[str, Any]) -> Audio:
    return Audio(
        audio_id=data["audio_id"],
        original_name=data["original_name"],
        owner=data["owner"],
        received_at=parse_time(data["received_at"]),
        delete_at=parse_time(data["delete_at"]),
        users=tuple(data["users"]),
        deleted_at=_load_time(data["deleted_at"]),
        deleted_by=data["deleted_by"],
    )


def save_job(layout: Layout, job: Job) -> None:
    """Writes `job` to ``jobs/<request_id>/job.json`` atomically with mode 0600."""
    folder = layout.jobs / job.request_id
    folder.mkdir(mode=_JOB_FOLDER_MODE, exist_ok=True)
    atomic_write_text(folder / "job.json", json.dumps(_job_to_dict(job), indent=2), _RECORD_MODE)


def load_job(layout: Layout, request_id: str) -> Job:
    """Reads one job record.

    Raises:
        FileNotFoundError: If the job has no record.
    """
    return _job_from_dict(json.loads((layout.jobs / request_id / "job.json").read_text(encoding="utf-8")))


def load_jobs(layout: Layout) -> list[Job]:
    """Reads every job record, ordered by ``seq``. Folders without a record are skipped."""
    jobs = [
        _job_from_dict(json.loads(path.read_text(encoding="utf-8"))) for path in layout.jobs.glob("*/job.json")
    ]
    return sorted(jobs, key=lambda job: job.seq)


def save_audio(layout: Layout, audio: Audio) -> None:
    """Writes `audio` to ``audio/<audio_id>.json`` atomically with mode 0600.

    The record lives beside, not inside, the file folder ``audio/<audio_id>/``,
    so deleting the recording leaves the record.
    """
    atomic_write_text(layout.audio / f"{audio.audio_id}.json", json.dumps(_audio_to_dict(audio), indent=2), _RECORD_MODE)


def load_audio(layout: Layout, audio_id: str) -> Audio:
    """Reads one audio record.

    Raises:
        FileNotFoundError: If the audio has no record.
    """
    return _audio_from_dict(json.loads((layout.audio / f"{audio_id}.json").read_text(encoding="utf-8")))


def load_audios(layout: Layout) -> list[Audio]:
    """Reads every audio record, ordered by ``audio_id``."""
    return [load_audio(layout, path.stem) for path in sorted(layout.audio.glob("*.json"))]


def next_seq(jobs: Iterable[Job]) -> int:
    """Returns the sequence number for the next accepted job: highest ``seq`` + 1, or 1."""
    return max((job.seq for job in jobs), default=0) + 1


def queued_in_order(jobs: Iterable[Job]) -> list[Job]:
    """Returns the ``queued`` jobs, lowest ``seq`` first."""
    return sorted((job for job in jobs if job.state == "queued"), key=lambda job: job.seq)


def queue_position(job: Job, jobs: Iterable[Job]) -> int | None:
    """Returns the 1-based place in line, or None if `job` is not ``queued``.

    A running job is not counted: position = number of queued jobs with a lower
    ``seq`` + 1.
    """
    if job.state != "queued":
        return None
    return 1 + sum(1 for other in jobs if other.state == "queued" and other.seq < job.seq)
