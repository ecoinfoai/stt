"""Reader protocol tests: the supervisor against decode-child doubles, and the real decode child.

The supervisor runs through ``reader_harness.py`` behind the emulated reader socket (systemd ``Accept=yes``).
Decode-child doubles come from ``fake_decode_child.py``; they never read or interpret recording content.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

from stt.server import reader

from .conftest import EmulatedReaderSocket, encode_tone, make_wav

HARNESS = Path(__file__).with_name("reader_harness.py")
FAKE_CHILD = Path(__file__).with_name("fake_decode_child.py")
VALID_PROBE_RESULT = '{"duration_s": 2.5}'

SocketFactory = Callable[..., EmulatedReaderSocket]


class Descriptors:
    """Opens descriptors a caller would send and closes whatever is left at teardown."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._open: set[int] = set()
        self._count = 0

    def recording(self) -> int:
        self._count += 1
        path = self._directory / f"recording{self._count}.bin"
        path.write_bytes(b"not interpreted by anyone")
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        self._open.add(fd)
        return fd

    def open_file(self, path: Path) -> int:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        self._open.add(fd)
        return fd

    def pipe(self) -> tuple[int, int]:
        read_end, write_end = os.pipe()
        self._open.update((read_end, write_end))
        return read_end, write_end

    def directory(self) -> int:
        fd = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        self._open.add(fd)
        return fd

    def close(self, fd: int) -> None:
        if fd in self._open:
            self._open.discard(fd)
            os.close(fd)

    def close_all(self) -> None:
        for fd in list(self._open):
            self.close(fd)


@pytest.fixture
def descriptors(tmp_path: Path) -> Iterator[Descriptors]:
    pool = Descriptors(tmp_path)
    yield pool
    pool.close_all()


def fake_child(
    mode: str, *, payload: str | None = None, count: int | None = None, marker: Path | None = None
) -> list[str]:
    command = [sys.executable, str(FAKE_CHILD), "--mode", mode]
    if payload is not None:
        command += ["--payload", payload]
    if count is not None:
        command += ["--count", str(count)]
    if marker is not None:
        command += ["--marker", str(marker)]
    return command


def supervisor_command(
    child: list[str] | None = None, *, request_timeout_s: float | None = None, report: Path | None = None
) -> list[str]:
    command = [sys.executable, str(HARNESS)]
    if child is not None:
        command += ["--child-cmd", json.dumps(child)]
    if request_timeout_s is not None:
        command += ["--request-timeout-s", str(request_timeout_s)]
    if report is not None:
        command += ["--report-modules", str(report)]
    return command


def connect(path: Path) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    client.settimeout(10.0)
    client.connect(str(path))
    return client


def send_request(client: socket.socket, body: dict[str, Any] | bytes, fds: list[int]) -> None:
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    socket.send_fds(client, [data], fds)


def receive_all(client: socket.socket) -> list[dict[str, Any]]:
    """Reads messages until the supervisor closes the connection."""
    messages: list[dict[str, Any]] = []
    while True:
        data = client.recv(reader.MESSAGE_LIMIT)
        if not data:
            return messages
        messages.append(json.loads(data))


class Outcome(NamedTuple):
    messages: list[dict[str, Any]]
    output: bytes


def exchange(path: Path, pool: Descriptors, op: str, limit_s: float) -> Outcome:
    """Sends one well-formed request, drains the decode pipe and collects the supervisor's messages."""
    recording = pool.recording()
    drained: list[bytes] = []
    thread: threading.Thread | None = None
    fds = [recording]
    if op == "decode":
        read_end, write_end = pool.pipe()
        fds.append(write_end)

        def drain() -> None:
            chunks = []
            while chunk := os.read(read_end, 65536):
                chunks.append(chunk)
            drained.append(b"".join(chunks))

        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
    with connect(path) as client:
        send_request(client, {"op": op, "limit_s": limit_s}, fds)
        for fd in fds:
            pool.close(fd)
        messages = receive_all(client)
    if thread is not None:
        thread.join(10.0)
        assert not thread.is_alive(), "decode pipe never reached end of file"
    return Outcome(messages, drained[0] if drained else b"")


