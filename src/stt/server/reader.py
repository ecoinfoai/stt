"""Reader protocol: the supervisor of one reader instance and its decode child.

The supervisor (``serve``, run as ``stt server read``) imports only the standard
library and never reads a recording. It checks the caller's request, starts the
decode child and answers with its verdict. The decode child
(``python -m stt.server.reader child probe|decode <recording fd> <output fd>``)
is the only code that imports PyAV; it reads the recording through the
descriptor it was handed and never sees a path.

Neither side writes logs or anything else to standard output or error.
"""

from __future__ import annotations

import array
import json
import math
import os
import select
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any, BinaryIO

SOCKET_PATH = "/run/stt-reader.sock"
MESSAGE_LIMIT = 4096
RESULT_LIMIT = 256
PROBE_LIMIT_S = 30
DECODE_LIMIT_S = 240
START_TIMEOUT_S = 30
VERDICT_GRACE_S = 5
SAMPLE_RATE = 16000
MAX_DECODED_BYTES = 362_880_000
# FFmpeg demuxers of the attachment extensions the request schema allows
# (m4a, mp3, wav, flac, ogg, opus, aac, wma). Opening any other format could
# follow references to other files, such as a playlist's.
AUDIO_FORMATS = "mov,mp3,wav,flac,ogg,aac,asf"

DEFAULT_CHILD_COMMAND: tuple[str, ...] = (sys.executable, "-m", "stt.server.reader", "child")

_LIMITS_S = {"probe": PROBE_LIMIT_S, "decode": DECODE_LIMIT_S}
_REQUEST_KEYS = frozenset({"op", "limit_s"})
_DESCRIPTOR_KINDS: dict[str, tuple[Callable[[int], bool], ...]] = {
    "probe": (stat.S_ISREG,),
    "decode": (stat.S_ISREG, stat.S_ISFIFO),
}
# One more than the most any valid request carries, so that an extra descriptor is seen rather than dropped.
_MAX_REQUEST_FDS = 3
_FAILED = {"status": "failed"}

_FIFO_SAMPLES = 500_000
_BYTES_PER_SAMPLE = 2


def _send(connection: socket.socket, message: dict[str, Any]) -> bool:
    try:
        connection.send(json.dumps(message).encode("utf-8"))
    except OSError:
        return False
    return True


def _close_all(fds: Sequence[int]) -> None:
    for fd in fds:
        os.close(fd)


def _receive_request(connection: socket.socket, timeout_s: float) -> tuple[bytes, list[int], bool] | None:
    """Receives the request message with its descriptors.

    Args:
        connection: The accepted connection.
        timeout_s: Longest wait for the message.

    Returns:
        The body, the received descriptors (close-on-exec) and whether both were received whole; None when the
        caller closed the connection or sent nothing in time.
    """
    if not select.select([connection], [], [], timeout_s)[0]:
        return None
    try:
        data, ancillary, flags, _ = connection.recvmsg(
            MESSAGE_LIMIT, socket.CMSG_SPACE(_MAX_REQUEST_FDS * 4), socket.MSG_CMSG_CLOEXEC
        )
    except OSError:
        return None
    fds = array.array("i")
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            fds.frombytes(payload[: len(payload) - len(payload) % fds.itemsize])
    if not data and not fds:
        return None
    whole = not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
    return data, list(fds), whole


def _parse_request(data: bytes) -> tuple[str, float] | None:
    try:
        body = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(body, dict) or body.keys() != _REQUEST_KEYS:
        return None
    op, limit_s = body["op"], body["limit_s"]
    if not isinstance(op, str) or op not in _LIMITS_S:
        return None
    if isinstance(limit_s, bool) or not isinstance(limit_s, (int, float)):
        return None
    if not 0 < limit_s <= _LIMITS_S[op]:
        return None
    return op, float(limit_s)


def _descriptors_fit(op: str, fds: Sequence[int]) -> bool:
    kinds = _DESCRIPTOR_KINDS[op]
    return len(fds) == len(kinds) and all(is_kind(os.fstat(fd).st_mode) for is_kind, fd in zip(kinds, fds))


def _start_child(
    command: Sequence[str], op: str, fds: list[int]
) -> tuple[subprocess.Popen[bytes], int | None] | None:
    """Starts the decode child in the supervisor's own process group.

    Returns:
        The child and, for probe, the read end of its result pipe; None when the child could not be started.
        Either way the supervisor's copies of `fds` are closed.
    """
    result_read: int | None = None
    if op == "probe":
        result_read, output = os.pipe()
        passed = [fds[0], output]
    else:
        output = fds[1]
        passed = list(fds)
    try:
        child = subprocess.Popen(
            [*command, op, str(passed[0]), str(passed[1])],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=passed,
            close_fds=True,
        )
    except OSError:
        if result_read is not None:
            os.close(result_read)
        return None
    finally:
        _close_all(set(passed))
    return child, result_read


def _wait_for_end(connection: socket.socket, child: subprocess.Popen[bytes], limit_s: float) -> str:
    """Waits for the first of the child's end, the caller hanging up and `limit_s`.

    Returns:
        ``"hangup"``, ``"exited"`` or ``"timeout"``. Anything the caller sends after its request counts as hanging up.
    """
    pidfd = os.pidfd_open(child.pid)
    try:
        poller = select.poll()
        poller.register(connection.fileno(), select.POLLIN | select.POLLHUP | select.POLLERR)
        poller.register(pidfd, select.POLLIN)
        deadline = time.monotonic() + limit_s
        while (remaining := deadline - time.monotonic()) > 0:
            ready = {fd for fd, _ in poller.poll(math.ceil(remaining * 1000))}
            if connection.fileno() in ready:
                return "hangup"
            if pidfd in ready:
                return "exited"
        return "timeout"
    finally:
        os.close(pidfd)


