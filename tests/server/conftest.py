"""Shared helpers and fixtures for the server transcription tests.

Production modules (``stt.server.*``) are imported inside helpers and
fixtures so that test collection works before those modules exist.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import sys
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Returns an empty state root directory."""
    state_root = tmp_path / "stt"
    state_root.mkdir()
    return state_root


class FakeClock:
    """An injectable UTC clock that only moves when told to."""

    def __init__(self, start: str = "2026-03-02T01:00:00Z") -> None:
        self._now = datetime.strptime(start, _TIME_FORMAT).replace(tzinfo=timezone.utc)

    def now(self) -> datetime:
        """Returns the current fake time (UTC aware)."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Moves the clock forward."""
        self._now += timedelta(seconds=seconds)

    def set(self, iso: str) -> None:
        """Sets the clock to a ``YYYY-MM-DDTHH:MM:SSZ`` time."""
        self._now = datetime.strptime(iso, _TIME_FORMAT).replace(tzinfo=timezone.utc)


def set_to_ctime_plus(clock: FakeClock, path: Path, minutes: float) -> None:
    """Moves `clock` to `minutes` after the real inode change time of `path`.

    A file's ``st_ctime`` cannot be chosen by a test, so the clock follows it.

    Args:
        clock: The injected clock.
        path: A file or folder that exists.
        minutes: Offset from the file's ``st_ctime``.
    """
    changed = datetime.fromtimestamp(os.lstat(path).st_ctime, timezone.utc)
    clock.set((changed + timedelta(minutes=minutes)).strftime(_TIME_FORMAT))


def make_wav(path: Path, seconds: float, silent: bool = True) -> Path:
    """Writes a 16 kHz mono 16-bit WAV file.

    Args:
        path: Destination file.
        seconds: Duration in seconds.
        silent: True for silence, False for a 440 Hz tone.

    Returns:
        The written path.
    """
    rate = 16000
    frames = int(rate * seconds)
    if silent:
        data = bytes(2 * frames)
    else:
        data = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(frames)
        )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data)
    return path


def new_id() -> str:
    """Returns a new lowercase UUID string."""
    return str(uuid.uuid4())


