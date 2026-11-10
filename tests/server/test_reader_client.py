"""Reader client tests: the five outcomes of ``probe()`` and ``decode()``.

The client talks to the emulated reader socket (systemd ``Accept=yes``) that runs either the real supervisor with
decode-child doubles (``reader_harness.py``, ``fake_decode_child.py``), the instance-level fake (``fake_reader.py``) or
the scripted fake (``scripted_reader.py``). No double reads or interprets recording content.
"""

from __future__ import annotations

import errno
import json
import math
import os
import shutil
import socket
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from stt.server import reader, reader_client

from .conftest import EmulatedReaderSocket, fake_reader_cmd, make_wav
from .test_reader import fake_child, supervisor_command

SCRIPTED = Path(__file__).with_name("scripted_reader.py")
OPS = ["probe", "decode"]
STARTED = {"send": '{"started": true}'}
SocketFactory = Callable[..., EmulatedReaderSocket]


def scripted(
    steps: list[dict[str, Any]], *, end: str = "close", marker: Path | None = None, skip_request: bool = False
) -> list[str]:
    command = [sys.executable, str(SCRIPTED), "--script", json.dumps(steps), "--end", end]
    if marker is not None:
        command += ["--marker", str(marker)]
    if skip_request:
        command.append("--skip-request")
    return command


def make_config(socket_path: Path | str, **overrides: float) -> reader_client.ReaderConfig:
    settings: dict[str, Any] = {
        "socket_path": str(socket_path),
        "start_timeout_s": 3.0,
        "verdict_grace_s": 1.0,
        "poll_s": 0.05,
        "probe_limit_s": 2.0,
        "decode_limit_s": 2.0,
    }
    settings.update(overrides)
    return reader_client.ReaderConfig(**settings)


def run(
    op: str,
    config: reader_client.ReaderConfig,
    recording: Path,
    *,
    job_running: Callable[[], bool] = lambda: True,
    job_limit_s: float = 60.0,
) -> reader_client.ProbeResult | reader_client.DecodeResult:
    if op == "probe":
        return reader_client.probe(config, recording)
    return reader_client.decode(config, recording, job_running=job_running, job_limit_s=job_limit_s)


@pytest.fixture
def recording(tmp_path: Path) -> Path:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"not interpreted by anyone")
    return path


@pytest.fixture
def short_dir() -> Iterator[Path]:
    # AF_UNIX paths are limited to ~107 bytes, which pytest's tmp_path can exceed.
    directory = Path(tempfile.mkdtemp(prefix="stt-rc-"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def open_fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def assert_fd_count_settles_at(baseline: int) -> None:
    deadline = time.monotonic() + 2.0
    while open_fd_count() > baseline and time.monotonic() < deadline:
        time.sleep(0.01)
    assert open_fd_count() == baseline, "the client left a descriptor open"


def read_marker(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ config


def test_config_defaults_match_the_contract() -> None:
    config = reader_client.ReaderConfig()

    assert config.socket_path == reader.SOCKET_PATH
    assert config.start_timeout_s == reader.START_TIMEOUT_S
    assert config.verdict_grace_s == reader.VERDICT_GRACE_S
    assert config.poll_s == 2.0
    assert config.probe_limit_s == reader.PROBE_LIMIT_S
    assert config.decode_limit_s == reader.DECODE_LIMIT_S


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("probe_limit_s", 0),
        ("probe_limit_s", -1),
        ("probe_limit_s", math.nan),
        ("probe_limit_s", reader.PROBE_LIMIT_S + 0.001),
        ("decode_limit_s", 0),
        ("decode_limit_s", math.inf),
        ("decode_limit_s", reader.DECODE_LIMIT_S + 0.001),
    ],
)
def test_a_limit_the_reader_would_refuse_is_rejected_when_the_config_is_made(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="limit"):
        reader_client.ReaderConfig(**{field: value})


@pytest.mark.parametrize("field", ["start_timeout_s", "verdict_grace_s", "poll_s"])
@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, -math.inf])
def test_a_waiting_time_that_is_not_finite_and_positive_is_rejected_when_the_config_is_made(
    field: str, value: float
) -> None:
    with pytest.raises(ValueError, match=f"{field} must be finite and above 0"):
        reader_client.ReaderConfig(**{field: value})


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, -math.inf])
def test_a_job_limit_that_is_not_finite_and_positive_is_rejected_by_decode_before_anything_is_sent(
    tmp_path: Path, value: float
) -> None:
    config = make_config(tmp_path / "missing.sock")

    with pytest.raises(ValueError, match="job_limit_s must be finite and above 0"):
        reader_client.decode(config, tmp_path / "missing.wav", job_running=lambda: True, job_limit_s=value)