def _read_duration(result_read: int) -> float | None:
    os.set_blocking(result_read, False)
    try:
        data = os.read(result_read, RESULT_LIMIT + 1)
    except BlockingIOError:
        return None
    if not data or len(data) > RESULT_LIMIT:
        return None
    try:
        body = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(body, dict) or body.keys() != {"duration_s"}:
        return None
    value = body["duration_s"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # RESULT_LIMIT keeps any integer far below the float range, so this conversion cannot overflow.
    duration = float(value)
    return duration if 0 <= duration < math.inf else None


def _verdict(op: str, returncode: int, result_read: int | None) -> dict[str, Any]:
    if returncode != 0:
        return _FAILED
    if result_read is None:
        return {"status": "ok"}
    duration = _read_duration(result_read)
    return _FAILED if duration is None else {"status": "ok", "duration_s": duration}


def _supervise(connection: socket.socket, child_command: Sequence[str], request_timeout_s: float) -> int:
    received = _receive_request(connection, request_timeout_s)
    if received is None:
        return 1
    data, fds, whole = received
    request = _parse_request(data) if whole else None
    if request is None or not _descriptors_fit(request[0], fds):
        _close_all(fds)
        _send(connection, {"started": False})
        return 0
    op, limit_s = request
    started = _start_child(child_command, op, fds)
    if started is None:
        _send(connection, {"started": False})
        return 0
    child, result_read = started
    try:
        if not _send(connection, {"started": True}):
            outcome = "hangup"
        else:
            outcome = _wait_for_end(connection, child, limit_s)
        if outcome != "exited":
            child.kill()
        returncode = child.wait()
        if outcome != "hangup":
            _send(connection, _verdict(op, returncode, result_read) if outcome == "exited" else _FAILED)
    finally:
        if result_read is not None:
            os.close(result_read)
    return 0


def _adopt_stdin() -> socket.socket | None:
    try:
        connection = socket.socket(fileno=0)
    except OSError:
        return None
    if connection.family == socket.AF_UNIX and connection.type == socket.SOCK_SEQPACKET:
        try:
            connection.getpeername()
        except OSError:
            pass
        else:
            return connection
    connection.close()
    return None


def serve(child_command: Sequence[str] = DEFAULT_CHILD_COMMAND, *, request_timeout_s: float = START_TIMEOUT_S) -> int:
    """Serves the one request on standard input and ends.

    The child is started as ``[*child_command, op, recording_fd, output_fd]``, in this
    process' own group so that killing the group also kills the child.

    Args:
        child_command: The decode child's command without the op and descriptors.
        request_timeout_s: Longest wait for the caller's request.

    Returns:
        0 when the request was answered (including a refusal) or the caller hung up; 1 when the caller sent no
        request; 2 when standard input is not a connected ``AF_UNIX`` ``SOCK_SEQPACKET`` socket.
    """
    connection = _adopt_stdin()
    if connection is None:
        return 2
    with connection:
        return _supervise(connection, child_command, request_timeout_s)


def container_duration_s(container: Any) -> float:
    """Returns the length of an opened container in seconds.

    Uses the container duration; when the container has none, sums the audio
    packet durations of the first audio stream without decoding.

    Args:
        container: An open PyAV input container.

    Returns:
        Duration in seconds.

    Raises:
        ValueError: If the container has no audio stream.
    """
    import av

    if not container.streams.audio:
        raise ValueError("container has no audio stream")
    if container.duration is not None:
        return float(container.duration) / av.time_base
    stream = container.streams.audio[0]
    return float(
        sum(float(packet.duration * stream.time_base) for packet in container.demux(stream) if packet.duration is not None)
    )


def _open_container(recording: BinaryIO) -> Any:
    import av

    return av.open(recording, metadata_errors="ignore", container_options={"format_whitelist": AUDIO_FORMATS})


def _probe(recording_fd: int, output_fd: int) -> None:
    with open(recording_fd, "rb") as recording, _open_container(recording) as container:
        duration = container_duration_s(container)
    with open(output_fd, "wb") as output:
        output.write(json.dumps({"duration_s": duration}).encode("utf-8"))


def _valid_frames(frames: Iterator[Any]) -> Iterator[Any]:
    import av

    while True:
        try:
            yield next(frames)
        except StopIteration:
            return
        except av.error.InvalidDataError:
            continue


def _write_resampled(resampler: Any, frame: Any, output: BinaryIO) -> None:
    for resampled in resampler.resample(frame):
        output.write(bytes(resampled.planes[0])[: resampled.samples * _BYTES_PER_SAMPLE])


def _decode(recording_fd: int, output_fd: int) -> None:
    import av

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    fifo = av.audio.fifo.AudioFifo()
    with open(recording_fd, "rb") as recording, open(output_fd, "wb") as output, _open_container(recording) as container:
        for frame in _valid_frames(container.decode(audio=0)):
            frame.pts = None
            fifo.write(frame)
            if fifo.samples >= _FIFO_SAMPLES:
                _write_resampled(resampler, fifo.read(), output)
        if fifo.samples > 0:
            _write_resampled(resampler, fifo.read(), output)
        _write_resampled(resampler, None, output)


_CHILD_ACTIONS: dict[str, Callable[[int, int], None]] = {"probe": _probe, "decode": _decode}


def main(argv: list[str]) -> int:
    """Runs the decode child: ``child probe|decode <recording fd> <output fd>``.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        0 on success; 1 on bad arguments or any error, with nothing written anywhere.
    """
    try:
        if len(argv) != 4 or argv[0] != "child":
            raise ValueError("expected: child probe|decode <recording fd> <output fd>")
        _CHILD_ACTIONS[argv[1]](int(argv[2]), int(argv[3]))
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
