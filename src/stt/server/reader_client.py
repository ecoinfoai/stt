"""Reader protocol, caller side: ``probe()`` and ``decode()`` for intake and the worker.

Each call opens the recording itself, makes one connection to the reader socket, sends the one request message
with the recording descriptor (and, for decode, the write end of a pipe), and waits for the reader's answers. The
reader may be compromised, so every answer is checked strictly and any deviation ends the call with a failure.

The five outcomes (contracts/reader.md section 7):

* ``unavailable``: any failure before the reader's ``{"started": true}``.
* ``failed``: any failure after it, including decoded output of an odd size or above ``MAX_DECODED_BYTES``.
* ``ok``.
* ``stopped``: the job is no longer running (decode only).
* ``timeout``: the job's time limit ran out first (decode only).

There is no fallback that decodes in this process when the reader is unavailable.
"""

from __future__ import annotations

import json
import math
import os
import select
import socket
import stat
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple

from stt.server.reader import (
    DECODE_LIMIT_S,
    MAX_DECODED_BYTES,
    MESSAGE_LIMIT,
    PROBE_LIMIT_S,
    SOCKET_PATH,
    START_TIMEOUT_S,
    VERDICT_GRACE_S,
)

Outcome = Literal["ok", "failed", "unavailable", "stopped", "timeout"]

_PIPE_READ_BYTES = 65536


def _require_finite_positive(name: str, value: float) -> None:
    if not 0 < value < math.inf:
        raise ValueError(f"{name} must be finite and above 0")


@dataclass(frozen=True)
class ReaderConfig:
    """Where the reader is and how long to wait for it.

    Attributes:
        socket_path: Path of the reader's ``AF_UNIX`` ``SOCK_SEQPACKET`` socket.
        start_timeout_s: Longest wait from the first connect until the reader says ``started``.
        verdict_grace_s: Time beyond the request's ``limit_s`` that the reader gets to send its verdict.
        poll_s: How often decode asks whether the job is still running.
        probe_limit_s: The ``limit_s`` sent with a probe request.
        decode_limit_s: The ``limit_s`` sent with a decode request.
    """

    socket_path: str = SOCKET_PATH
    start_timeout_s: float = START_TIMEOUT_S
    verdict_grace_s: float = VERDICT_GRACE_S
    poll_s: float = 2.0
    probe_limit_s: float = PROBE_LIMIT_S
    decode_limit_s: float = DECODE_LIMIT_S

    def __post_init__(self) -> None:
        for name in ("start_timeout_s", "verdict_grace_s", "poll_s"):
            _require_finite_positive(name, getattr(self, name))
        for name, bound in (("probe_limit_s", PROBE_LIMIT_S), ("decode_limit_s", DECODE_LIMIT_S)):
            if not 0 < getattr(self, name) <= bound:
                raise ValueError(f"{name} must be above 0 and at most {bound}")


@dataclass(frozen=True)
class ProbeResult:
    """The result of ``probe()``.

    Attributes:
        outcome: ``ok``, ``failed`` or ``unavailable``.
        duration_s: The recording's length in seconds; set only when `outcome` is ``ok``.
    """

    outcome: Outcome
    duration_s: float | None = None


@dataclass(frozen=True)
class DecodeResult:
    """The result of ``decode()``.

    Attributes:
        outcome: Any of the five outcomes.
        audio: Decoded 16 kHz mono s16le samples, an even number of bytes; empty unless `outcome` is ``ok``.
    """

    outcome: Outcome
    audio: bytes | bytearray = b""


class _Reply(NamedTuple):
    outcome: Outcome
    duration_s: float | None = None
    audio: bytes | bytearray = b""


class RecordingError(OSError):
    """The recording could not be opened as a regular file; nothing was sent to the reader.

    The errno is that of the failed open (a symbolic link is ``ELOOP``); it is None when the file opened but is
    not a regular file.
    """


def _open_recording(path: str | os.PathLike[str]) -> int:
    """Opens the recording without following a symbolic link and without blocking on a FIFO.

    Raises:
        RecordingError: If the file cannot be opened (with the errno of the failure) or is not a regular file.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as error:
        raise RecordingError(error.errno, error.strerror) from error
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RecordingError("the recording is not a regular file")
    return fd


def _parse_message(data: bytes, flags: int) -> dict[str, Any] | None:
    """Returns the JSON object of a received message; None when the message breaks the protocol or is an EOF."""
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        return None
    try:
        body = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    return body if isinstance(body, dict) else None


def _receive(connection: socket.socket) -> dict[str, Any] | None:
    """Receives one message without accepting descriptors; None when the reader hung up or broke the protocol."""
    try:
        try:
            data, _, flags, _ = connection.recvmsg(MESSAGE_LIMIT)
        except ConnectionResetError:
            # The kernel reports a reset once, ahead of the messages queued before it.
            data, _, flags, _ = connection.recvmsg(MESSAGE_LIMIT)
    except OSError:
        return None
    return _parse_message(data, flags)


def _started(body: dict[str, Any] | None) -> bool | None:
    if body is None or body.keys() != {"started"} or not isinstance(body["started"], bool):
        return None
    return body["started"]


def _duration(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        duration = float(value)
    except OverflowError:
        return None
    return duration if 0 <= duration < math.inf else None


def _verdict(op: str, body: dict[str, Any] | None) -> _Reply | None:
    """Maps a verdict message to a reply; None when the message breaks the protocol."""
    if body is None:
        return None
    if body == {"status": "failed"}:
        return _Reply("failed")
    if op == "decode":
        return _Reply("ok") if body == {"status": "ok"} else None
    if body.keys() != {"status", "duration_s"} or body["status"] != "ok":
        return None
    duration = _duration(body["duration_s"])
    return None if duration is None else _Reply("ok", duration)


def _connect_and_send(config: ReaderConfig, request: bytes, fds: list[int]) -> socket.socket | None:
    """Connects and sends the request with `fds`; None when that fails. The returned socket is non-blocking."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        connection.settimeout(config.start_timeout_s)
        connection.connect(config.socket_path)
        socket.send_fds(connection, [request], fds)
        connection.setblocking(False)
    except OSError:
        connection.close()
        return None
    return connection


