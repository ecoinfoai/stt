"""GPU lock shared by the command line (``STT_GPU_LOCK``) and the server worker.

The lock file is opened read-only and locked with ``flock``. ``fcntl`` is imported inside
the functions so the module stays importable on platforms without it (Windows).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

ENV_VAR = "STT_GPU_LOCK"
WAIT_MESSAGE = "GPU is in use by another stt job; waiting for it to finish."


class MissingGpuLockError(Exception):
    """Raised when the configured GPU lock file does not exist."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"{ENV_VAR} points to a missing file: {path}")


def lock_path_from_env(environ: Mapping[str, str]) -> Path | None:
    """Returns the GPU lock file named by ``STT_GPU_LOCK``, or None if unset or empty."""
    value = environ.get(ENV_VAR, "")
    return Path(value) if value else None


def _open(path: Path) -> int:
    try:
        return os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        raise MissingGpuLockError(path) from None


@contextmanager
def _release_on_exit(fd: int) -> Iterator[None]:
    try:
        yield
    finally:
        os.close(fd)


@contextmanager
def acquire(path: Path, on_wait: Callable[[], None]) -> Iterator[None]:
    """Holds an exclusive lock on `path`, waiting while another process holds it.

    Args:
        path: Existing lock file.
        on_wait: Called exactly once, only if the lock is busy, before blocking.

    Raises:
        MissingGpuLockError: If `path` does not exist.
    """
    import fcntl

    fd = _open(path)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            on_wait()
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def try_acquire(path: Path) -> AbstractContextManager[None] | None:
    """Takes an exclusive lock on `path` without waiting.

    The lock is already held when this returns; the caller must enter the returned
    context manager so it is released on exit.

    Args:
        path: Existing lock file.

    Returns:
        A context manager releasing the lock, or None if another process holds it.

    Raises:
        MissingGpuLockError: If `path` does not exist.
    """
    import fcntl

    fd = _open(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return _release_on_exit(fd)
