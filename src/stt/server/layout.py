"""State-root layout, request and attachment names, atomic writes, flock helpers, safe inbox reads."""

from __future__ import annotations

import errno
import fcntl
import os
import re
import secrets
import stat
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Literal

REQUEST_LIMIT_BYTES = 1024 * 1024

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_REQUEST_NAME = re.compile(rf"({_UUID})\.json")
_ATTACHMENT_NAME = re.compile(rf"({_UUID})\.audio\.(?:m4a|mp3|wav|flac|ogg|opus|aac|wma)")

UnsafeReason = Literal["symlink", "hardlink", "not_regular", "too_large", "unreadable"]

_ROOT_MODE = 0o751
_REQUESTS_MODE = 0o751
_INBOX_MODE = 0o770
_OUTBOX_MODE = 0o750
_INTERNAL_MODE = 0o700
_LOCK_MODE = 0o600


class UnsafeFileError(Exception):
    """An inbox file is not a plain, singly-linked, readable regular file.

    The message is the bare reason code so that it never carries a path.

    Attributes:
        reason: One of ``symlink``, ``hardlink``, ``not_regular``, ``too_large``
            or ``unreadable``.
    """

    def __init__(self, reason: UnsafeReason) -> None:
        super().__init__(reason)
        self.reason: UnsafeReason = reason


class Layout:
    """Paths of the state root.

    Args:
        root: The state root directory (``/var/lib/stt`` in production).
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.requests = root / "requests"
        self.inbox = self.requests / "inbox"
        self.outbox = self.requests / "outbox"
        self.jobs = root / "jobs"
        self.audio = root / "audio"
        self.work = root / "work"
        self.queue = root / "queue"
        self.state_lock = root / "state.lock"
        self.worker_lock = root / "worker.lock"
        self.intake_lock = root / "intake.lock"
        self.sweep_lock = root / "sweep.lock"

    def ensure_layout(self) -> None:
        """Creates missing folders; the mode of an existing folder is never changed.

        Internal folders are created 0700. The state root, ``requests``, ``inbox``
        and ``outbox`` are normally made by the host (tmpfiles); when missing they
        are created 0751, 0751, 0770 and 0750 (group-writable inbox, no setgid or ACL).
        """
        for folder, mode in (
            (self.root, _ROOT_MODE),
            (self.requests, _REQUESTS_MODE),
            (self.inbox, _INBOX_MODE),
            (self.outbox, _OUTBOX_MODE),
            (self.jobs, _INTERNAL_MODE),
            (self.audio, _INTERNAL_MODE),
            (self.work, _INTERNAL_MODE),
            (self.queue, _INTERNAL_MODE),
        ):
            _make_folder(folder, mode)


def _make_folder(folder: Path, mode: int) -> None:
    try:
        folder.mkdir()
    except FileExistsError:
        return
    os.chmod(folder, mode)


def is_request_name(name: str) -> bool:
    """Returns whether `name` is ``<lowercase UUID>.json``."""
    return _REQUEST_NAME.fullmatch(name) is not None


def attachment_id(name: str) -> str | None:
    """Returns the request id of ``<uuid>.audio.<ext>``, or None for any other name."""
    match = _ATTACHMENT_NAME.fullmatch(name)
    return match.group(1) if match else None


def atomic_write_text(path: Path, text: str, mode: int = 0o640) -> None:
    """Writes `text` to `path` so readers see the old or the new file, never a part.

    The data goes to ``.<name>.<random>.tmp`` in the same folder, is fsynced, given
    exactly `mode` and renamed over `path`. The temporary file is removed on failure.

    Args:
        path: Final file path.
        text: UTF-8 text to write.
        mode: Exact final permission bits (umask does not apply).
    """
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _open_lock(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, _LOCK_MODE)


@contextmanager
def hold(path: Path) -> Iterator[None]:
    """Holds an exclusive flock on `path` (created if missing), waiting until it is free."""
    fd = _open_lock(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextmanager
def _release_on_exit(fd: int) -> Iterator[None]:
    try:
        yield
    finally:
        os.close(fd)


def try_hold(path: Path) -> AbstractContextManager[None] | None:
    """Takes an exclusive flock on `path` without waiting.

    The lock is already held when this returns; the returned context manager must be
    entered (``with``) so that it is released on exit.

    Args:
        path: Lock file (created if missing).

    Returns:
        A context manager releasing the lock, or None if someone else holds it.
    """
    fd = _open_lock(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return _release_on_exit(fd)


def _open_regular(path: Path) -> int:
    """Opens `path` read-only and returns the descriptor of a regular, singly-linked file.

    O_NONBLOCK keeps an open on a FIFO without writer from blocking.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        reason: UnsafeReason = "symlink" if error.errno == errno.ELOOP else "unreadable"
        raise UnsafeFileError(reason) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeFileError("not_regular")
        if info.st_nlink != 1:
            raise UnsafeFileError("hardlink")
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_request_bytes(path: Path, limit: int = REQUEST_LIMIT_BYTES) -> bytes:
    """Reads a request file after checking it is safe.

    Args:
        path: Request file in the inbox.
        limit: Largest accepted size in bytes.

    Returns:
        The file content.

    Raises:
        UnsafeFileError: For a symlink, hard link, non-regular file, unreadable file
            or a file larger than `limit`; the content is not read in these cases
            (a file that grew past `limit` after the size check is read up to `limit` + 1 bytes).
    """
    fd = _open_regular(path)
    with os.fdopen(fd, "rb") as handle:
        if os.fstat(handle.fileno()).st_size > limit:
            raise UnsafeFileError("too_large")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise UnsafeFileError("too_large")
    return data


def open_attachment(path: Path) -> BinaryIO:
    """Opens an attachment read-only after checking it is safe.

    Args:
        path: Attachment file in the inbox.

    Returns:
        A binary file object; the caller closes it.

    Raises:
        UnsafeFileError: For a symlink, hard link, non-regular or unreadable file.
    """
    return os.fdopen(_open_regular(path), "rb")