def _write_file(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(path, mode)


def _publish_request(inbox: Path, request_id: str, body: dict[str, Any], mode: int) -> Path:
    tmp = inbox / f"{request_id}.json.tmp"
    final = inbox / f"{request_id}.json"
    _write_file(tmp, json.dumps(body).encode("utf-8"), mode)
    os.replace(tmp, final)
    return final


def write_transcribe_request(
    inbox: Path,
    *,
    request_id: str,
    requested_by: str,
    attachment: Path | None,
    from_request: str | None = None,
    language: str = "ko",
    diarize: bool = False,
    hints: list[str] | None = None,
    delete_audio: str = "after_job",
    mode: int = 0o640,
) -> Path:
    """Hands over a transcribe request the way the contract prescribes.

    The attachment is copied to ``<request_id>.audio.<ext>`` first, then the
    request is written as ``.json.tmp`` and renamed.

    Args:
        inbox: The inbox directory.
        request_id: Lowercase UUID of the request.
        requested_by: Opaque requester marker.
        attachment: Local recording to hand over, or None.
        from_request: Earlier request whose recording is reused, or None.
        language: Language code.
        diarize: Whether to separate speakers.
        hints: Optional glossary terms, written in the given order.
        delete_audio: ``after_job`` or ``at_midnight``.
        mode: File mode of the request and the attachment.

    Returns:
        The final request path ``<inbox>/<request_id>.json``.
    """
    audio: dict[str, str] = {}
    if attachment is not None:
        name = f"{request_id}.audio{attachment.suffix}"
        target = inbox / name
        shutil.copyfile(attachment, target)
        os.chmod(target, mode)
        audio["file"] = name
    if from_request is not None:
        audio["from_request"] = from_request
    body: dict[str, Any] = {
        "schema_version": 1,
        "kind": "transcribe",
        "request_id": request_id,
        "requested_by": requested_by,
        "audio": audio,
        "language": language,
        "diarize": diarize,
        "delete_audio": delete_audio,
    }
    if hints is not None:
        body["hints"] = hints
    return _publish_request(inbox, request_id, body, mode)


def write_delete_request(
    inbox: Path,
    *,
    request_id: str,
    requested_by: str,
    target: str,
    scope: str = "result",
    mode: int = 0o640,
) -> Path:
    """Hands over a delete request (``.json.tmp``, then rename).

    Args:
        inbox: The inbox directory.
        request_id: Lowercase UUID of this delete request.
        requested_by: Opaque requester marker.
        target: ``request_id`` of the transcribe request to delete from.
        scope: ``result`` or ``everything``.
        mode: File mode of the request.

    Returns:
        The final request path ``<inbox>/<request_id>.json``.
    """
    body = {
        "schema_version": 1,
        "kind": "delete",
        "request_id": request_id,
        "requested_by": requested_by,
        "target": target,
        "scope": scope,
    }
    return _publish_request(inbox, request_id, body, mode)


def leftovers(root: Path) -> list[Path]:
    """Lists everything under a state root that should not outlive its job.

    Covers recording folders ``audio/<id>/``, anything under ``work/`` and
    ``queue/``, temporary ``.*.tmp`` files and folders anywhere (including
    ``outbox/.*.tmp/``), ``*.wav`` files, and job records that still hold
    ``hints``.

    Args:
        root: The state root directory.

    Returns:
        Sorted paths of the leftovers; empty when nothing remains.
    """
    found: set[Path] = set()
    audio = root / "audio"
    if audio.is_dir():
        found.update(p for p in audio.iterdir() if p.is_dir())
    for name in ("work", "queue"):
        folder = root / name
        if folder.is_dir():
            found.update(folder.rglob("*"))
    found.update(root.rglob(".*.tmp"))
    found.update(root.rglob("*.wav"))
    jobs = root / "jobs"
    if jobs.is_dir():
        for record in jobs.rglob("*.json"):
            data = json.loads(record.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("hints") is not None:
                found.add(record)
    return sorted(found)


def _engine_cmd(mode: str) -> list[str]:
    return [sys.executable, str(FAKE_ENGINE), "--mode", mode]


def _worker_kwargs(layout: Any, clock: FakeClock, mode: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    state_root = layout.root
    models_dir = state_root.parent / f"{state_root.name}-models"
    models_dir.mkdir(exist_ok=True)
    gpu_lock = state_root.parent / f"{state_root.name}-gpu.lock"
    gpu_lock.touch()
    options: dict[str, Any] = {
        "engine_cmd": _engine_cmd(mode),
        "now": clock.now,
        "poll_s": 0.05,
        "models_dir": models_dir,
        "gpu_lock": gpu_lock,
    }
    options.update(kwargs)
    return options


def run_worker(layout: Any, clock: FakeClock, mode: str = "done", **kwargs: Any) -> int:
    """Runs ``worker.run`` with the fake engine, holding ``worker.lock`` like the CLI.

    Args:
        layout: A ``stt.server.layout.Layout``.
        clock: The injected clock.
        mode: Fake engine mode (``done``, ``fail=<reason>``, ``sleep=<s>``, ``die``).
        **kwargs: Overrides and extra arguments for ``worker.run``.

    Returns:
        What ``worker.run`` returns.
    """
    from stt.server import worker
    from stt.server.layout import hold

    options = _worker_kwargs(layout, clock, mode, kwargs)
    with hold(layout.worker_lock):
        return worker.run(layout, options.pop("engine_cmd"), options.pop("now"), **options)


def run_worker_once(layout: Any, clock: FakeClock, mode: str = "done", **kwargs: Any) -> bool:
    """Runs ``worker.run_one`` with the fake engine, holding ``worker.lock`` like the CLI.

    Args:
        layout: A ``stt.server.layout.Layout``.
        clock: The injected clock.
        mode: Fake engine mode (``done``, ``fail=<reason>``, ``sleep=<s>``, ``die``).
        **kwargs: Overrides and extra arguments for ``worker.run_one``.

    Returns:
        What ``worker.run_one`` returns.
    """
    from stt.server import worker
    from stt.server.layout import hold

    options = _worker_kwargs(layout, clock, mode, kwargs)
    with hold(layout.worker_lock):
        return worker.run_one(layout, options.pop("engine_cmd"), options.pop("now"), **options)


def run_sweep(layout: Any, clock: FakeClock) -> None:
    """Runs ``sweep.run_once``, holding ``sweep.lock`` like the CLI.

    Args:
        layout: A ``stt.server.layout.Layout``.
        clock: The injected clock.
    """
    from stt.server import sweep
    from stt.server.layout import hold

    with hold(layout.sweep_lock):
        sweep.run_once(layout, clock.now)


@pytest.fixture
def gpu_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes the engine's start-up check see one CUDA device."""
    monkeypatch.setattr("stt.server.engine.cuda_device_count", lambda *a, **k: 1)
    monkeypatch.setattr("stt.server.engine.torch_cuda_available", lambda *a, **k: True)


@pytest.fixture
def pinned_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Writes the pinned model refs under a temporary ``HF_HOME`` and points to it.

    Returns:
        The temporary ``HF_HOME`` directory.
    """
    from stt.server.models import PINNED

    hf_home = tmp_path / "hf_home"
    for repo, commit in PINNED:
        org, name = repo.split("/", 1)
        refs = hf_home / "hub" / f"models--{org}--{name}" / "refs"
        refs.mkdir(parents=True)
        (refs / "main").write_text(commit, encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(hf_home))
    return hf_home
