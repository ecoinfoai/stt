"""Fixtures shared by every test."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def hide_host_gpu_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Points the default GPU lock at a missing file.

    The server host has ``/run/stt/gpu.lock``; a test that leaves
    ``STT_GPU_LOCK`` unset must not queue behind real server jobs there.
    """
    from stt import gpu_lock

    monkeypatch.setattr(gpu_lock, "DEFAULT_PATH", tmp_path / "no-host-gpu.lock", raising=False)