# ---------------------------------------------------------------- constants


def test_constants_match_the_contract() -> None:
    assert reader.SOCKET_PATH == "/run/stt-reader.sock"
    assert reader.MESSAGE_LIMIT == 4096
    assert reader.RESULT_LIMIT == 256
    assert reader.PROBE_LIMIT_S == 30
    assert reader.DECODE_LIMIT_S == 240
    assert reader.START_TIMEOUT_S == 30
    assert reader.VERDICT_GRACE_S == 5
    assert reader.SAMPLE_RATE == 16000
    assert reader.MAX_DECODED_BYTES == 362_880_000
    assert reader.AUDIO_FORMATS == "mov,mp3,wav,flac,ogg,aac,asf"


# ------------------------------------------------- supervisor: (a) rejected requests

PROBE_BODY: dict[str, Any] = {"op": "probe", "limit_s": 5}
DECODE_BODY: dict[str, Any] = {"op": "decode", "limit_s": 5}

REJECTED_REQUESTS = [
    ("unknown-key", {"op": "probe", "limit_s": 5, "extra": 1}, ("file",)),
    ("missing-limit", {"op": "probe"}, ("file",)),
    ("missing-op", {"limit_s": 5}, ("file",)),
    ("unknown-op", {"op": "seek", "limit_s": 5}, ("file",)),
    ("op-not-a-string", {"op": 1, "limit_s": 5}, ("file",)),
    ("limit-is-a-string", {"op": "probe", "limit_s": "5"}, ("file",)),
    ("limit-is-null", {"op": "probe", "limit_s": None}, ("file",)),
    ("limit-is-a-bool", {"op": "probe", "limit_s": True}, ("file",)),
    ("limit-is-a-list", {"op": "probe", "limit_s": [5]}, ("file",)),
    ("limit-zero", {"op": "probe", "limit_s": 0}, ("file",)),
    ("limit-negative", {"op": "probe", "limit_s": -1}, ("file",)),
    ("probe-limit-above-bound", {"op": "probe", "limit_s": 30.001}, ("file",)),
    ("decode-limit-above-bound", {"op": "decode", "limit_s": 240.001}, ("file", "pipe")),
    ("limit-nan", b'{"op": "probe", "limit_s": NaN}', ("file",)),
    ("limit-infinity", b'{"op": "probe", "limit_s": Infinity}', ("file",)),
    ("limit-overflows-to-infinity", b'{"op": "probe", "limit_s": 1e999}', ("file",)),
    ("limit-huge-integer", json.dumps({"op": "probe", "limit_s": 10**400}).encode(), ("file",)),
    ("not-json", b"not json", ("file",)),
    ("not-utf-8", b"\xff\xfe\x00", ("file",)),
    ("json-array", b"[]", ("file",)),
    ("deeply-nested", b"[" * 3000, ("file",)),
    ("json-string", b'"probe"', ("file",)),
    ("message-above-limit", json.dumps(PROBE_BODY).encode() + b" " * reader.MESSAGE_LIMIT, ("file",)),
    ("probe-without-descriptor", PROBE_BODY, ()),
    ("probe-with-two-descriptors", PROBE_BODY, ("file", "pipe")),
    ("probe-with-a-pipe", PROBE_BODY, ("pipe",)),
    ("probe-with-a-directory", PROBE_BODY, ("dir",)),
    ("decode-with-one-descriptor", DECODE_BODY, ("file",)),
    ("decode-with-three-descriptors", DECODE_BODY, ("file", "pipe", "pipe")),
    ("decode-with-two-files", DECODE_BODY, ("file", "file")),
    ("decode-with-two-pipes", DECODE_BODY, ("pipe", "pipe")),
    ("decode-with-descriptors-swapped", DECODE_BODY, ("pipe", "file")),
    ("decode-with-a-directory", DECODE_BODY, ("file", "dir")),
]


