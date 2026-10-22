"""Intake stage: turns inbox request files into queued jobs or answers.

The caller holds ``intake.lock``. ``state.lock`` is taken here, briefly, around
every decision that reads or changes records and responses.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable

from stt.server import contract, deadlines, lifecycle
from stt.server.layout import (
    Layout,
    UnsafeFileError,
    atomic_write_text,
    hold,
    is_request_name,
    open_attachment,
    read_request_bytes,
)
from stt.server.records import (
    Audio,
    Job,
    load_job,
    load_jobs,
    next_seq,
    save_audio,
    save_job,
)
from stt.server.responses import delete_outcome, job_status, refusal, rejected_status

_AUDIO_FOLDER_MODE = 0o700
_AUDIO_FILE_MODE = 0o600
_REQUEST_SUFFIX = ".json"
_ATTACHMENT_INFIX = ".audio."

_NOT_A_REQUEST_LOG = "dropped an inbox file whose name is not a request id"
_UNSAFE_DETAILS = {
    "symlink": "request: file is a symbolic link",
    "hardlink": "request: file has more than one link",
    "not_regular": "request: file is not a regular file",
    "too_large": "request: file is larger than 1 MiB",
    "unreadable": "request: file is not readable",
}
_NOT_JSON_DETAIL = "request: file is not UTF-8 JSON"
_NAME_MISMATCH_DETAIL = "request_id: must equal the file name"
_ATTACHMENT_NAME_DETAIL = "audio.file: name must start with the request id"
_ATTACHMENT_DETAIL = "audio.file: attachment is missing or not a safe regular file"
_LANGUAGE_DETAIL = "language: language is not supported"
_FROM_REQUEST_DETAIL = "from_request is not supported."


def probe_duration(path: Path) -> float:
    """Returns the length of a recording in seconds.

    Uses the container duration; when the container has none, sums the audio
    packet durations without decoding.

    Args:
        path: The recording.

    Returns:
        Duration in seconds.
    """
    import av

    with av.open(str(path)) as container:
        if container.duration is not None:
            return float(container.duration) / av.time_base
        stream = container.streams.audio[0]
        total = sum(
            float(packet.duration * stream.time_base)
            for packet in container.demux(stream)
            if packet.duration is not None
        )
        return total


def _log(request_id: str, state: str) -> None:
    print(f"{request_id} {state}", flush=True)


def _discard(layout: Layout, stem: str) -> None:
    """Removes ``<stem>.json`` and every ``<stem>.audio.*`` from the inbox."""
    request_name = f"{stem}{_REQUEST_SUFFIX}"
    attachment_prefix = f"{stem}{_ATTACHMENT_INFIX}"
    for entry in os.scandir(layout.inbox):
        if entry.name == request_name or entry.name.startswith(attachment_prefix):
            if entry.is_dir(follow_symlinks=False):
                os.rmdir(entry.path)
            else:
                os.unlink(entry.path)


def _pending(layout: Layout) -> list[Path]:
    entries = [entry for entry in os.scandir(layout.inbox) if entry.name.endswith(_REQUEST_SUFFIX)]
    entries.sort(key=lambda entry: (entry.stat(follow_symlinks=False).st_mtime, entry.name))
    return [Path(entry.path) for entry in entries]


def _duplicate(layout: Layout, request_id: str, now: datetime) -> bool:
    """Marks a repeated request id on the job record or the existing response; False if new."""
    response_path = layout.outbox / f"{request_id}.json"
    job_path = layout.jobs / request_id / "job.json"
    if job_path.exists():
        job = replace(load_job(layout, request_id), duplicate_refused_at=now)
        save_job(layout, job)
        lifecycle.write_job_response(layout, job, now)
        return True
    if response_path.exists():
        doc = json.loads(response_path.read_text(encoding="utf-8"))
        doc["duplicate_refused_at"] = deadlines.format_time(now)
        atomic_write_text(response_path, json.dumps(doc, indent=2), 0o640)
        return True
    return False


def _answer(layout: Layout, doc: dict[str, Any]) -> None:
    with hold(layout.state_lock):
        lifecycle.write_response(layout, doc)


def _refuse(layout: Layout, request_id: str, detail: str, now: datetime) -> None:
    _answer(layout, refusal(request_id, detail, now))
    _log(request_id, "refused")


def _reject(layout: Layout, request_id: str, detail: str, now: datetime) -> None:
    _answer(layout, rejected_status(request_id, "invalid_request", detail, now))
    _log(request_id, "failed invalid_request")


def _delete_outcome(layout: Layout, body: dict[str, Any], now: datetime) -> str:
    """Applies a delete request to its target and returns the outcome; the state lock is held."""
    target = layout.jobs / body["target"] / "job.json"
    job = load_job(layout, body["target"]) if target.exists() else None
    if job is None or job.requested_by != body["requested_by"]:
        return "refused"
    if body["scope"] == "everything":
        if job.state == "deleted":
            return "already_deleted"
        lifecycle.delete_everything(layout, job, now)
        return "deleted"
    if job.result_deleted_at is not None:
        return "already_deleted"
    if job.state != "done":
        return "no_result"
    lifecycle.delete_result(layout, job, now)
    return "deleted"


def _process_delete(layout: Layout, request_id: str, body: dict[str, Any], now: datetime) -> None:
    with hold(layout.state_lock):
        outcome = _delete_outcome(layout, body, now)
        lifecycle.write_response(layout, delete_outcome(request_id, body["target"], body["scope"], outcome, now))
    _log(request_id, f"delete {outcome}")


def _copy_attachment(source: BinaryIO, folder: Path, extension: str) -> Path:
    folder.mkdir(mode=_AUDIO_FOLDER_MODE, exist_ok=True)
    target = folder / f"audio.{extension}"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, _AUDIO_FILE_MODE)
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), _AUDIO_FILE_MODE)
        shutil.copyfileobj(source, handle)
    return target


def _accept(layout: Layout, body: dict[str, Any], source: BinaryIO, now: datetime) -> None:
    request_id: str = body["request_id"]
    attachment_name: str = body["audio"]["file"]
    folder = layout.audio / request_id
    try:
        copy = _copy_attachment(source, folder, attachment_name.rsplit(".", 1)[1])
        duration_s = probe_duration(copy)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    policy: dict[str, str | datetime] = (
        {"after_job": request_id}
        if body["delete_audio"] == "after_job"
        else {"at": deadlines.next_midnight(now)}
    )
    hints = body.get("hints")
    with hold(layout.state_lock):
        job = Job(
            request_id=request_id,
            requested_by=body["requested_by"],
            seq=next_seq(load_jobs(layout)),
            accepted_at=now,
            audio_id=request_id,
            language=body["language"],
            diarize=body["diarize"],
            hints=None if hints is None else tuple(hints),
            delete_audio=body["delete_audio"],
            duration_s=duration_s,
            state="queued",
            expires_at=deadlines.expires_at(now),
        )
        save_audio(
            layout,
            Audio(
                audio_id=request_id,
                original_name=attachment_name,
                owner=body["requested_by"],
                received_at=now,
                hard_deadline=deadlines.hard_deadline(now),
                policy=policy,
                users=(request_id,),
            ),
        )
        save_job(layout, job)
        lifecycle.create_marker(layout, job)
        lifecycle.rewrite_queued_responses(layout, now)
    _log(request_id, "queued")


def _process_transcribe(layout: Layout, request_id: str, body: dict[str, Any], now: datetime) -> None:
    audio = body["audio"]
    if "file" not in audio:
        supported = contract.language_supported(body["language"])
        _reject(layout, request_id, _FROM_REQUEST_DETAIL if supported else _LANGUAGE_DETAIL, now)
        return
    if not audio["file"].startswith(f"{request_id}{_ATTACHMENT_INFIX}"):
        _reject(layout, request_id, _ATTACHMENT_NAME_DETAIL, now)
        return
    try:
        source = open_attachment(layout.inbox / audio["file"])
    except UnsafeFileError:
        _reject(layout, request_id, _ATTACHMENT_DETAIL, now)
        return
    with source:
        if not contract.language_supported(body["language"]):
            _reject(layout, request_id, _LANGUAGE_DETAIL, now)
            return
        _accept(layout, body, source, now)


def _process(layout: Layout, path: Path, now: Callable[[], datetime]) -> None:
    if not is_request_name(path.name):
        print(_NOT_A_REQUEST_LOG, flush=True)
        return
    request_id = path.name[: -len(_REQUEST_SUFFIX)]
    moment = now()
    with hold(layout.state_lock):
        if _duplicate(layout, request_id, moment):
            _log(request_id, "duplicate")
            return
    try:
        data = read_request_bytes(path)
    except UnsafeFileError as error:
        _refuse(layout, request_id, _UNSAFE_DETAILS[error.reason], moment)
        return
    try:
        body = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        _refuse(layout, request_id, _NOT_JSON_DETAIL, moment)
        return
    problem = contract.validate_request(body)
    if problem is not None:
        _refuse(layout, request_id, problem, moment)
        return
    if body["request_id"] != request_id:
        if body["kind"] == "delete":
            _refuse(layout, request_id, _NAME_MISMATCH_DETAIL, moment)
        else:
            _reject(layout, request_id, _NAME_MISMATCH_DETAIL, moment)
        return
    if body["kind"] == "delete":
        _process_delete(layout, request_id, body, moment)
        return
    _process_transcribe(layout, request_id, body, moment)


def run_once(layout: Layout, now: Callable[[], datetime]) -> None:
    """Processes every ``*.json`` in the inbox and removes each one it saw.

    Files (and directories) named ``*.json`` are taken in (mtime, name) order. A request that cannot be used is
    answered and its attachment (named by the request file's own name) removed.
    Non-file ``OSError``s propagate and leave the file in place.

    Args:
        layout: The state root.
        now: Clock returning the current UTC time.
    """
    for path in _pending(layout):
        _process(layout, path, now)
        _discard(layout, path.name[: -len(_REQUEST_SUFFIX)])