# --------------------------------------------------- opening the recording


@pytest.mark.parametrize("op", OPS)
def test_a_symlinked_recording_raises_before_connecting(
    emulated_reader_socket: SocketFactory, tmp_path: Path, recording: Path, op: str
) -> None:
    link = tmp_path / "link.mp3"
    link.symlink_to(recording)
    server = emulated_reader_socket(fake_reader_cmd("no-start"))

    with pytest.raises(reader_client.RecordingError) as raised:
        run(op, make_config(server.path), link)

    assert raised.value.errno == 40  # ELOOP
    time.sleep(0.3)
    assert server.spawn_count == 0


@pytest.mark.parametrize("op", OPS)
def test_a_missing_recording_raises_before_connecting(
    emulated_reader_socket: SocketFactory, tmp_path: Path, op: str
) -> None:
    server = emulated_reader_socket(fake_reader_cmd("no-start"))

    with pytest.raises(reader_client.RecordingError) as raised:
        run(op, make_config(server.path), tmp_path / "missing.mp3")

    assert raised.value.errno == errno.ENOENT
    time.sleep(0.3)
    assert server.spawn_count == 0


@pytest.mark.parametrize("op", OPS)
def test_a_recording_that_is_not_a_regular_file_raises_before_connecting(
    emulated_reader_socket: SocketFactory, tmp_path: Path, op: str
) -> None:
    server = emulated_reader_socket(fake_reader_cmd("no-start"))

    with pytest.raises(reader_client.RecordingError, match="regular file") as raised:
        run(op, make_config(server.path), tmp_path)

    assert raised.value.errno is None
    time.sleep(0.3)
    assert server.spawn_count == 0


# ----------------------------------------------- the request that is sent


def test_probe_sends_the_recording_alone_and_decode_sends_it_with_a_pipe(
    emulated_reader_socket: SocketFactory, recording: Path
) -> None:
    probe_server = emulated_reader_socket(supervisor_command(fake_child("write-raw", payload='{"duration_s": 2.5}')))
    decode_server = emulated_reader_socket(supervisor_command(fake_child("write-bytes", count=4)))

    probed = reader_client.probe(make_config(probe_server.path), recording)
    decoded = reader_client.decode(
        make_config(decode_server.path), recording, job_running=lambda: True, job_limit_s=60
    )

    probe_server.wait_for_exits(1)
    decode_server.wait_for_exits(1)
    assert probed == reader_client.ProbeResult("ok", 2.5)
    assert decoded == reader_client.DecodeResult("ok", bytes(4))
    inode = os.stat(recording).st_ino
    [probe_fds] = probe_server.received()
    [decode_fds] = decode_server.received()
    assert probe_fds is not None and decode_fds is not None
    assert [(fd.kind, fd.ino) for fd in probe_fds] == [(stat.S_IFREG, inode)]
    assert [(fd.kind, fd.ino) for fd in decode_fds] == [(stat.S_IFREG, inode), (stat.S_IFIFO, decode_fds[1].ino)]


@pytest.mark.parametrize("op", OPS)
def test_the_client_sends_exactly_one_message(
    emulated_reader_socket: SocketFactory, recording: Path, tmp_path: Path, op: str
) -> None:
    marker = tmp_path / "marker.json"
    server = emulated_reader_socket(scripted([STARTED], end="hold", marker=marker))

    result = run(op, make_config(server.path, decode_limit_s=0.3, probe_limit_s=0.3, verdict_grace_s=0.1), recording)

    server.wait_for_exits(1)
    assert result.outcome == "failed"
    assert read_marker(marker)["extra_messages"] == 0