@pytest.mark.parametrize(
    ("body", "kinds"), [case[1:] for case in REJECTED_REQUESTS], ids=[case[0] for case in REJECTED_REQUESTS]
)
def test_a_request_that_breaks_the_protocol_gets_started_false_and_starts_no_child(
    emulated_reader_socket: SocketFactory,
    descriptors: Descriptors,
    tmp_path: Path,
    body: dict[str, Any] | bytes,
    kinds: tuple[str, ...],
) -> None:
    marker = tmp_path / "child-started"
    supervisor = emulated_reader_socket(supervisor_command(fake_child("write-raw", payload=VALID_PROBE_RESULT, marker=marker)))
    fds = []
    for kind in kinds:
        if kind == "file":
            fds.append(descriptors.recording())
        elif kind == "pipe":
            fds.append(descriptors.pipe()[1])
        else:
            fds.append(descriptors.directory())

    with connect(supervisor.path) as client:
        send_request(client, body, fds)
        messages = receive_all(client)

    assert messages == [{"started": False}]
    supervisor.wait_for_exits(1)
    assert not marker.exists()
    assert supervisor.fd_counts() == [len(kinds)]


# ------------------------------------------- supervisor: (b) accepted requests


@pytest.mark.parametrize("op", ["probe", "decode"])
def test_a_valid_request_is_acknowledged_with_started_true_before_the_verdict(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, op: str
) -> None:
    supervisor = emulated_reader_socket(supervisor_command(fake_child("write-raw", payload=VALID_PROBE_RESULT)))

    outcome = exchange(supervisor.path, descriptors, op, 5)

    assert outcome.messages[0] == {"started": True}
    assert len(outcome.messages) == 2
    supervisor.wait_for_exits(1)


@pytest.mark.parametrize("op", ["probe", "decode"])
def test_the_child_is_spawned_as_the_contract_requires_in_the_supervisors_process_group(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, op: str
) -> None:
    supervisor = emulated_reader_socket(supervisor_command(fake_child("verify-spawn")))

    outcome = exchange(supervisor.path, descriptors, op, 5)

    expected = {"status": "ok", "duration_s": 1.5} if op == "probe" else {"status": "ok"}
    assert outcome.messages == [{"started": True}, expected]
    supervisor.wait_for_exits(1)


def test_a_child_that_cannot_be_started_gets_started_false(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, tmp_path: Path
) -> None:
    supervisor = emulated_reader_socket(supervisor_command([str(tmp_path / "no-such-command")]))

    outcome = exchange(supervisor.path, descriptors, "probe", 5)

    assert outcome.messages == [{"started": False}]
    supervisor.wait_for_exits(1)


# ------------------------------------------------- supervisor: (c) verdicts


def padded(text: str, size: int) -> str:
    return text + " " * (size - len(text.encode("utf-8")))


PROBE_VERDICTS = [
    ("ok", "write-raw", VALID_PROBE_RESULT, {"status": "ok", "duration_s": 2.5}),
    ("ok-zero", "write-raw", '{"duration_s": 0}', {"status": "ok", "duration_s": 0}),
    ("ok-at-result-limit", "write-raw", padded(VALID_PROBE_RESULT, reader.RESULT_LIMIT), {"status": "ok", "duration_s": 2.5}),
    ("oversized-result", "write-raw", padded(VALID_PROBE_RESULT, reader.RESULT_LIMIT + 1), {"status": "failed"}),
    ("child-error", "exit-error", None, {"status": "failed"}),
    ("child-killed", "sigkill", None, {"status": "failed"}),
    ("valid-result-but-error-exit", "write-raw-then-error", VALID_PROBE_RESULT, {"status": "failed"}),
    ("no-result", "write-raw", "", {"status": "failed"}),
    ("not-json", "write-raw", "garbage", {"status": "failed"}),
    ("json-array", "write-raw", "[1.5]", {"status": "failed"}),
    ("missing-key", "write-raw", "{}", {"status": "failed"}),
    ("extra-key", "write-raw", '{"duration_s": 1, "extra": 2}', {"status": "failed"}),
    ("duration-negative", "write-raw", '{"duration_s": -1}', {"status": "failed"}),
    ("duration-nan", "write-raw", '{"duration_s": NaN}', {"status": "failed"}),
    ("duration-infinite", "write-raw", '{"duration_s": 1e999}', {"status": "failed"}),
    ("duration-large-integer", "write-raw", '{"duration_s": ' + "9" * 200 + "}", {"status": "ok", "duration_s": 1e200}),
    ("duration-string", "write-raw", '{"duration_s": "1.5"}', {"status": "failed"}),
    ("duration-bool", "write-raw", '{"duration_s": true}', {"status": "failed"}),
    ("duration-null", "write-raw", '{"duration_s": null}', {"status": "failed"}),
]


