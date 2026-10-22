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
    load_audio,
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
_SOURCE_UNAVAILABLE_DETAIL = "The source recording is not available."
_UNREADABLE_AUDIO_DETAIL = "The recording could not be read as audio."
_AUDIO_TOO_LONG_DETAIL = "The recording is longer than 3 hours."


class UnreadableAudioError(Exception):
    """The recording cannot be opened, has no audio stream or cannot be demuxed."""


class AudioTooLongError(Exception):
    """The recording is longer than ``deadlines.MAX_AUDIO_S``."""


def probe_duration(path: Path) -> float:
    """Returns the length of a recording in seconds.

    Uses the container duration; when the container has none, sums the audio
    packet durations without decoding.

    Args:
        path: The recording.

    Returns:
        Duration in seconds.

    Raises:
        UnreadableAudioError: If the file cannot be opened, has no audio stream or fails to demux or parse.
    """
    import av

    try:
        with av.open(str(path), metadata_errors="ignore") as container:
            if not container.streams.audio:
                raise UnreadableAudioError
            if container.duration is not None:
                return float(container.duration) / av.time_base
            stream = container.streams.audio[0]
            return sum(
                float(packet.duration * stream.time_base)
                for packet in container.demux(stream)
                if packet.duration is not None
            )
    except (av.error.FFmpegError, ValueError, TypeError, ArithmeticError):
        raise UnreadableAudioError from None


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


def _fail(layout: Layout, request_id: str, reason: str, detail: str, now: datetime) -> None:
    _answer(layout, rejected_status(request_id, reason, detail, now))
    _log(request_id, f"failed {reason}")


def _reject(layout: Layout, request_id: str, detail: str, now: datetime) -> None:
    _fail(layout, request_id, "invalid_request", detail, now)


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


def _policy(body: dict[str, Any], now: datetime) -> dict[str, str | datetime]:
    """Returns the recording policy that the request `body`, accepted at `now`, asks for."""
    if body["delete_audio"] == "after_job":
        return {"after_job": body["request_id"]}
    return {"at": deadlines.next_midnight(now)}


def _copy_and_probe(source: BinaryIO, folder: Path, extension: str) -> float:
    """Copies the attachment into `folder` and returns its length; on any failure `folder` is removed.

    Raises:
        UnreadableAudioError: If the copy is not readable audio.
        AudioTooLongError: If the copy is longer than ``deadlines.MAX_AUDIO_S``.
    """
    try:
        duration_s = probe_duration(_copy_attachment(source, folder, extension))
        if duration_s > deadlines.MAX_AUDIO_S:
            raise AudioTooLongError
        return duration_s
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _accept(layout: Layout, body: dict[str, Any], source: BinaryIO, now: datetime) -> None:
    request_id: str = body["request_id"]
    attachment_name: str = body["audio"]["file"]
    try:
        duration_s = _copy_and_probe(source, layout.audio / request_id, attachment_name.rsplit(".", 1)[1])
    except UnreadableAudioError:
        _fail(layout, request_id, "unreadable_audio", _UNREADABLE_AUDIO_DETAIL, now)
        return
    except AudioTooLongError:
        _fail(layout, request_id, "audio_too_long", _AUDIO_TOO_LONG_DETAIL, now)
        return
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
                policy=_policy(body, now),
                users=(request_id,),
            ),
        )
        save_job(layout, job)
        lifecycle.create_marker(layout, job)
        lifecycle.rewrite_queued_responses(layout, now)
    _log(request_id, "queued")


def _usable_source(layout: Layout, body: dict[str, Any], now: datetime) -> Job | None:
    """Returns the earlier job whose recording the request may reuse, or None; the state lock is held.

    A recording whose delete time has passed is deleted here first, without waiting for the sweep.
    """
    source_id = body["audio"]["from_request"]
    if not (layout.jobs / source_id / "job.json").exists():
        return None
    source = load_job(layout, source_id)
    if source.requested_by != body["requested_by"]:
        return None
    audio = load_audio(layout, source.audio_id)
    cause = lifecycle.due_cause(audio, now)
    if cause is not None:
        for failed in lifecycle.delete_audio(layout, audio.audio_id, cause, now):
            _log(failed.request_id, "failed audio_deleted")
        return None
    return source if audio.deleted_at is None else None


def _accept_second_pass(layout: Layout, body: dict[str, Any], source: Job, now: datetime) -> None:
    """Queues a job on the source's recording and hands the recording's policy to this request; the state lock is held."""
    request_id: str = body["request_id"]
    hints = body.get("hints")
    job = Job(
        request_id=request_id,
        requested_by=body["requested_by"],
        seq=next_seq(load_jobs(layout)),
        accepted_at=now,
        audio_id=source.audio_id,
        language=body["language"],
        diarize=body["diarize"],
        hints=None if hints is None else tuple(hints),
        delete_audio=body["delete_audio"],
        duration_s=source.duration_s,
        state="queued",
        expires_at=deadlines.expires_at(now),
    )
    audio = load_audio(layout, source.audio_id)
    audio = replace(audio, policy=_policy(body, now), users=(*audio.users, request_id))
    save_audio(layout, audio)
    save_job(layout, job)
    lifecycle.create_marker(layout, job)
    lifecycle.write_users_responses(layout, audio, now)
    lifecycle.rewrite_queued_responses(layout, now)
    _log(request_id, "queued")


def _process_from_request(layout: Layout, request_id: str, body: dict[str, Any], now: datetime) -> None:
    if not contract.language_supported(body["language"]):
        _reject(layout, request_id, _LANGUAGE_DETAIL, now)
        return
    with hold(layout.state_lock):
        source = _usable_source(layout, body, now)
        if source is not None:
            _accept_second_pass(layout, body, source, now)
            return
    _fail(layout, request_id, "source_unavailable", _SOURCE_UNAVAILABLE_DETAIL, now)


def _process_transcribe(layout: Layout, request_id: str, body: dict[str, Any], now: datetime) -> None:
    audio = body["audio"]
    if "file" not in audio:
        _process_from_request(layout, request_id, body, now)
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
