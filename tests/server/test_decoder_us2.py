"""US2 of the decoder isolation: the GPU stage never sees or decodes the recording; every read is its own process.

These tests drive intake, the worker and the engine against the real reader supervisor behind the emulated
reader socket (``reader_socket``). They fail until intake and the worker use the reader.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stt import diarize, transcribe
from stt.server import engine, intake
from stt.server.layout import Layout

from .conftest import (
    FAKE_ENGINE,
    FakeClock,
    ReaderSocket,
    ReceivedFd,
    make_wav,
    new_id,
    run_worker,
    run_worker_once,
)
from .inbox_helpers import make_layout, put_attachment, put_body, read_response, transcribe_body
from .test_engine import PLAIN_SEGMENTS, FakeModel

GATED_CHILD = Path(__file__).with_name("gated_decode_child.py")
SERVER_SOURCE = Path(engine.__file__).parent
REQUEST_ID = "0b0c7a2e-5d63-4c43-9a3e-6c1f0b7d9e11"
SECONDS_BOUND = 10.0
WAIT_S = 10.0
GATE_MAX_WAIT_S = 5.0


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def tone(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "tone.wav", 2.0, silent=False)


@pytest.fixture
def silence(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "silence.wav", 1.0)


def submit(layout: Layout, recording: Path, clock: FakeClock) -> str:
    """Hands over one transcribe request carrying `recording` and lets intake accept it."""
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio{recording.suffix}", recording)
    body = transcribe_body(request_id, audio={"file": f"{request_id}.audio{recording.suffix}"})
    put_body(layout.inbox, f"{request_id}.json", body)
    intake.run_once(layout, clock.now)
    return request_id


def submit_second_pass(layout: Layout, clock: FakeClock, source: str) -> str:
    """Hands over a request that reuses the recording of `source` and lets intake accept it."""
    request_id = new_id()
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, audio={"from_request": source}))
    intake.run_once(layout, clock.now)
    return request_id


def recording_identity(layout: Layout, audio_id: str) -> ReceivedFd:
    (path,) = (layout.audio / audio_id).glob("audio.*")
    info = os.stat(path)
    return ReceivedFd(stat.S_IFREG, info.st_dev, info.st_ino)


def decode_connections(reader_socket: ReaderSocket) -> list[tuple[ReceivedFd, ...]]:
    """Returns the descriptors of the connections that asked for a decode (they carry a pipe)."""
    return [
        fds
        for fds in reader_socket.received()
        if fds is not None and any(fd.kind == stat.S_IFIFO for fd in fds)
    ]


def pipe_identity(fds: tuple[ReceivedFd, ...]) -> ReceivedFd:
    (pipe,) = [fd for fd in fds if fd.kind == stat.S_IFIFO]
    return pipe


def recorded_engine_cmd(record: Path) -> list[str]:
    return [sys.executable, str(FAKE_ENGINE), "--mode", "done", "--record", str(record)]


def test_decoder_us2_5_engine_gets_only_decoded_audio_and_no_recording_path(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket, tmp_path: Path
) -> None:
    request_id = submit(layout, tone, clock)
    record = tmp_path / "engine.jsonl"

    run_worker(layout, clock, engine_cmd=recorded_engine_cmd(record))

    (line,) = record.read_text(encoding="utf-8").splitlines()
    seen = json.loads(line)
    assert "audio_path" not in seen["spec"]
    assert str(layout.audio) not in json.dumps(seen["spec"])
    assert sorted(seen["work_files"]) == ["audio.wav", "spec.json"]
    assert seen["work_files"]["audio.wav"] == 0o600
    assert seen["wav"] == {"channels": 1, "sample_width": 2, "frame_rate": 16000}
    assert read_response(layout, request_id)["state"] == "done"


def test_decoder_us2_6_two_jobs_are_read_by_two_connections_with_one_recording_each(
    layout: Layout, tone: Path, silence: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    first = submit(layout, tone, clock)
    second = submit(layout, silence, clock)

    run_worker(layout, clock)

    decodes = decode_connections(reader_socket)
    assert len(decodes) == 2
    for fds in decodes:
        assert [fd.kind for fd in fds].count(stat.S_IFREG) == 1
    assert {fd for fds in decodes for fd in fds if fd.kind == stat.S_IFREG} == {
        recording_identity(layout, first),
        recording_identity(layout, second),
    }
    assert pipe_identity(decodes[0]) != pipe_identity(decodes[1])


def test_decoder_us2_8_one_intake_run_with_a_good_and_a_text_attachment_answers_both_in_time(
    layout: Layout, tone: Path, tmp_path: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    text = tmp_path / "notes.mp3"
    text.write_text("This is plain text, not audio.\n", encoding="utf-8")
    good_id, text_id = new_id(), new_id()
    for request_id, recording in ((good_id, tone), (text_id, text)):
        put_attachment(layout.inbox, f"{request_id}.audio{recording.suffix}", recording)
        body = transcribe_body(request_id, audio={"file": f"{request_id}.audio{recording.suffix}"})
        put_body(layout.inbox, f"{request_id}.json", body)

    started = time.monotonic()
    intake.run_once(layout, clock.now)
    elapsed = time.monotonic() - started

    assert elapsed < SECONDS_BOUND
    assert read_response(layout, good_id)["state"] == "queued"
    failed = read_response(layout, text_id)
    assert (failed["state"], failed["failure"]["reason"]) == ("failed", "unreadable_audio")
    assert reader_socket.fd_counts() == [1, 1]


def test_decoder_edge_1_a_second_pass_on_a_waiting_first_pass_is_read_on_its_own_connection(
    layout: Layout, tone: Path, clock: FakeClock, reader_socket: ReaderSocket
) -> None:
    first = submit(layout, tone, clock)
    submit_second_pass(layout, clock, first)

    run_worker(layout, clock)

    decodes = decode_connections(reader_socket)
    assert len(decodes) == 2
    shared = recording_identity(layout, first)
    assert [[fd for fd in fds if fd.kind == stat.S_IFREG] for fds in decodes] == [[shared], [shared]]
    assert pipe_identity(decodes[0]) != pipe_identity(decodes[1])


def test_decoder_edge_2_a_length_probe_and_a_decode_that_overlap_use_different_connections(
    layout: Layout, tone: Path, silence: Path, clock: FakeClock, reader_socket: ReaderSocket, tmp_path: Path
) -> None:
    started, release = tmp_path / "decode.started", tmp_path / "decode.release"
    reader_socket.use_supervisor(
        [
            sys.executable,
            str(GATED_CHILD),
            "--started", str(started),
            "--release", str(release),
            "--max-wait-s", str(GATE_MAX_WAIT_S),
        ]
    )
    first = submit(layout, tone, clock)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            run_worker_once(layout, clock)
        except BaseException as error:
            errors.append(error)

    worker_thread = threading.Thread(target=work)
    worker_thread.start()
    try:
        deadline = time.monotonic() + WAIT_S
        while not started.exists() and worker_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists(), "the worker never asked the reader to decode"
        second = submit(layout, silence, clock)
    finally:
        release.touch()
        worker_thread.join(timeout=WAIT_S)
    assert not worker_thread.is_alive()
    assert errors == []
    assert read_response(layout, first)["state"] == "done"
    assert read_response(layout, second)["state"] == "queued"

    received = reader_socket.received()
    assert len(received) == 3
    assert reader_socket.spawn_count == 3
    (decode,) = decode_connections(reader_socket)
    probes = [fds for fds in received if fds is not None and fds != decode]
    assert sorted(fds[0] for fds in probes) == sorted(
        [recording_identity(layout, first), recording_identity(layout, second)]
    )
    assert [fd for fd in decode if fd.kind == stat.S_IFREG] == [recording_identity(layout, first)]


_IMPORT_SERVER_STAGES = """
import json, sys
import stt.server.intake, stt.server.worker
print(json.dumps({
    "av": "av" in sys.modules,
    "files": {m: sys.modules[m].__file__ for m in sorted(sys.modules) if m.startswith("stt.server.")},
}))
"""


def _run_stage_import() -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, "-c", _IMPORT_SERVER_STAGES], capture_output=True, text=True, check=True, timeout=60
    )
    return json.loads(done.stdout)


def _parse(path: str) -> ast.AST:
    return ast.parse(Path(path).read_text(encoding="utf-8"))


def test_decoder_us2_5_intake_and_worker_do_not_import_av() -> None:
    assert _run_stage_import()["av"] is False


def test_decoder_us2_5_no_module_loaded_by_intake_or_worker_can_import_av() -> None:
    files = _run_stage_import()["files"]
    importers: list[str] = []
    for name, path in files.items():
        if name == "stt.server.reader":
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "av" for alias in node.names):
                importers.append(name)
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "av":
                importers.append(name)
    assert importers == []


def test_decoder_us2_5_intake_and_worker_use_only_constants_of_the_reader_module() -> None:
    files = _run_stage_import()["files"]
    misused: list[str] = []
    for name in ("stt.server.intake", "stt.server.worker"):
        for node in ast.walk(_parse(files[name])):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "reader":
                if not node.attr.isupper():
                    misused.append(f"{name}: reader.{node.attr}")
            if isinstance(node, ast.ImportFrom) and node.module == "stt.server.reader":
                misused.extend(f"{name}: {alias.name}" for alias in node.names if not alias.name.isupper())
    assert misused == []


def test_decoder_us2_5_gpu_stage_runs_the_second_pass_without_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gpu_present: None, pinned_models: Path
) -> None:
    work_dir = tmp_path / "job"
    work_dir.mkdir()
    wav = make_wav(work_dir / "audio.wav", 12.0, silent=False)
    os.chmod(wav, 0o600)
    with wave.open(str(wav), "rb") as handle:
        ints = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    spec = {
        "request_id": REQUEST_ID,
        "original_name": "lecture one.m4a",
        "language": "en",
        "diarize": True,
        "hints": None,
        "duration_s": 12.0,
    }
    (work_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    no_tools = tmp_path / "empty-path"
    no_tools.mkdir()
    monkeypatch.setenv("PATH", str(no_tools))
    assert shutil.which("ffmpeg") is None

    def forbidden_to_wav(src: Path, dst: Path) -> Path:
        raise AssertionError("the GPU stage must not convert the recording with ffmpeg")

    model = FakeModel(PLAIN_SEGMENTS)
    diarized_wavs: list[Path] = []

    def fake_diarize_wav(pipeline: object, path: Path, *args: Any, **kwargs: Any) -> diarize.Diarization:
        diarized_wavs.append(path)
        return diarize.Diarization([diarize.Turn(0.0, 12.0, "SPEAKER_00")], {})

    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.setattr(transcribe, "load_model", lambda name, device: model)
    monkeypatch.setattr(diarize, "load_pipeline", lambda device: object())
    monkeypatch.setattr(diarize, "to_wav", forbidden_to_wav)
    monkeypatch.setattr(diarize, "diarize_wav", fake_diarize_wav)

    assert engine.main([str(work_dir)]) == 0

    outcome = json.loads((work_dir / "outcome.json").read_text(encoding="utf-8"))
    assert outcome == {"status": "done"}
    result = json.loads((work_dir / "out" / "result.json").read_text(encoding="utf-8"))
    assert result["summary"]["diarized"] is True
    assert {paragraph["speaker"] for paragraph in result["paragraphs"]} == {diarize.speaker_label("SPEAKER_00")}
    assert diarized_wavs == [work_dir / "audio.wav"]
    (call,) = model.calls
    assert isinstance(call["audio"], np.ndarray)
    assert call["audio"].dtype == np.float32
    assert call["audio"].shape == (12 * 16000,)
    assert np.array_equal(call["audio"], ints.astype(np.float32) / 32768.0)
    assert call["audio"].min() < -0.2