@pytest.mark.parametrize(
    ("mode", "payload", "verdict"), [case[1:] for case in PROBE_VERDICTS], ids=[case[0] for case in PROBE_VERDICTS]
)
def test_probe_verdict_is_ok_only_for_a_clean_exit_with_a_valid_result(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, mode: str, payload: str | None, verdict: dict[str, Any]
) -> None:
    supervisor = emulated_reader_socket(supervisor_command(fake_child(mode, payload=payload)))

    outcome = exchange(supervisor.path, descriptors, "probe", 5)

    assert outcome.messages == [{"started": True}, verdict]
    supervisor.wait_for_exits(1)


DECODE_VERDICTS = [
    ("ok-without-output", "write-bytes", 0, {"status": "ok"}),
    ("ok-with-output", "write-bytes", 1_000_000, {"status": "ok"}),
    ("child-error", "exit-error", None, {"status": "failed"}),
    ("child-killed", "sigkill", None, {"status": "failed"}),
    ("output-then-error-exit", "write-raw-then-error", None, {"status": "failed"}),
]


@pytest.mark.parametrize(
    ("mode", "count", "verdict"), [case[1:] for case in DECODE_VERDICTS], ids=[case[0] for case in DECODE_VERDICTS]
)
def test_decode_verdict_is_ok_only_for_a_clean_exit_and_the_supervisor_does_not_police_the_pipe(
    emulated_reader_socket: SocketFactory,
    descriptors: Descriptors,
    mode: str,
    count: int | None,
    verdict: dict[str, Any],
) -> None:
    supervisor = emulated_reader_socket(supervisor_command(fake_child(mode, count=count, payload="xx")))

    outcome = exchange(supervisor.path, descriptors, "decode", 5)

    assert outcome.messages == [{"started": True}, verdict]
    if mode in ("write-bytes", "write-raw-then-error"):
        assert len(outcome.output) == (count if count is not None else 2)
    supervisor.wait_for_exits(1)


# ------------------------------------------- supervisor: (d) caller disconnects


@pytest.mark.parametrize("op", ["probe", "decode"])
def test_a_caller_that_hangs_up_gets_its_child_killed_and_the_supervisor_ends_promptly(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, tmp_path: Path, op: str
) -> None:
    marker = tmp_path / "child-pid"
    supervisor = emulated_reader_socket(supervisor_command(fake_child("hang", marker=marker)))
    fds = [descriptors.recording()]
    if op == "decode":
        fds.append(descriptors.pipe()[1])
    started = time.monotonic()

    with connect(supervisor.path) as client:
        send_request(client, {"op": op, "limit_s": reader.PROBE_LIMIT_S}, fds)
        assert receive_one(client) == {"started": True}
        child_pid = wait_for_child_pid(marker)

    supervisor.wait_for_exits(1)
    assert time.monotonic() - started < reader.PROBE_LIMIT_S / 2
    assert not Path(f"/proc/{child_pid}").exists()


def wait_for_child_pid(marker: Path, timeout_s: float = 5.0) -> int:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        text = marker.read_text(encoding="utf-8") if marker.exists() else ""
        if text:
            return int(text)
        time.sleep(0.01)
    raise AssertionError("the child never started")


def receive_one(client: socket.socket) -> dict[str, Any]:
    data = client.recv(reader.MESSAGE_LIMIT)
    assert data, "the supervisor closed the connection"
    return json.loads(data)


# ------------------------------------------- supervisor: an error while it waits