# ---------------------------------------------------------------- ok


def test_probe_returns_ok_with_the_duration_of_a_real_recording(
    emulated_reader_socket: SocketFactory, tmp_path: Path
) -> None:
    server = emulated_reader_socket(supervisor_command())
    path = make_wav(tmp_path / "tone.wav", 1.0, silent=False)

    result = reader_client.probe(make_config(server.path, probe_limit_s=20), path)

    server.wait_for_exits(1)
    assert result.outcome == "ok"
    assert result.duration_s == pytest.approx(1.0, abs=0.01)


def test_decode_returns_ok_with_the_decoded_samples_of_a_real_recording(
    emulated_reader_socket: SocketFactory, tmp_path: Path
) -> None:
    server = emulated_reader_socket(supervisor_command())
    path = make_wav(tmp_path / "tone.wav", 1.0, silent=False)

    result = reader_client.decode(
        make_config(server.path, decode_limit_s=20), path, job_running=lambda: True, job_limit_s=60
    )

    server.wait_for_exits(1)
    assert result.outcome == "ok"
    assert len(result.audio) == reader.SAMPLE_RATE * 2
    assert any(result.audio)


@pytest.mark.parametrize(
    ("reply", "duration_s"),
    [('{"status": "ok", "duration_s": 3}', 3.0), ('{"status": "ok", "duration_s": 0}', 0.0)],
)
def test_probe_accepts_a_finite_non_negative_duration(
    emulated_reader_socket: SocketFactory, recording: Path, reply: str, duration_s: float
) -> None:
    server = emulated_reader_socket(scripted([STARTED, {"send": reply}]))

    result = reader_client.probe(make_config(server.path), recording)

    assert result == reader_client.ProbeResult("ok", duration_s)
    assert isinstance(result.duration_s, float)


# ------------------------------------------------------------ unavailable


@pytest.mark.parametrize("op", OPS)
def test_a_missing_socket_is_unavailable(short_dir: Path, recording: Path, op: str) -> None:
    result = run(op, make_config(short_dir / "none.sock"), recording)

    assert result.outcome == "unavailable"


@pytest.mark.parametrize("op", OPS)
def test_a_socket_nobody_listens_on_is_unavailable(short_dir: Path, recording: Path, op: str) -> None:
    path = short_dir / "dead.sock"
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    dead.bind(str(path))
    dead.close()

    result = run(op, make_config(path), recording)

    assert result.outcome == "unavailable"


@pytest.mark.parametrize("op", OPS)
def test_a_connection_closed_before_started_is_unavailable(
    emulated_reader_socket: SocketFactory, recording: Path, op: str
) -> None:
    server = emulated_reader_socket(fake_reader_cmd("no-start"))

    assert run(op, make_config(server.path), recording).outcome == "unavailable"


@pytest.mark.parametrize("op", OPS)
def test_started_false_is_unavailable(emulated_reader_socket: SocketFactory, recording: Path, op: str) -> None:
    server = emulated_reader_socket(fake_reader_cmd("not-started"))

    assert run(op, make_config(server.path), recording).outcome == "unavailable"


@pytest.mark.parametrize("op", OPS)
def test_a_decode_child_that_cannot_be_started_is_unavailable(
    emulated_reader_socket: SocketFactory, recording: Path, tmp_path: Path, op: str
) -> None:
    server = emulated_reader_socket(supervisor_command([str(tmp_path / "no-such-command")]))

    assert run(op, make_config(server.path), recording).outcome == "unavailable"
    server.wait_for_exits(1)


@pytest.mark.parametrize("op", OPS)
def test_a_reader_that_resets_the_connection_before_started_is_unavailable(
    emulated_reader_socket: SocketFactory, recording: Path, op: str
) -> None:
    server = emulated_reader_socket(scripted([], skip_request=True))

    assert run(op, make_config(server.path), recording).outcome == "unavailable"


