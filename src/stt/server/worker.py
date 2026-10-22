"""Worker stage: runs queued jobs one at a time through the engine subprocess.

The caller holds ``worker.lock``. ``state.lock`` is taken here, briefly, to pick
and start a job and to publish and finish it; it is never held while the engine runs.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from stt.server import lifecycle
from stt.server.layout import Layout, hold
from stt.server.records import Failure, Job, load_audio, load_job, load_jobs, queued_in_order

DEFAULT_ENGINE_CMD: tuple[str, ...] = (sys.executable, "-m", "stt.server.engine")

_WORK_FOLDER_MODE = 0o700
_RESULT_FOLDER_MODE = 0o2750
_RESULT_FILE_MODE = 0o640
_RESULT_FILES = ("transcript.txt", "result.json")
_ENGINE_REASONS = frozenset(
    {"unreadable_audio", "gpu_out_of_memory", "gpu_unavailable", "models_unavailable", "engine_error"}
)
_MAX_DETAIL_CHARS = 500
_NO_VERDICT = Failure("engine_error", "The engine stopped without a verdict.")


def _log(request_id: str, state: str) -> None:
    print(f"{request_id} {state}", flush=True)


def _prepare(layout: Layout, job: Job) -> Path:
    """Creates ``work/<request_id>/`` with the job's ``spec.json`` and returns it."""
    audio = load_audio(layout, job.audio_id)
    recording = next((layout.audio / job.audio_id).glob("audio.*"))
    spec = {
        "request_id": job.request_id,
        "audio_path": str(recording),
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
    return work_dir


def _is_running(layout: Layout, request_id: str) -> bool:
    """True while the job's record exists and says ``running``; a purged record counts as not running."""
    with hold(layout.state_lock):
        if not (layout.jobs / request_id / "job.json").exists():
            return False
        return load_job(layout, request_id).state == "running"


def _run_engine(
    layout: Layout, request_id: str, engine_cmd: Sequence[str], work_dir: Path, models_dir: Path, poll_s: float
) -> bool:
    """Runs the engine until it ends; kills it as soon as the job is no longer ``running``.

    Returns:
        True if the engine ended by itself, False if it was killed.
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
    try:
        while process.poll() is None:
            if not _is_running(layout, request_id):
                break
            time.sleep(poll_s)
        else:
            return True
    except BaseException:
        _kill(process)
        raise
    _kill(process)
    return False


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
        staging.mkdir(mode=0o700)
        for name in _RESULT_FILES:
            target = staging / name
            shutil.copyfile(work_dir / "out" / name, target)
            os.chmod(target, _RESULT_FILE_MODE)
        os.chmod(staging, _RESULT_FOLDER_MODE)
        staging.rename(layout.outbox / request_id)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _settle(layout: Layout, job: Job, work_dir: Path, failure: Failure | None, now: Callable[[], datetime]) -> None:
    """Publishes and finishes `job`, unless it is no longer ``running``."""
    with hold(layout.state_lock):
        current = load_job(layout, job.request_id)
        if current.state != "running":
            return
        if failure is None:
            _publish(layout, work_dir, job.request_id)
            lifecycle.finish(layout, current, "done", now())
            _log(job.request_id, "done")
        else:
            lifecycle.finish(layout, current, "failed", now(), failure)
            _log(job.request_id, f"failed {failure.reason}")


def _pick(layout: Layout, at: datetime) -> Job | None:
    """Starts the oldest queued job whose recording is not yet due for deletion.

    A queued job whose recording is already due is failed with ``audio_deleted``
    instead of being started, without waiting for the sweep.
    """
    while True:
        queued = queued_in_order(load_jobs(layout))
        if not queued:
            return None
        cause = lifecycle.due_cause(load_audio(layout, queued[0].audio_id), at)
        if cause is None:
            return lifecycle.start(layout, queued[0], at)
        for failed in lifecycle.delete_audio(layout, queued[0].audio_id, cause, at):
            _log(failed.request_id, "failed audio_deleted")


def run_one(
    layout: Layout,
    engine_cmd: Sequence[str],
    now: Callable[[], datetime],
    *,
    poll_s: float = 2.0,
    models_dir: Path,
) -> bool:
    """Runs the oldest queued job to its end.

    Args:
        layout: The state root.
        engine_cmd: Engine command; the work folder is appended as the last argument.
        now: Clock returning the current UTC time.
        poll_s: Seconds between checks whether the engine has ended or the job was taken away.
        models_dir: Model cache handed to the engine as ``HF_HOME``.

    Returns:
        True if a job was run, False if the queue was empty.
    """
    with hold(layout.state_lock):
        job = _pick(layout, now())
        if job is None:
            return False
        work_dir = _prepare(layout, job)
    _log(job.request_id, "running")
    try:
        if _run_engine(layout, job.request_id, engine_cmd, work_dir, models_dir, poll_s):
            _settle(layout, job, work_dir, _verdict(work_dir), now)
        else:
            _log(job.request_id, "stopped")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return True


def run(
    layout: Layout,
    engine_cmd: Sequence[str],
    now: Callable[[], datetime],
    *,
    poll_s: float = 2.0,
    models_dir: Path,
) -> int:
    """Runs queued jobs one at a time until the queue is empty.

    Args:
        layout: The state root.
        engine_cmd: Engine command; the work folder is appended as the last argument.
        now: Clock returning the current UTC time.
        poll_s: Seconds between checks whether the engine has ended or the job was taken away.
        models_dir: Model cache handed to the engine as ``HF_HOME``.

    Returns:
        0 once the queue is empty.
    """
    with hold(layout.state_lock):
        for interrupted in lifecycle.clear_dead_worker(layout, now()):
            _log(interrupted.request_id, "failed interrupted")
    while run_one(layout, engine_cmd, now, poll_s=poll_s, models_dir=models_dir):
        pass
    return 0