def test_a_child_is_killed_and_reaped_when_waiting_for_it_fails(
    descriptors: Descriptors, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[subprocess.Popen[bytes]] = []

    class RecordingPopen(subprocess.Popen):  # type: ignore[type-arg]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            started.append(self)

    def failing_pidfd_open(pid: int) -> int:
        raise OSError("injected failure")

    monkeypatch.setattr(reader.subprocess, "Popen", RecordingPopen)
    monkeypatch.setattr(reader.os, "pidfd_open", failing_pidfd_open)
    server_end, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        with server_end, client:
            send_request(client, PROBE_BODY, [descriptors.recording()])
            with pytest.raises(OSError, match="injected failure"):
                reader._supervise(server_end, fake_child("hang"), 5.0)
        assert [child.poll() for child in started] == [-signal.SIGKILL]
    finally:
        for child in started:
            if child.poll() is None:
                child.kill()
                child.wait()


# --------------------------------------------------- supervisor: (e) limit_s


@pytest.mark.parametrize("op", ["probe", "decode"])
def test_a_child_still_running_when_limit_s_passes_is_killed_and_the_verdict_is_failed(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, op: str
) -> None:
    supervisor = emulated_reader_socket(supervisor_command(fake_child("hang")))
    started = time.monotonic()

    outcome = exchange(supervisor.path, descriptors, op, 0.4)

    elapsed = time.monotonic() - started
    assert outcome.messages == [{"started": True}, {"status": "failed"}]
    assert 0.35 <= elapsed < 5.0
    supervisor.wait_for_exits(1)


# ----------------------------------------------------- ruling 5: process group


def test_killing_the_supervisors_process_group_also_kills_the_child(tmp_path: Path) -> None:
    marker = tmp_path / "child-pid"
    server_end, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    pool = Descriptors(tmp_path)
    proc = subprocess.Popen(
        supervisor_command(fake_child("hang", marker=marker)),
        stdin=server_end,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    server_end.close()
    try:
        recording = pool.recording()
        send_request(client, PROBE_BODY, [recording])
        assert receive_one(client) == {"started": True}
        child_pid = wait_for_child_pid(marker)

        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)

        deadline = time.monotonic() + 5
        while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not Path(f"/proc/{child_pid}").exists()
    finally:
        client.close()
        pool.close_all()
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


# ----------------------------- supervisor: (f) imports, (g) silence, (h) stdin


class Run(NamedTuple):
    code: int
    stdout: bytes
    stderr: bytes


def run_supervisor(stdin: Any, command: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )


def finish(proc: subprocess.Popen[bytes]) -> Run:
    try:
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return Run(proc.returncode, stdout, stderr)


def converse(command: list[str], pool: Descriptors, body: dict[str, Any] | bytes, kinds: tuple[str, ...]) -> tuple[Run, list[dict[str, Any]]]:
    server_end, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    client.settimeout(10.0)
    proc = run_supervisor(server_end, command)
    server_end.close()
    with client:
        fds = [pool.recording() if kind == "file" else pool.pipe()[1] for kind in kinds]
        send_request(client, body, fds)
        messages = receive_all(client)
    return finish(proc), messages


def test_the_supervisor_loads_nothing_outside_the_standard_library(descriptors: Descriptors, tmp_path: Path) -> None:
    report = tmp_path / "modules.json"
    command = supervisor_command(fake_child("write-raw", payload=VALID_PROBE_RESULT), report=report)

    run, messages = converse(command, descriptors, PROBE_BODY, ("file",))

    assert messages[-1] == {"status": "ok", "duration_s": 2.5}
    assert run.code == 0
    assert json.loads(report.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("child", "body", "kinds", "last_message"),
    [
        (fake_child("write-raw", payload=VALID_PROBE_RESULT), PROBE_BODY, ("file",), {"status": "ok", "duration_s": 2.5}),
        (fake_child("exit-error"), PROBE_BODY, ("file",), {"status": "failed"}),
        (fake_child("exit-error"), {"op": "bogus", "limit_s": 1}, ("file",), {"started": False}),
    ],
    ids=["ok", "child-failure", "rejected-request"],
)
def test_the_supervisor_writes_nothing_to_stdout_or_stderr(
    descriptors: Descriptors,
    child: list[str],
    body: dict[str, Any],
    kinds: tuple[str, ...],
    last_message: dict[str, Any],
) -> None:
    run, messages = converse(supervisor_command(child), descriptors, body, kinds)

    assert messages[-1] == last_message
    assert run.code == 0
    assert run.stdout == b""
    assert run.stderr == b""


def unconnected_seqpacket() -> socket.socket:
    return socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)