@pytest.mark.parametrize("op", OPS)
def test_no_started_within_the_start_time_is_unavailable_and_closes_the_connection(
    emulated_reader_socket: SocketFactory, recording: Path, tmp_path: Path, op: str
) -> None:
    marker = tmp_path / "marker.json"
    server = emulated_reader_socket(scripted([], end="hold", marker=marker))
    began = time.monotonic()

    result = run(op, make_config(server.path, start_timeout_s=0.3), recording)

    elapsed = time.monotonic() - began
    server.wait_for_exits(1)
    assert result.outcome == "unavailable"
    assert 0.25 <= elapsed < 2.5
    marked = read_marker(marker)
    assert marked["eof"] is True
    assert marked["pipe"] == ("closed" if op == "decode" else None)


STARTED_BAD = [
    ("started-is-a-string", {"send": '{"started": "yes"}'}),
    ("started-is-a-number", {"send": '{"started": 1}'}),
    ("started-is-null", {"send": '{"started": null}'}),
    ("extra-key", {"send": '{"started": true, "extra": 1}'}),
    ("empty-object", {"send": "{}"}),
    ("json-array", {"send": "[true]"}),
    ("json-string", {"send": '"started"'}),
    ("not-json", {"send": "started"}),
    ("deeply-nested", {"send": "[" * 3000}),
    ("a-verdict-instead", {"send": '{"status": "ok"}'}),
    ("above-the-message-limit", {"send": '{"started": true}' + " " * reader.MESSAGE_LIMIT}),
    ("with-a-descriptor", {"send_fd": '{"started": true}'}),
]


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("step", [case[1] for case in STARTED_BAD], ids=[case[0] for case in STARTED_BAD])
def test_a_started_message_that_breaks_the_protocol_is_unavailable(
    emulated_reader_socket: SocketFactory, recording: Path, op: str, step: dict[str, Any]
) -> None:
    server = emulated_reader_socket(scripted([step]))

    assert run(op, make_config(server.path), recording).outcome == "unavailable"


# ----------------------------------------------------------------- failed


@pytest.mark.parametrize("op", OPS)
def test_a_hang_up_without_a_verdict_after_started_is_failed(
    emulated_reader_socket: SocketFactory, recording: Path, op: str
) -> None:
    server = emulated_reader_socket(fake_reader_cmd("no-verdict"))

    assert run(op, make_config(server.path), recording).outcome == "failed"


@pytest.mark.parametrize("op", OPS)
def test_a_reset_after_started_is_failed(emulated_reader_socket: SocketFactory, recording: Path, op: str) -> None:
    server = emulated_reader_socket(scripted([STARTED], skip_request=True))

    assert run(op, make_config(server.path), recording).outcome == "failed"


@pytest.mark.parametrize("op", OPS)
def test_a_failed_verdict_is_failed(emulated_reader_socket: SocketFactory, recording: Path, op: str) -> None:
    server = emulated_reader_socket(scripted([STARTED, {"send": '{"status": "failed"}'}]))

    result = run(op, make_config(server.path), recording)

    assert result.outcome == "failed"
    assert not getattr(result, "audio", b"")
    assert getattr(result, "duration_s", None) is None


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("mode", ["exit-error", "sigkill", "hang"])
def test_a_decode_child_that_does_not_finish_cleanly_is_failed(
    emulated_reader_socket: SocketFactory, recording: Path, op: str, mode: str
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child(mode)))

    result = run(op, make_config(server.path, probe_limit_s=0.4, decode_limit_s=0.4), recording)

    server.wait_for_exits(1)
    assert result.outcome == "failed"


@pytest.mark.parametrize("payload", ["garbage", "", '{"duration_s": -1}', '{"duration_s": 1, "extra": 1}'])
def test_a_probe_result_the_supervisor_rejects_is_failed(
    emulated_reader_socket: SocketFactory, recording: Path, payload: str
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child("write-raw", payload=payload)))

    assert reader_client.probe(make_config(server.path), recording).outcome == "failed"
    server.wait_for_exits(1)


