"""Shared helpers and fixtures for the server transcription tests.

Production modules (``stt.server.*``) are imported inside helpers and
fixtures so that test collection works before those modules exist.
"""

from __future__ import annotations

import array
import json
import math
import os
import select
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest

if TYPE_CHECKING:
    from stt.server.reader_client import ReaderConfig

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")
FAKE_READER = Path(__file__).with_name("fake_reader.py")
READER_HARNESS = Path(__file__).with_name("reader_harness.py")
MESSAGE_LIMIT = 4096
MAX_PEEKED_FDS = 16
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


def encode_tone(path: Path, container: str, codec: str, rate: int = 16000, seconds: float = 2.0) -> Path:
    """Writes a mono 440 Hz tone with PyAV.

    Args:
        path: Destination file.
        container: FFmpeg muxer name, such as ``ipod`` or ``adts``.
        codec: FFmpeg encoder name, such as ``aac``; encoded at 64 kbit/s where the codec takes a bit rate.
        rate: Sample rate in Hz.
        seconds: Duration in seconds.

    Returns:
        The written path.
    """
    import av
    import numpy as np

    samples = int(rate * seconds)
    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(samples) / rate)).astype(np.float32)
    with av.open(str(path), "w", format=container) as output:
        stream = output.add_stream(codec, rate=rate, layout="mono")
        stream.bit_rate = 64000
        frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
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
    mode: int = 0o640,
) -> Path:
    """Hands over a delete request, which deletes everything (``.json.tmp``, then rename).

    Args:
        inbox: The inbox directory.
        request_id: Lowercase UUID of this delete request.
        requested_by: Opaque requester marker.
        target: ``request_id`` of the transcribe request whose recording and jobs go.
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


def leftovers_besides_recordings(root: Path) -> list[Path]:
    """Lists the leftovers outside ``audio/``; a recording stays until a delete request or the daily purge.

    Args:
        root: The state root directory.

    Returns:
        Sorted paths of the leftovers not under ``audio/``.
    """
    audio = root / "audio"
    return [path for path in leftovers(root) if audio not in path.parents]


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


def run_sweep(layout: Any, clock: FakeClock) -> int:
    """Runs ``sweep.run_once``, holding ``sweep.lock`` like the CLI.

    Args:
        layout: A ``stt.server.layout.Layout``.
        clock: The injected clock.

    Returns:
        What ``sweep.run_once`` returns: the number of items it could not clean.
    """
    from stt.server import sweep
    from stt.server.layout import hold

    with hold(layout.sweep_lock):
        return sweep.run_once(layout, clock.now)


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


def fake_reader_cmd(mode: str) -> list[str]:
    """Builds the command that runs ``fake_reader.py`` in `mode`.

    Args:
        mode: One of ``no-start``, ``not-started`` or ``no-verdict``.

    Returns:
        An argument list for ``EmulatedReaderSocket``.
    """
    return [sys.executable, str(FAKE_READER), "--mode", mode]


class ReceivedFd(NamedTuple):
    """Identity of one descriptor a caller sent with its request.

    Attributes:
        kind: ``stat.S_IFMT`` of the file, e.g. ``stat.S_IFREG`` or ``stat.S_IFIFO``.
        dev: ``st_dev`` of the file.
        ino: ``st_ino`` of the file.
    """

    kind: int
    dev: int
    ino: int


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


class EmulatedReaderSocket:
    """A temporary SEQPACKET socket that mirrors systemd ``Accept=yes``.

    For every accepted connection it peeks at the request (``MSG_PEEK``, so the
    message and its descriptors stay queued for the spawned program), records
    the identity of the descriptors, starts a new process from `cmd` in its own
    session with the connection as standard input (argument list, no shell,
    stdout to ``/dev/null``) and closes its own copy. The peek waits at most
    `peek_timeout_s` for the request; a connection that sends nothing in time
    is recorded as None and spawned anyway. Peeking is serial, so a silent
    client delays the next accept by that long.

    Because the record comes from the fixture, it works for any spawned
    program, the real supervisor included.

    Attributes:
        path: Socket path to hand to the caller under test.
    """

    def __init__(self, cmd: list[str], peek_timeout_s: float = 1.0) -> None:
        # AF_UNIX paths are limited to ~107 bytes, which pytest's tmp_path can exceed.
        self._dir = Path(tempfile.mkdtemp(prefix="stt-rd-"))
        self._cmd = list(cmd)
        self._peek_timeout_s = peek_timeout_s
        self._lock = threading.Lock()
        self._procs: list[subprocess.Popen[bytes]] = []
        self._received: list[tuple[ReceivedFd, ...] | None] = []
        self._error: BaseException | None = None
        self._stop = threading.Event()
        self._closed = False
        self.path = self._dir / "reader.sock"
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self._listener.bind(str(self.path))
            self._listener.listen()
        except BaseException:
            self._listener.close()
            shutil.rmtree(self._dir, ignore_errors=True)
            raise
        self._listener.settimeout(0.05)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _peek(self, connection: socket.socket) -> tuple[ReceivedFd, ...] | None:
        if not select.select([connection], [], [], self._peek_timeout_s)[0]:
            return None
        data, ancillary, flags, _ = connection.recvmsg(
            MESSAGE_LIMIT, socket.CMSG_SPACE(MAX_PEEKED_FDS * 4), socket.MSG_PEEK | socket.MSG_CMSG_CLOEXEC
        )
        fds = array.array("i")
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds.frombytes(payload[: len(payload) - len(payload) % fds.itemsize])
        identities: list[ReceivedFd] = []
        try:
            for fd in fds:
                info = os.fstat(fd)
                identities.append(ReceivedFd(stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino))
        finally:
            for fd in fds:
                os.close(fd)
        if flags & socket.MSG_CTRUNC:
            raise AssertionError("request carried more descriptors than the fixture can record")
        if not data and not identities:
            return None
        return tuple(identities)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                try:
                    peeked = self._peek(connection)
                    with self._lock:
                        cmd = list(self._cmd)
                    proc = subprocess.Popen(
                        cmd,
                        stdin=connection,
                        stdout=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except BaseException as exc:
                    self._error = exc
                    return
                with self._lock:
                    self._received.append(peeked)
                    self._procs.append(proc)

    def _raise_serve_error(self) -> None:
        if self._error is not None:
            raise AssertionError("emulated reader socket failed to serve a connection") from self._error

    def set_command(self, cmd: list[str]) -> None:
        """Makes every connection accepted from now on run `cmd`.

        Args:
            cmd: Argument list of the program to start per connection.
        """
        with self._lock:
            self._cmd = list(cmd)

    @property
    def spawn_count(self) -> int:
        """Returns how many reader processes have been started."""
        with self._lock:
            return len(self._procs)

    def received(self) -> list[tuple[ReceivedFd, ...] | None]:
        """Returns, per spawned process, the descriptors its request carried.

        An entry is None when the client sent no request before the peek timed
        out or hung up.
        """
        with self._lock:
            return list(self._received)

    def fd_counts(self) -> list[int | None]:
        """Returns, per spawned process, how many descriptors its request carried."""
        return [None if fds is None else len(fds) for fds in self.received()]

    def _all_gone(self) -> bool:
        with self._lock:
            procs = list(self._procs)
        return all(proc.poll() is not None and not _group_alive(proc.pid) for proc in procs)

    def wait_for_exits(self, count: int, timeout_s: float = 5.0) -> None:
        """Waits until `count` processes were started and their process groups are empty.

        Args:
            count: Expected number of started processes.
            timeout_s: Longest wait.

        Raises:
            AssertionError: If that state is not reached in time, or serving failed.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._raise_serve_error()
            if self.spawn_count >= count and self._all_gone():
                return
            time.sleep(0.01)
        self._raise_serve_error()
        raise AssertionError(f"reader processes did not settle: spawned={self.spawn_count}, expected={count}")

    def close(self, grace_s: float = 5.0) -> None:
        """Stops listening, reaps the processes and asserts none was left running.

        A process counts as left running when it, or anything else in its
        process group (e.g. a decoding child), is alive `grace_s` after the
        listener stopped. Such groups are killed with SIGKILL.

        Args:
            grace_s: How long a process group may take to end by itself.

        Raises:
            AssertionError: If a process group was still running after `grace_s`,
                or serving a connection had failed.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join()
        self._listener.close()
        with self._lock:
            procs = list(self._procs)
        deadline = time.monotonic() + grace_s
        running = 0
        for proc in procs:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            while proc.poll() is None or _group_alive(proc.pid):
                if time.monotonic() >= deadline:
                    running += 1
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    while _group_alive(proc.pid):
                        time.sleep(0.01)
                    break
                time.sleep(0.01)
        shutil.rmtree(self._dir, ignore_errors=True)
        self._raise_serve_error()
        assert running == 0, f"{running} reader process group(s) still running at teardown"


@pytest.fixture
def emulated_reader_socket() -> Iterator[Callable[..., EmulatedReaderSocket]]:
    """Returns a factory for opt-in emulated reader sockets.

    ``factory(cmd, peek_timeout_s=1.0)`` starts a socket that runs `cmd` once
    per connection; the result exposes ``path``, ``spawn_count``,
    ``received()``, ``fd_counts()`` and ``wait_for_exits()``. At teardown every
    socket is closed and the test fails if a spawned process (or anything in
    its process group) is still running.
    """
    created: list[EmulatedReaderSocket] = []

    def factory(cmd: list[str], peek_timeout_s: float = 1.0) -> EmulatedReaderSocket:
        emulated = EmulatedReaderSocket(cmd, peek_timeout_s)
        created.append(emulated)
        return emulated

    yield factory
    errors: list[str] = []
    for emulated in created:
        try:
            emulated.close()
        except AssertionError as exc:
            errors.append(str(exc))
    if errors:
        raise AssertionError("; ".join(errors))


class ReaderSocket:
    """An emulated reader socket that, unless told otherwise, runs the real supervisor.

    Every connection starts ``python -m stt server read`` with the connection as
    standard input, as systemd does for ``stt-reader@.service``. A test that needs
    another reader chooses the command *before* running a stage with one of the
    ``use_*`` methods; the choice applies to the connections accepted afterwards.

    Attributes:
        config: A ``reader_client.ReaderConfig`` for this socket's path, to hand to the
            code under test.
    """

    def __init__(self, emulated: EmulatedReaderSocket, config: ReaderConfig) -> None:
        self._emulated = emulated
        self.config = config

    @property
    def path(self) -> Path:
        """Returns the socket path."""
        return self._emulated.path

    @property
    def spawn_count(self) -> int:
        """Returns how many reader processes have been started."""
        return self._emulated.spawn_count

    def received(self) -> list[tuple[ReceivedFd, ...] | None]:
        """Returns, per started process, the descriptors its request carried."""
        return self._emulated.received()

    def fd_counts(self) -> list[int | None]:
        """Returns, per started process, how many descriptors its request carried."""
        return self._emulated.fd_counts()

    def wait_for_exits(self, count: int, timeout_s: float = 5.0) -> None:
        """Waits until `count` processes were started and their process groups are empty.

        Raises:
            AssertionError: If that state is not reached in time.
        """
        self._emulated.wait_for_exits(count, timeout_s)

    def use_command(self, cmd: list[str]) -> None:
        """Runs `cmd` instead of the real supervisor for the connections accepted from now on."""
        self._emulated.set_command(cmd)

    def use_fake(self, mode: str) -> None:
        """Runs ``fake_reader.py`` in `mode` (``no-start``, ``not-started`` or ``no-verdict``)."""
        self._emulated.set_command(fake_reader_cmd(mode))

    def use_supervisor(self, child_command: list[str]) -> None:
        """Runs the real supervisor with a decode-child double.

        Args:
            child_command: The child command without the operation and descriptors, which the
                supervisor appends, e.g. ``[sys.executable, str(FAKE_DECODE_CHILD), "--mode", "hang"]``.
        """
        self._emulated.set_command(
            [sys.executable, str(READER_HARNESS), "--child-cmd", json.dumps(child_command)]
        )


@pytest.fixture
def reader_socket(emulated_reader_socket: Callable[..., EmulatedReaderSocket]) -> ReaderSocket:
    """Starts an emulated reader socket whose default spawn command is the real supervisor.

    Not autouse: a test asks for it. Its ``config`` is for the caller under test, and
    ``use_command``, ``use_fake`` and ``use_supervisor`` swap the spawned program.
    """
    from stt.server.reader_client import ReaderConfig

    emulated = emulated_reader_socket([sys.executable, "-m", "stt", "server", "read"])
    return ReaderSocket(emulated, ReaderConfig(socket_path=str(emulated.path), poll_s=0.05))
