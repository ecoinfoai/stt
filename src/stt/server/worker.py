"""Worker stage: runs queued jobs one at a time, reading the recording through the reader, then through the engine.

The caller holds ``worker.lock``. ``state.lock`` is taken here, briefly, to pick
and start a job and to publish and finish it; it is never held while the engine runs.
A job stays ``queued`` until the worker holds the GPU lock it shares with the command line.

A job has two inner stages under one time limit that starts with the first: the read stage decodes the
recording through the reader unit into ``work/<id>/audio.wav``, and the GPU stage runs the engine on that file.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Sequence

from stt import gpu_lock as gpu_lock_module
from stt.server import deadlines, lifecycle, reader_client
from stt.server.layout import Layout, describe_error, hold
from stt.server.reader_client import ReaderConfig
from stt.server.records import Failure, Job, load_audio, load_job, load_jobs, queued_in_order

DEFAULT_ENGINE_CMD: tuple[str, ...] = (sys.executable, "-m", "stt.server.engine")

_WORK_FOLDER_MODE = 0o700
# The setgid bit comes from outbox at mkdir: RestrictSUIDSGID refuses a chmod that asks for it.
_RESULT_FOLDER_MODE = 0o750
_RESULT_FILE_MODE = 0o640
_AUDIO_FILE_MODE = 0o600
_AUDIO_FILE = "audio.wav"
_SAMPLE_RATE_HZ = 16000
_SAMPLE_WIDTH_BYTES = 2
_RESULT_FILES = ("transcript.txt", "result.json")
_ENGINE_REASONS = frozenset(
    {"unreadable_audio", "gpu_out_of_memory", "gpu_unavailable", "models_unavailable", "engine_error"}
)
_MAX_DETAIL_CHARS = 500
_NO_VERDICT = Failure("engine_error", "The engine stopped without a verdict.")
_TIMEOUT = Failure("processing_timeout", "The job ran longer than its processing time limit.")
_Ending = Literal["ended", "stopped", "timeout"]


def _log(request_id: str, state: str) -> None:
    print(f"{request_id} {state}", flush=True)


def _prepare(layout: Layout, job: Job) -> tuple[Path, Path]:
    """Creates ``work/<request_id>/`` with the job's ``spec.json``.

    Returns:
        The work folder and the path of the job's recording.
    """
    audio = load_audio(layout, job.audio_id)
    recording = next((layout.audio / job.audio_id).glob("audio.*"))
    spec = {
        "request_id": job.request_id,
        "original_name": audio.original_name,
        "language": job.language,
        "diarize": job.diarize,
        "hints": None if job.hints is None else list(job.hints),
        "duration_s": job.duration_s,
    }
    work_dir = layout.work / job.request_id
    work_dir.mkdir(mode=_WORK_FOLDER_MODE)
    os.chmod(work_dir, _WORK_FOLDER_MODE)
    (work_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    return work_dir, recording


def _write_audio(work_dir: Path, samples: bytes | bytearray) -> None:
    """Writes 16 kHz mono s16le `samples` to ``audio.wav`` in `work_dir`, readable by the owner only."""
    path = work_dir / _AUDIO_FILE
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _AUDIO_FILE_MODE))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(_SAMPLE_WIDTH_BYTES)
        handle.setframerate(_SAMPLE_RATE_HZ)
        handle.writeframes(samples)


def _read(
    layout: Layout, request_id: str, recording: Path, work_dir: Path, reader: ReaderConfig, limit_s: float
) -> reader_client.Outcome:
    """Decodes `recording` through the reader and writes the result to ``audio.wav`` when the read is ``ok``.

    The decoded samples are released on return, before the GPU stage starts.
    """
    result = reader_client.decode(
        reader, recording, job_running=lambda: _is_running(layout, request_id), job_limit_s=limit_s
    )
    if result.outcome == "ok":
        _write_audio(work_dir, result.audio)
    return result.outcome


def _is_running(layout: Layout, request_id: str) -> bool:
    """True while the job's record exists and says ``running``; a purged record counts as not running."""
    with hold(layout.state_lock):
        if not (layout.jobs / request_id / "job.json").exists():
            return False
        return load_job(layout, request_id).state == "running"