PROBE_BAD_VERDICTS = [
    ("status-unknown", '{"status": "done", "duration_s": 1}'),
    ("status-is-a-list", '{"status": ["ok"], "duration_s": 1}'),
    ("ok-without-duration", '{"status": "ok"}'),
    ("extra-key", '{"status": "ok", "duration_s": 1, "extra": 1}'),
    ("duration-negative", '{"status": "ok", "duration_s": -1}'),
    ("duration-nan", '{"status": "ok", "duration_s": NaN}'),
    ("duration-infinite", '{"status": "ok", "duration_s": Infinity}'),
    ("duration-overflows-to-infinity", '{"status": "ok", "duration_s": 1e999}'),
    ("duration-integer-above-float-range", '{"status": "ok", "duration_s": ' + "9" * 400 + "}"),
    ("duration-string", '{"status": "ok", "duration_s": "1"}'),
    ("duration-bool", '{"status": "ok", "duration_s": true}'),
    ("duration-null", '{"status": "ok", "duration_s": null}'),
]
DECODE_BAD_VERDICTS = [
    ("status-unknown", '{"status": "done"}'),
    ("ok-with-duration", '{"status": "ok", "duration_s": 1}'),
    ("extra-key", '{"status": "ok", "extra": 1}'),
]
BAD_VERDICTS_FOR_BOTH = [
    ("empty-object", "{}"),
    ("json-array", "[]"),
    ("not-json", "ok"),
    ("a-second-started", '{"started": true}'),
    ("above-the-message-limit", '{"status": "ok"' + " " * reader.MESSAGE_LIMIT + "}"),
]


@pytest.mark.parametrize(
    ("op", "reply"),
    [("probe", case[1]) for case in PROBE_BAD_VERDICTS + BAD_VERDICTS_FOR_BOTH]
    + [("decode", case[1]) for case in DECODE_BAD_VERDICTS + BAD_VERDICTS_FOR_BOTH],
    ids=[f"probe-{case[0]}" for case in PROBE_BAD_VERDICTS + BAD_VERDICTS_FOR_BOTH]
    + [f"decode-{case[0]}" for case in DECODE_BAD_VERDICTS + BAD_VERDICTS_FOR_BOTH],
)
def test_a_verdict_that_breaks_the_protocol_is_failed(
    emulated_reader_socket: SocketFactory, recording: Path, op: str, reply: str
) -> None:
    server = emulated_reader_socket(scripted([STARTED, {"send": reply}]))

    assert run(op, make_config(server.path), recording).outcome == "failed"


@pytest.mark.parametrize("op", OPS)
def test_a_verdict_with_a_descriptor_attached_is_failed(
    emulated_reader_socket: SocketFactory, recording: Path, op: str
) -> None:
    ok = '{"status": "ok", "duration_s": 1}' if op == "probe" else '{"status": "ok"}'
    server = emulated_reader_socket(scripted([STARTED, {"send_fd": ok}]))

    assert run(op, make_config(server.path), recording).outcome == "failed"


@pytest.mark.parametrize("op", OPS)
def test_no_verdict_within_the_limit_and_grace_is_failed_and_closes_the_connection(
    emulated_reader_socket: SocketFactory, recording: Path, tmp_path: Path, op: str
) -> None:
    marker = tmp_path / "marker.json"
    server = emulated_reader_socket(scripted([STARTED], end="hold", marker=marker))
    config = make_config(server.path, probe_limit_s=0.3, decode_limit_s=0.3, verdict_grace_s=0.2)
    began = time.monotonic()

    result = run(op, config, recording)

    elapsed = time.monotonic() - began
    server.wait_for_exits(1)
    assert result.outcome == "failed"
    assert 0.45 <= elapsed < 3.0
    marked = read_marker(marker)
    assert marked["eof"] is True
    assert marked["pipe"] == ("closed" if op == "decode" else None)


# ------------------------------------------------- decoded output checks