def listening_seqpacket(directory: Path) -> socket.socket:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    listener.bind(str(directory / "listen.sock"))
    listener.listen()
    return listener


def connected_stream() -> socket.socket:
    first, second = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    second.close()
    return first


@pytest.mark.parametrize("kind", ["devnull", "pipe", "regular-file", "unconnected", "listening", "stream"])
def test_stdin_that_is_not_a_connected_seqpacket_socket_ends_with_code_2_and_no_output(
    tmp_path: Path, kind: str
) -> None:
    opened: list[Any] = []
    if kind == "devnull":
        stdin: Any = subprocess.DEVNULL
    elif kind == "pipe":
        read_end, write_end = os.pipe()
        opened += [read_end, write_end]
        stdin = read_end
    elif kind == "regular-file":
        path = tmp_path / "stdin.txt"
        path.write_bytes(b"x")
        stdin = open(path, "rb")
        opened.append(stdin)
    elif kind == "unconnected":
        stdin = unconnected_seqpacket()
        opened.append(stdin)
    elif kind == "listening":
        stdin = listening_seqpacket(tmp_path)
        opened.append(stdin)
    else:
        stdin = connected_stream()
        opened.append(stdin)
    try:
        run = finish(run_supervisor(stdin, supervisor_command()))
    finally:
        for handle in opened:
            if isinstance(handle, int):
                os.close(handle)
            else:
                handle.close()

    assert run.code == 2
    assert run.stdout == b""
    assert run.stderr == b""


# ------------------------------------------------- supervisor: no request


def test_a_connection_closed_before_any_request_ends_the_supervisor_without_output(tmp_path: Path) -> None:
    server_end, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    proc = run_supervisor(server_end, supervisor_command(fake_child("exit-error")))
    server_end.close()
    client.close()

    run = finish(proc)

    assert run.code == 1
    assert run.stdout == b""
    assert run.stderr == b""


def test_a_connection_that_sends_no_request_in_time_ends_the_supervisor_without_output() -> None:
    server_end, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    proc = run_supervisor(server_end, supervisor_command(fake_child("exit-error"), request_timeout_s=0.3))
    server_end.close()
    started = time.monotonic()
    try:
        run = finish(proc)
    finally:
        client.close()

    assert time.monotonic() - started < 5.0
    assert run.code == 1
    assert run.stdout == b""
    assert run.stderr == b""


# =============================================================== real child

LISTED_FORMATS = [
    ("m4a", "ipod", "aac", 16000),
    ("mp3", "mp3", "libmp3lame", 16000),
    ("flac", "flac", "flac", 16000),
    ("ogg", "ogg", "libopus", 48000),
    ("opus", "opus", "libopus", 48000),
    ("aac", "adts", "aac", 16000),
    ("wma", "asf", "wmav2", 16000),
]
FORMAT_IDS = [case[0] for case in LISTED_FORMATS]


def read_recording(factory: SocketFactory, pool: Descriptors, path: Path, op: str) -> Outcome:
    """Reads `path` through the real supervisor and the real decode child."""
    supervisor = factory(supervisor_command())
    recording = pool.open_file(path)
    drained: list[bytes] = []
    thread: threading.Thread | None = None
    fds = [recording]
    if op == "decode":
        read_end, write_end = pool.pipe()
        fds.append(write_end)

        def drain() -> None:
            chunks = []
            while chunk := os.read(read_end, 65536):
                chunks.append(chunk)
            drained.append(b"".join(chunks))

        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
    limit_s = reader.PROBE_LIMIT_S if op == "probe" else reader.DECODE_LIMIT_S
    with connect(supervisor.path) as client:
        send_request(client, {"op": op, "limit_s": limit_s}, fds)
        for fd in fds:
            pool.close(fd)
        messages = receive_all(client)
    if thread is not None:
        thread.join(10.0)
        assert not thread.is_alive()
    supervisor.wait_for_exits(1, timeout_s=10.0)
    return Outcome(messages, drained[0] if drained else b"")


