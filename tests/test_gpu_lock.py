"""GPU lock tests: env lookup, read-only flock, single wait notice, release, no fcntl import."""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterator

import pytest

from stt.gpu_lock import (
    WAIT_MESSAGE,
    MissingGpuLockError,
    acquire,
    lock_path_from_env,
    try_acquire,
)

HOLDER_SCRIPT = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDONLY)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('ready', flush=True)\n"
    "sys.stdin.read()\n"
)
PROBE_SCRIPT = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDONLY)\n"
    "try:\n"
    "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "except BlockingIOError:\n"
    "    print('busy')\n"
    "else:\n"
    "    print('free')\n"
)


@pytest.fixture
def lock_file(tmp_path: Path) -> Path:
    path = tmp_path / "gpu.lock"
    path.touch()
    return path


@pytest.fixture
def holder(lock_file: Path) -> Iterator[subprocess.Popen[str]]:
    """Another process that holds the lock until its stdin is closed."""
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER_SCRIPT, str(lock_file)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    yield process
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            stream.close()


def release_holder(process: subprocess.Popen[str]) -> None:
    assert process.stdin is not None
    process.stdin.close()
    process.wait(timeout=10)


def probe(path: Path) -> str:
    """Reports from a fresh process whether the lock is `busy` or `free`."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE_SCRIPT, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_wait_message_is_the_contract_text() -> None:
    assert WAIT_MESSAGE == "GPU is in use by another stt job; waiting for it to finish."


def test_lock_path_from_env_returns_the_path() -> None:
    assert lock_path_from_env({"STT_GPU_LOCK": "/run/stt/gpu.lock"}) == Path("/run/stt/gpu.lock")


@pytest.mark.parametrize("environ", [{}, {"STT_GPU_LOCK": ""}])
def test_lock_path_from_env_is_none_when_missing_or_empty(environ: dict[str, str]) -> None:
    assert lock_path_from_env(environ) is None


def test_acquire_on_free_lock_does_not_call_on_wait_and_releases_on_exit(lock_file: Path) -> None:
    calls: list[int] = []
    with acquire(lock_file, lambda: calls.append(1)):
        assert probe(lock_file) == "busy"
    assert calls == []
    assert probe(lock_file) == "free"


def test_acquire_releases_when_the_body_raises(lock_file: Path) -> None:
    with pytest.raises(RuntimeError):
        with acquire(lock_file, lambda: None):
            raise RuntimeError("boom")
    assert probe(lock_file) == "free"


def test_acquire_works_on_a_read_only_file(lock_file: Path) -> None:
    lock_file.chmod(0o444)
    with acquire(lock_file, lambda: None):
        assert probe(lock_file) == "busy"


def test_acquire_calls_on_wait_once_then_blocks_until_holder_releases(
    lock_file: Path, holder: subprocess.Popen[str]
) -> None:
    waiting = threading.Event()
    acquired = threading.Event()
    calls: list[int] = []

    def on_wait() -> None:
        calls.append(1)
        waiting.set()

    def worker() -> None:
        with acquire(lock_file, on_wait):
            acquired.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert waiting.wait(timeout=10)
    assert not acquired.wait(timeout=0.3)
    release_holder(holder)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert acquired.is_set()
    assert calls == [1]
    assert probe(lock_file) == "free"


def test_try_acquire_returns_none_while_another_process_holds_the_lock(
    lock_file: Path, holder: subprocess.Popen[str]
) -> None:
    assert try_acquire(lock_file) is None
    release_holder(holder)
    held = try_acquire(lock_file)
    assert held is not None
    with held:
        assert probe(lock_file) == "busy"
    assert probe(lock_file) == "free"


def test_try_acquire_holds_the_lock_before_the_context_is_entered(lock_file: Path) -> None:
    held = try_acquire(lock_file)
    assert held is not None
    assert probe(lock_file) == "busy"
    with held:
        pass
    assert probe(lock_file) == "free"


def test_opened_descriptor_is_not_inheritable(
    lock_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []
    real_flock = fcntl.flock

    def spy(fd: int, operation: int) -> None:
        seen.append(os.get_inheritable(fd))
        real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", spy)
    with acquire(lock_file, lambda: None):
        pass
    assert seen == [False]


def test_acquire_on_missing_file_raises_with_contract_message(tmp_path: Path) -> None:
    missing = tmp_path / "absent.lock"
    with pytest.raises(MissingGpuLockError) as excinfo:
        with acquire(missing, lambda: None):
            pass
    assert str(excinfo.value) == f"STT_GPU_LOCK points to a missing file: {missing}"
    assert not missing.exists()


def test_try_acquire_on_missing_file_raises_and_does_not_create_it(tmp_path: Path) -> None:
    missing = tmp_path / "absent.lock"
    with pytest.raises(MissingGpuLockError):
        try_acquire(missing)
    assert not missing.exists()


def test_module_imports_without_fcntl() -> None:
    script = (
        "import sys\n"
        "sys.modules['fcntl'] = None\n"
        "from pathlib import Path\n"
        "import stt.gpu_lock as module\n"
        "assert module.lock_path_from_env({'STT_GPU_LOCK': 'x'}) == Path('x')\n"
        "assert module.lock_path_from_env({}) is None\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True)