@pytest.fixture
def small_output_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(reader_client, "MAX_DECODED_BYTES", 1000)
    return 1000


def decode_bytes(server: EmulatedReaderSocket, recording: Path) -> reader_client.DecodeResult:
    result = reader_client.decode(
        make_config(server.path, decode_limit_s=60, verdict_grace_s=5),
        recording,
        job_running=lambda: True,
        job_limit_s=120,
    )
    server.wait_for_exits(1)
    return result


@pytest.mark.parametrize("count", [1, 3, 999])
def test_an_odd_number_of_decoded_bytes_is_failed(
    emulated_reader_socket: SocketFactory, recording: Path, count: int
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child("write-bytes", count=count)))

    result = decode_bytes(server, recording)

    assert result.outcome == "failed"
    assert not result.audio


@pytest.mark.parametrize("count", [2, 998, 1000])
def test_an_even_number_of_decoded_bytes_up_to_the_limit_is_ok(
    emulated_reader_socket: SocketFactory, recording: Path, small_output_limit: int, count: int
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child("write-bytes", count=count)))

    result = decode_bytes(server, recording)

    assert result.outcome == "ok"
    assert bytes(result.audio) == bytes(count)


@pytest.mark.parametrize("count", [1001, 1002, 200_000])
def test_more_decoded_bytes_than_the_limit_is_failed(
    emulated_reader_socket: SocketFactory, recording: Path, small_output_limit: int, count: int
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child("write-bytes", count=count)))

    result = decode_bytes(server, recording)

    assert result.outcome == "failed"
    assert not result.audio


def test_the_client_limit_is_the_contract_limit() -> None:
    assert reader_client.MAX_DECODED_BYTES is reader.MAX_DECODED_BYTES


def test_output_the_reader_wrote_before_an_error_exit_is_not_returned(
    emulated_reader_socket: SocketFactory, recording: Path
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child("write-raw-then-error", payload="abcd")))

    result = decode_bytes(server, recording)

    assert result.outcome == "failed"
    assert not result.audio


# ---------------------------------------------------------------- stopped