@pytest.mark.parametrize(("extension", "muxer", "codec", "rate"), LISTED_FORMATS, ids=FORMAT_IDS)
def test_probe_measures_the_formats_of_the_schema_extensions(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, tmp_path: Path, extension: str, muxer: str, codec: str, rate: int
) -> None:
    recording = encode_tone(tmp_path / f"tone.{extension}", muxer, codec, rate)

    outcome = read_recording(emulated_reader_socket, descriptors, recording, "probe")

    assert outcome.messages[0] == {"started": True}
    assert outcome.messages[1]["status"] == "ok"
    assert outcome.messages[1]["duration_s"] == pytest.approx(2.0, abs=0.2)


def test_probe_reads_the_container_duration(emulated_reader_socket: SocketFactory, descriptors: Descriptors, tmp_path: Path) -> None:
    recording = make_wav(tmp_path / "source.wav", 2.0)

    outcome = read_recording(emulated_reader_socket, descriptors, recording, "probe")

    assert outcome.messages[1]["duration_s"] == pytest.approx(2.0, abs=0.01)


def test_duration_without_a_container_duration_is_the_sum_of_the_packet_durations(tmp_path: Path) -> None:
    import av

    source = make_wav(tmp_path / "no_duration.wav", 3.0)

    class NoDuration:
        def __init__(self, container: object) -> None:
            self._container = container

        duration = None

        def __getattr__(self, name: str) -> object:
            return getattr(self._container, name)

    with av.open(str(source)) as container:
        assert reader.container_duration_s(NoDuration(container)) == pytest.approx(3.0, abs=0.05)


def test_duration_uses_the_container_duration_when_there_is_one() -> None:
    container = SimpleNamespace(streams=SimpleNamespace(audio=[object()]), duration=2_500_000)

    assert reader.container_duration_s(container) == 2.5


def test_duration_of_a_container_without_an_audio_stream_is_an_error() -> None:
    container = SimpleNamespace(streams=SimpleNamespace(audio=[]), duration=2_500_000)

    with pytest.raises(ValueError):
        reader.container_duration_s(container)


@pytest.mark.parametrize("op", ["probe", "decode"])
def test_a_text_file_with_a_recording_extension_fails(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, tmp_path: Path, op: str
) -> None:
    attachment = tmp_path / "note.mp3"
    attachment.write_text("This is a plain text note, not a recording.\n", encoding="utf-8")

    outcome = read_recording(emulated_reader_socket, descriptors, attachment, op)

    assert outcome.messages == [{"started": True}, {"status": "failed"}]
    assert outcome.output == b""


OUTSIDE_THE_LIST = [("aiff", "aiff", "pcm_s16be", 16000), ("au", "au", "pcm_s16be", 16000), ("webm", "webm", "libopus", 48000)]


@pytest.mark.parametrize("op", ["probe", "decode"])
@pytest.mark.parametrize(
    ("extension", "muxer", "codec", "rate"), OUTSIDE_THE_LIST, ids=[case[0] for case in OUTSIDE_THE_LIST]
)
def test_a_format_outside_the_list_fails(
    emulated_reader_socket: SocketFactory,
    descriptors: Descriptors,
    tmp_path: Path,
    op: str,
    extension: str,
    muxer: str,
    codec: str,
    rate: int,
) -> None:
    recording = encode_tone(tmp_path / f"tone.{extension}", muxer, codec, rate)

    outcome = read_recording(emulated_reader_socket, descriptors, recording, op)

    assert outcome.messages == [{"started": True}, {"status": "failed"}]
    assert outcome.output == b""