def _wait(
    config: ReaderConfig,
    op: str,
    limit_s: float,
    connection: socket.socket,
    pipe_read: int | None,
    began: float,
    job_running: Callable[[], bool] | None,
    job_limit_s: float,
) -> _Reply:
    """Waits for the reader's answers and the decoded output until one of the five outcomes is reached."""
    poller = select.poll()
    poller.register(connection, select.POLLIN)
    if pipe_read is not None:
        poller.register(pipe_read, select.POLLIN)
    job_deadline = began + job_limit_s
    started_at: float | None = None
    next_check = began + config.poll_s
    answered = False
    reply = _Reply("ok")
    audio = bytearray()
    pipe_open = pipe_read is not None
    while not answered or pipe_open:
        now = time.monotonic()
        if now >= job_deadline:
            return _Reply("timeout")
        if started_at is None:
            phase_end = began + config.start_timeout_s
        else:
            phase_end = started_at + limit_s + config.verdict_grace_s
        if now >= phase_end:
            return _Reply("unavailable" if started_at is None else "failed")
        if job_running is not None and now >= next_check:
            if not job_running():
                return _Reply("stopped")
            next_check = now + config.poll_s
        wake = min(job_deadline, phase_end, next_check if job_running is not None else math.inf)
        for fd, _ in poller.poll(math.ceil((wake - now) * 1000)):
            if fd == connection.fileno():
                body = _receive(connection)
                if started_at is None:
                    if not _started(body):
                        return _Reply("unavailable")
                    started_at = time.monotonic()
                    continue
                verdict = _verdict(op, body)
                if verdict is None or verdict.outcome != "ok":
                    return _Reply("failed")
                reply, answered = verdict, True
                poller.unregister(connection)
            else:
                chunk = os.read(fd, _PIPE_READ_BYTES)
                if not chunk:
                    pipe_open = False
                    poller.unregister(fd)
                    continue
                audio += chunk
                if len(audio) > MAX_DECODED_BYTES:
                    return _Reply("failed")
    if len(audio) % 2:
        return _Reply("failed")
    return reply._replace(audio=audio)


def _exchange(
    config: ReaderConfig,
    op: str,
    recording: str | os.PathLike[str],
    limit_s: float,
    job_running: Callable[[], bool] | None,
    job_limit_s: float,
) -> _Reply:
    began = time.monotonic()
    with ExitStack() as held:
        with ExitStack() as sent:
            recording_fd = _open_recording(recording)
            sent.callback(os.close, recording_fd)
            fds = [recording_fd]
            pipe_read: int | None = None
            if op == "decode":
                pipe_read, pipe_write = os.pipe2(os.O_CLOEXEC)
                held.callback(os.close, pipe_read)
                sent.callback(os.close, pipe_write)
                # Only the read end: the write end goes to the reader's child, which must write in blocking mode.
                os.set_blocking(pipe_read, False)
                fds.append(pipe_write)
            request = json.dumps({"op": op, "limit_s": limit_s}).encode("utf-8")
            connection = _connect_and_send(config, request, fds)
        if connection is None:
            return _Reply("unavailable")
        held.enter_context(connection)
        return _wait(config, op, limit_s, connection, pipe_read, began, job_running, job_limit_s)


def probe(config: ReaderConfig, recording: str | os.PathLike[str]) -> ProbeResult:
    """Measures the length of a recording through the reader.

    Args:
        config: The reader's socket and the waiting times.
        recording: Path of the recording; opened here without following a symbolic link.

    Returns:
        ``ok`` with the duration, ``failed`` or ``unavailable``.

    Raises:
        RecordingError: If the recording cannot be opened as a regular file (a symbolic link included); nothing
            was sent.
        OSError: If a pipe or the socket cannot be made, or an I/O error occurs after the reader started.
    """
    reply = _exchange(config, "probe", recording, config.probe_limit_s, None, math.inf)
    return ProbeResult(reply.outcome, reply.duration_s)


def decode(
    config: ReaderConfig,
    recording: str | os.PathLike[str],
    *,
    job_running: Callable[[], bool],
    job_limit_s: float,
) -> DecodeResult:
    """Decodes a recording to 16 kHz mono s16le samples through the reader.

    Args:
        config: The reader's socket and the waiting times.
        recording: Path of the recording; opened here without following a symbolic link.
        job_running: Called every ``config.poll_s``; when it returns False the connection and the pipe are closed
            and the result is ``stopped``.
        job_limit_s: Seconds from this call that the job may still take; when they pass first the result is
            ``timeout``.

    Returns:
        The outcome with the decoded samples when it is ``ok``.

    Raises:
        RecordingError: If the recording cannot be opened as a regular file (a symbolic link included); nothing
            was sent.
        ValueError: If `job_limit_s` is not finite and above 0; nothing was sent.
        OSError: If a pipe or the socket cannot be made, or an I/O error occurs after the reader started.
    """
    _require_finite_positive("job_limit_s", job_limit_s)
    reply = _exchange(config, "decode", recording, config.decode_limit_s, job_running, job_limit_s)
    return DecodeResult(reply.outcome, reply.audio)