def test_a_job_that_is_no_longer_running_is_stopped_and_the_connection_and_pipe_are_closed(
    emulated_reader_socket: SocketFactory, recording: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "marker.json"
    server = emulated_reader_socket(scripted([STARTED], end="hold", marker=marker))
    began = time.monotonic()

    result = reader_client.decode(
        make_config(server.path), recording, job_running=lambda: time.monotonic() - began < 0.3, job_limit_s=60
    )

    elapsed = time.monotonic() - began
    server.wait_for_exits(1)
    assert result.outcome == "stopped"
    assert 0.25 <= elapsed < 2.0
    assert read_marker(marker) == {"eof": True, "extra_messages": 0, "pipe": "closed"}


def test_a_job_taken_away_while_the_reader_has_not_answered_yet_is_stopped(
    emulated_reader_socket: SocketFactory, recording: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "marker.json"
    server = emulated_reader_socket(scripted([], end="hold", marker=marker))

    result = reader_client.decode(make_config(server.path), recording, job_running=lambda: False, job_limit_s=60)

    server.wait_for_exits(1)
    assert result.outcome == "stopped"
    assert read_marker(marker) == {"eof": True, "extra_messages": 0, "pipe": "closed"}


def test_stopped_ends_the_real_supervisor_and_its_decode_child(
    emulated_reader_socket: SocketFactory, recording: Path, tmp_path: Path
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child("hang")))
    began = time.monotonic()

    result = reader_client.decode(
        make_config(server.path, decode_limit_s=60),
        recording,
        job_running=lambda: time.monotonic() - began < 0.3,
        job_limit_s=60,
    )

    server.wait_for_exits(1)
    assert result.outcome == "stopped"
    assert time.monotonic() - began < 5.0


def test_the_job_state_is_asked_about_every_poll_interval(
    emulated_reader_socket: SocketFactory, recording: Path
) -> None:
    server = emulated_reader_socket(scripted([STARTED], end="hold"))
    calls: list[float] = []

    def running() -> bool:
        calls.append(time.monotonic())
        return True

    began = time.monotonic()
    result = reader_client.decode(
        make_config(server.path, poll_s=0.1), recording, job_running=running, job_limit_s=0.55
    )

    server.wait_for_exits(1)
    assert result.outcome == "timeout"
    assert 3 <= len(calls) <= 7
    assert all(later - earlier >= 0.09 for earlier, later in zip(calls, calls[1:]))
    assert calls[0] - began >= 0.09


# ---------------------------------------------------------------- timeout


def test_the_job_limit_is_timeout_and_the_connection_and_pipe_are_closed(
    emulated_reader_socket: SocketFactory, recording: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "marker.json"
    server = emulated_reader_socket(scripted([STARTED], end="hold", marker=marker))
    began = time.monotonic()

    result = reader_client.decode(make_config(server.path), recording, job_running=lambda: True, job_limit_s=0.3)

    elapsed = time.monotonic() - began
    server.wait_for_exits(1)
    assert result.outcome == "timeout"
    assert 0.25 <= elapsed < 2.0
    assert read_marker(marker) == {"eof": True, "extra_messages": 0, "pipe": "closed"}


def test_the_job_limit_also_applies_while_the_reader_has_not_answered_yet(
    emulated_reader_socket: SocketFactory, recording: Path
) -> None:
    server = emulated_reader_socket(scripted([], end="hold"))

    result = reader_client.decode(
        make_config(server.path, start_timeout_s=30), recording, job_running=lambda: True, job_limit_s=0.3
    )

    server.wait_for_exits(1)
    assert result.outcome == "timeout"


def test_the_job_limit_coming_first_ends_the_real_supervisor_and_its_decode_child(
    emulated_reader_socket: SocketFactory, recording: Path
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child("hang")))
    began = time.monotonic()

    result = reader_client.decode(
        make_config(server.path, decode_limit_s=60), recording, job_running=lambda: True, job_limit_s=0.3
    )

    server.wait_for_exits(1)
    assert result.outcome == "timeout"
    assert time.monotonic() - began < 5.0


def test_the_reader_limit_coming_first_is_failed_not_timeout(
    emulated_reader_socket: SocketFactory, recording: Path
) -> None:
    server = emulated_reader_socket(supervisor_command(fake_child("hang")))

    result = reader_client.decode(
        make_config(server.path, decode_limit_s=0.3), recording, job_running=lambda: True, job_limit_s=60
    )

    server.wait_for_exits(1)
    assert result.outcome == "failed"


# ------------------------------------------------------------ descriptors


def test_the_client_holds_no_descriptor_after_any_outcome(
    emulated_reader_socket: SocketFactory, recording: Path
) -> None:
    ok = emulated_reader_socket(supervisor_command(fake_child("write-bytes", count=4)))
    attached = emulated_reader_socket(scripted([STARTED, {"send_fd": '{"status": "ok"}'}]))
    held = emulated_reader_socket(scripted([STARTED], end="hold"))
    unanswered = emulated_reader_socket(scripted([{"send_fd": '{"started": true}'}]))
    baseline = open_fd_count()
    config = make_config(ok.path)

    results = [
        reader_client.decode(config, recording, job_running=lambda: True, job_limit_s=60),
        reader_client.decode(make_config(attached.path), recording, job_running=lambda: True, job_limit_s=60),
        reader_client.decode(make_config(held.path), recording, job_running=lambda: False, job_limit_s=60),
        reader_client.decode(make_config(held.path), recording, job_running=lambda: True, job_limit_s=0.2),
        reader_client.probe(make_config(unanswered.path), recording),
        reader_client.probe(make_config("/nonexistent/stt-reader.sock"), recording),
    ]

    outcomes = [result.outcome for result in results]
    assert outcomes == ["ok", "failed", "stopped", "timeout", "unavailable", "unavailable"]
    for server in (ok, attached, held, unanswered):
        server.wait_for_exits(1 if server is not held else 2)
    assert_fd_count_settles_at(baseline)