def _run_engine(
    layout: Layout,
    request_id: str,
    engine_cmd: Sequence[str],
    work_dir: Path,
    models_dir: Path,
    poll_s: float,
    limit_s: float,
) -> _Ending:
    """Runs the engine until it ends; kills it when the job is no longer ``running`` or `limit_s` has passed.

    The limit is measured on the monotonic clock from the engine's launch.

    Returns:
        ``ended`` if the engine ended by itself, ``stopped`` if the job was taken away,
        ``timeout`` if the limit ran out.
    """
    env = {**os.environ, "TMPDIR": str(work_dir), "HF_HOME": str(models_dir), "HF_HUB_OFFLINE": "1"}
    process = subprocess.Popen(
        [*engine_cmd, str(work_dir)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + limit_s
    ending: _Ending = "ended"
    try:
        while process.poll() is None:
            if not _is_running(layout, request_id):
                ending = "stopped"
                break
            if time.monotonic() >= deadline:
                ending = "timeout"
                break
            time.sleep(poll_s)
    except BaseException:
        _kill(process)
        raise
    if ending != "ended":
        _kill(process)
    return ending


def _kill(process: subprocess.Popen[bytes]) -> None:
    """Kills the engine's whole process group and reaps the engine."""
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _verdict(work_dir: Path) -> Failure | None:
    """Reads ``outcome.json``; returns None for a usable ``done``, else why the job failed."""
    try:
        outcome = json.loads((work_dir / "outcome.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _NO_VERDICT
    if not isinstance(outcome, dict):
        return _NO_VERDICT
    if outcome.get("status") == "done":
        if all((work_dir / "out" / name).is_file() for name in _RESULT_FILES):
            return None
        return _NO_VERDICT
    reason, detail = outcome.get("reason"), outcome.get("detail")
    if (
        outcome.get("status") == "failed"
        and reason in _ENGINE_REASONS
        and isinstance(detail, str)
        and 0 < len(detail) <= _MAX_DETAIL_CHARS
    ):
        return Failure(reason, detail)
    return _NO_VERDICT


def _publish(layout: Layout, work_dir: Path, request_id: str) -> None:
    """Copies the result files to ``outbox/<request_id>/`` through a temporary folder."""
    staging = layout.outbox / f".{request_id}.tmp"
    try:
        staging.mkdir(mode=_RESULT_FOLDER_MODE)
        for name in _RESULT_FILES:
            target = staging / name
            shutil.copyfile(work_dir / "out" / name, target)
            os.chmod(target, _RESULT_FILE_MODE)
        staging.rename(layout.outbox / request_id)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _settle(layout: Layout, job: Job, work_dir: Path, failure: Failure | None, now: Callable[[], datetime]) -> None:
    """Publishes and finishes `job`, unless it is no longer ``running`` or its record was purged."""
    with hold(layout.state_lock):
        if not (layout.jobs / job.request_id / "job.json").exists():
            _log(job.request_id, "stopped")
            return
        current = load_job(layout, job.request_id)
        if current.state != "running":
            # intentional-skip: a delete or the daily purge ended the job and logged it.
            return
        if failure is None:
            _publish(layout, work_dir, job.request_id)
            lifecycle.finish(layout, current, "done", now())
            _log(job.request_id, "done")
        else:
            lifecycle.finish(layout, current, "failed", now(), failure)
            _log(job.request_id, f"failed {failure.reason}")


def _pick(layout: Layout, at: datetime) -> Job | None:
    """Returns the oldest queued job whose recording is not yet due for deletion.

    A queued job whose recording is already due is failed with ``audio_deleted``
    instead of being returned, without waiting for the sweep.

    Raises:
        RuntimeError: If a job is still first in line after failing it, which would
            otherwise spin while ``state.lock`` is held.
    """
    failed: str | None = None
    while True:
        queued = queued_in_order(load_jobs(layout))
        if not queued:
            return None
        if queued[0].request_id == failed:
            raise RuntimeError(f"{failed}: job is still queued after its due recording was deleted")
        if not _fail_if_due(layout, queued[0], at):
            return queued[0]
        failed = queued[0].request_id


def _fail_if_due(layout: Layout, job: Job, at: datetime) -> bool:
    """Purges `job`'s recording if its daily purge has come, failing its waiting users; True if it was due."""
    if not lifecycle.is_due(load_audio(layout, job.audio_id), at):
        return False
    for failed in lifecycle.purge_audio(layout, job.audio_id, at):
        _log(failed.request_id, "failed audio_deleted")
    return True


def _is_waiting(layout: Layout, request_id: str, at: datetime) -> bool:
    """True while the job is still ``queued`` and its recording is not due; call with ``state.lock`` held."""
    if not (layout.jobs / request_id / "job.json").exists():
        return False
    job = load_job(layout, request_id)
    return job.state == "queued" and not _fail_if_due(layout, job, at)


def _run_job(
    layout: Layout,
    request_id: str,
    engine_cmd: Sequence[str],
    now: Callable[[], datetime],
    poll_s: float,
    models_dir: Path,
    processing_limit: Callable[[float], float],
    reader: ReaderConfig,
) -> None:
    """Starts `request_id` if it is still waiting and runs it; the caller holds the GPU lock."""
    with hold(layout.state_lock):
        if not _is_waiting(layout, request_id, now()):
            return
        job = lifecycle.start(layout, load_job(layout, request_id), now())
        work_dir, recording = _prepare(layout, job)
    _log(job.request_id, "running")
    try:
        limit_s = processing_limit(job.duration_s)
        began = time.monotonic()
        try:
            outcome = _read(layout, job.request_id, recording, work_dir, reader, limit_s)
        except OSError:
            # A delete or the daily purge removes the recording and the work folder while the read is in flight.
            if _is_running(layout, job.request_id):
                raise
            outcome = "stopped"
        if outcome == "stopped":
            _log(job.request_id, "stopped")
            return
        if outcome != "ok":
            raise NotImplementedError(f"reader outcome {outcome} is not mapped to a failure yet")
        remaining_s = limit_s - (time.monotonic() - began)
        if remaining_s <= 0:
            _settle(layout, job, work_dir, _TIMEOUT, now)
            return
        ending = _run_engine(layout, job.request_id, engine_cmd, work_dir, models_dir, poll_s, remaining_s)
        if ending == "ended":
            _settle(layout, job, work_dir, _verdict(work_dir), now)
        elif ending == "timeout":
            _settle(layout, job, work_dir, _TIMEOUT, now)
        else:
            _log(job.request_id, "stopped")
    finally:
        _remove_work_folder(job.request_id, work_dir)


def _remove_work_folder(request_id: str, work_dir: Path) -> None:
    """Removes the job's work folder, logging a failure instead of raising over a propagating error."""
    try:
        shutil.rmtree(work_dir)
    except FileNotFoundError:
        # intentional-skip: finishing the job already removed it.
        return
    except OSError as error:
        _log(request_id, f"work folder removal failed {describe_error(error)}")


def run_one(
    layout: Layout,
    engine_cmd: Sequence[str],
    now: Callable[[], datetime],
    *,
    poll_s: float = 2.0,
    models_dir: Path,
    gpu_lock: Path,
    reader: ReaderConfig,
    processing_limit: Callable[[float], float] = deadlines.processing_limit,
) -> bool:
    """Runs the oldest queued job to its end, once the GPU lock is held.

    The job stays ``queued`` while another process holds the GPU lock. If it is deleted
    or its recording expires meanwhile, it is dropped without starting the engine.

    Args:
        layout: The state root.
        engine_cmd: Engine command; the work folder is appended as the last argument.
        now: Clock returning the current UTC time.
        poll_s: Seconds between checks whether the GPU lock is free, the engine has
            ended or the job was taken away.
        models_dir: Model cache handed to the engine as ``HF_HOME``.
        gpu_lock: Existing lock file shared with the command line.
        reader: The reader unit's socket and the waiting times.
        processing_limit: Maps a recording's length in seconds to the seconds a job may take, from the start
            of its read stage to the end of the engine.

    Returns:
        True if a job was picked, False if the queue was empty.

    Raises:
        MissingGpuLockError: If `gpu_lock` does not exist and a job is queued.
    """
    with hold(layout.state_lock):
        job = _pick(layout, now())
    if job is None:
        return False
    while True:
        held = gpu_lock_module.try_acquire(gpu_lock)
        if held is not None:
            with held:
                _run_job(layout, job.request_id, engine_cmd, now, poll_s, models_dir, processing_limit, reader)
            return True
        time.sleep(poll_s)
        with hold(layout.state_lock):
            if not _is_waiting(layout, job.request_id, now()):
                return True


def run(
    layout: Layout,
    engine_cmd: Sequence[str],
    now: Callable[[], datetime],
    *,
    poll_s: float = 2.0,
    models_dir: Path,
    gpu_lock: Path,
    reader: ReaderConfig,
    processing_limit: Callable[[float], float] = deadlines.processing_limit,
) -> int:
    """Runs queued jobs one at a time until the queue is empty.

    After each job the GPU lock is released and the worker rests `poll_s` before
    it tries the lock for the next job, so a command line waiting on it gets its turn.

    Args:
        layout: The state root.
        engine_cmd: Engine command; the work folder is appended as the last argument.
        now: Clock returning the current UTC time.
        poll_s: Seconds between checks whether the GPU lock is free, the engine has
            ended or the job was taken away.
        models_dir: Model cache handed to the engine as ``HF_HOME``.
        gpu_lock: Existing lock file shared with the command line.
        reader: The reader unit's socket and the waiting times.
        processing_limit: Maps a recording's length in seconds to the seconds a job may take, from the start
            of its read stage to the end of the engine.

    Returns:
        0 once the queue is empty.

    Raises:
        MissingGpuLockError: If `gpu_lock` does not exist and a job is queued.
    """
    with hold(layout.state_lock):
        for interrupted in lifecycle.clear_dead_worker(layout, now()):
            _log(interrupted.request_id, "failed interrupted")
    while run_one(
        layout,
        engine_cmd,
        now,
        poll_s=poll_s,
        models_dir=models_dir,
        gpu_lock=gpu_lock,
        reader=reader,
        processing_limit=processing_limit,
    ):
        time.sleep(poll_s)
    return 0