@pytest.mark.parametrize("op", ["probe", "decode"])
def test_a_format_outside_the_list_fails_even_with_a_listed_extension(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, tmp_path: Path, op: str
) -> None:
    recording = encode_tone(tmp_path / "tone.m4a", "aiff", "pcm_s16be")

    outcome = read_recording(emulated_reader_socket, descriptors, recording, op)

    assert outcome.messages == [{"started": True}, {"status": "failed"}]
    assert outcome.output == b""


def reference_int16_bytes(path: Path) -> bytes:
    """Returns faster-whisper's decoded samples as 16-bit little-endian bytes.

    ``decode_audio`` resamples to 16 kHz mono ``s16`` and then divides by 32768 in
    float32. That division is exact, so scaling back is lossless and the integers
    are the ones the resampler produced.
    """
    import numpy as np
    from faster_whisper.audio import decode_audio

    scaled = decode_audio(str(path)) * np.float32(32768.0)
    samples = scaled.astype("<i2")
    assert np.array_equal(samples.astype(np.float32), scaled)
    return bytes(samples.tobytes())


def tone_wav(path: Path) -> Path:
    return make_wav(path, 2.0, silent=False)


def silent_wav(path: Path) -> Path:
    return make_wav(path, 2.0)


@pytest.mark.parametrize(("extension", "muxer", "codec", "rate"), LISTED_FORMATS, ids=FORMAT_IDS)
def test_decode_output_equals_faster_whisper_int16_for_each_format(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, tmp_path: Path, extension: str, muxer: str, codec: str, rate: int
) -> None:
    recording = encode_tone(tmp_path / f"tone.{extension}", muxer, codec, rate)

    outcome = read_recording(emulated_reader_socket, descriptors, recording, "decode")

    assert outcome.messages == [{"started": True}, {"status": "ok"}]
    assert len(outcome.output) > 0
    assert outcome.output == reference_int16_bytes(recording)


@pytest.mark.parametrize("make", [tone_wav, silent_wav], ids=["wav-tone", "wav-silence"])
def test_decode_output_equals_faster_whisper_int16_for_wav(
    emulated_reader_socket: SocketFactory, descriptors: Descriptors, tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    recording = make(tmp_path / "source.wav")

    outcome = read_recording(emulated_reader_socket, descriptors, recording, "decode")

    assert outcome.messages == [{"started": True}, {"status": "ok"}]
    assert outcome.output == reference_int16_bytes(recording)


def test_the_child_reads_through_the_descriptor_and_its_command_line_holds_no_path(
    tmp_path: Path, descriptors: Descriptors
) -> None:
    recording = encode_tone(tmp_path / "secret-name.m4a", "ipod", "aac")
    fd = os.open(recording, os.O_RDONLY | os.O_CLOEXEC)
    recording.unlink()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    read_end, write_end = os.pipe()
    write_fd = write_end
    try:
        command = [sys.executable, "-m", "stt.server.reader", "child", "probe", str(fd), str(write_end)]
        proc = subprocess.run(
            command,
            cwd=elsewhere,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(fd, write_end),
            timeout=30,
            check=False,
        )
        os.close(write_end)
        write_end = -1
        result = json.loads(os.read(read_end, reader.RESULT_LIMIT))
    finally:
        os.close(fd)
        os.close(read_end)
        if write_end != -1:
            os.close(write_end)

    assert proc.returncode == 0
    assert proc.stdout == b"" and proc.stderr == b""
    assert result["duration_s"] == pytest.approx(2.0, abs=0.2)
    assert command[3:] == ["child", "probe", str(fd), str(write_fd)]
    assert "secret-name" not in " ".join(command)


@pytest.mark.parametrize(
    "arguments",
    [[], ["probe"], ["probe", "3"], ["seek", "3", "4"], ["probe", "x", "4"], ["probe", "3", "4", "5"]],
    ids=["none", "op-only", "one-fd", "unknown-op", "fd-not-a-number", "extra-argument"],
)
def test_the_child_ends_with_code_1_on_bad_arguments(arguments: list[str]) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "stt.server.reader", "child", *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 1
    assert proc.stdout == b"" and proc.stderr == b""
