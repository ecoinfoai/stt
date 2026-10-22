"""CLI GPU lock tests: `STT_GPU_LOCK` makes transcribe, batch and enroll queue behind the server."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

from stt import batch, diarize, enroll, gpu_lock, transcribe
from tests.test_cli_integration import FakeModel

HOLD_SECONDS = 1.0
HOLDER_SCRIPT = (
    "import fcntl, os, sys, time\n"
    "fd = os.open(sys.argv[1], os.O_RDONLY)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('ready', flush=True)\n"
    f"time.sleep({HOLD_SECONDS})\n"
    "print(time.monotonic(), flush=True)\n"
    "os.close(fd)\n"
)


class Holder:
    """Another process that holds the lock for about a second."""

    def __init__(self, path: Path) -> None:
        self._process = subprocess.Popen(
            [sys.executable, "-c", HOLDER_SCRIPT, str(path)],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self._process.stdout is not None
        assert self._process.stdout.readline().strip() == "ready"

    def released_at(self) -> float:
        """Monotonic time the holder gave the lock up (waits for the holder to exit)."""
        assert self._process.stdout is not None
        released = float(self._process.stdout.readline())
        self._process.wait(timeout=10)
        return released


@pytest.fixture
def lock_file(tmp_path: Path) -> Path:
    path = tmp_path / "gpu.lock"
    path.touch()
    return path


@pytest.fixture
def holder(lock_file: Path) -> Iterator[Holder]:
    yield Holder(lock_file)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    path = tmp_path / "talk.m4a"
    path.touch()
    return path


class LoadRecorder:
    """Fake `load_model` that records when it ran and whether the GPU lock was held."""

    def __init__(self, lock_file: Path | None = None) -> None:
        self.times: list[float] = []
        self.lock_busy_during_load: list[bool] = []
        self._lock_file = lock_file

    def __call__(self, name: str, device: str) -> FakeModel:
        self.times.append(time.monotonic())
        if self._lock_file is not None:
            self.lock_busy_during_load.append(gpu_lock.try_acquire(self._lock_file) is None)
        return FakeModel()


def test_us4_1_cli_waits_for_server_job(
    lock_file: Path, holder: Holder, media: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    recorder = LoadRecorder(lock_file)
    monkeypatch.setattr(transcribe, "load_model", recorder)

    code = transcribe.main([str(media), "--device", "cuda"])

    assert code == 0
    assert capsys.readouterr().err.count(gpu_lock.WAIT_MESSAGE) == 1
    assert recorder.times[0] >= holder.released_at()
    assert recorder.lock_busy_during_load == [True]
    free = gpu_lock.try_acquire(lock_file)
    assert free is not None
    with free:
        pass


def test_us4_1_cli_does_not_announce_waiting_when_free(
    lock_file: Path, media: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    monkeypatch.setattr(transcribe, "load_model", LoadRecorder())

    assert transcribe.main([str(media), "--device", "cuda"]) == 0
    assert gpu_lock.WAIT_MESSAGE not in capsys.readouterr().err


def test_us4_1_cli_takes_lock_before_loading_speaker_pipeline(
    lock_file: Path, holder: Holder, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    pipeline_times: list[float] = []

    def fake_load_pipeline(device: str) -> object:
        pipeline_times.append(time.monotonic())
        return object()

    monkeypatch.setattr(diarize, "load_pipeline", fake_load_pipeline)
    monkeypatch.setattr(transcribe, "run_with_fallback", lambda *args, **kwargs: None)

    assert transcribe.main([str(media), "--device", "cuda", "--diarize"]) == 0
    assert pipeline_times[0] >= holder.released_at()


def test_us4_1_enroll_waits_for_server_job(
    lock_file: Path,
    holder: Holder,
    media: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    enroll_map = tmp_path / "enroll.yaml"
    enroll_map.write_text(
        f"- media: {media}\n  speakers:\n    SPEAKER_00: Alice\n", encoding="utf-8"
    )
    pipeline_times: list[float] = []
    busy_during_enroll: list[bool] = []

    def fake_load_pipeline(device: str) -> object:
        pipeline_times.append(time.monotonic())
        return object()

    def fake_diarize_embeddings(pipeline: object, wav: Path) -> tuple[list[str], list[list[float]]]:
        busy_during_enroll.append(gpu_lock.try_acquire(lock_file) is None)
        return ["SPEAKER_00"], [[1.0, 0.0]]

    monkeypatch.setattr(diarize, "load_pipeline", fake_load_pipeline)
    monkeypatch.setattr(diarize, "to_wav", lambda src, dst: dst)
    monkeypatch.setattr(diarize, "diarize_embeddings", fake_diarize_embeddings)

    code = enroll.main(
        ["--map", str(enroll_map), "--db", str(tmp_path / "voices.json"), "--device", "cuda"]
    )

    assert code == 0
    assert capsys.readouterr().err.count(gpu_lock.WAIT_MESSAGE) == 1
    assert pipeline_times[0] >= holder.released_at()
    assert busy_during_enroll == [True]
    free = gpu_lock.try_acquire(lock_file)
    assert free is not None
    with free:
        pass


def test_us4_4_cli_unchanged_without_lock(
    tmp_path: Path, media: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(transcribe, "load_model", lambda name, device: FakeModel())
    monkeypatch.delenv("STT_GPU_LOCK", raising=False)
    assert transcribe.main([str(media), "--device", "cpu"]) == 0
    plain = capsys.readouterr()
    plain_text = (tmp_path / "talk.txt").read_text(encoding="utf-8")
    (tmp_path / "talk.txt").unlink()

    missing = tmp_path / "absent.lock"
    monkeypatch.setenv("STT_GPU_LOCK", str(missing))
    assert transcribe.main([str(media), "--device", "cpu"]) == 0
    cpu = capsys.readouterr()

    assert cpu.out == plain.out
    assert cpu.err == plain.err == ""
    assert (tmp_path / "talk.txt").read_text(encoding="utf-8") == plain_text


def test_empty_gpu_lock_variable_means_no_lock(
    media: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(transcribe, "load_model", lambda name, device: FakeModel())
    monkeypatch.setenv("STT_GPU_LOCK", "")

    assert transcribe.main([str(media), "--device", "cuda"]) == 0
    assert capsys.readouterr().err == ""


def test_cli_runs_without_fcntl(
    media: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.delenv("STT_GPU_LOCK", raising=False)
    monkeypatch.setattr(transcribe, "load_model", lambda name, device: FakeModel())

    assert transcribe.main([str(media), "--device", "cuda"]) == 0
    assert capsys.readouterr().err == ""


def test_us4_4_cuda_with_missing_lock_file_stops_with_exit_2(
    tmp_path: Path, media: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    missing = tmp_path / "absent.lock"
    monkeypatch.setenv("STT_GPU_LOCK", str(missing))
    recorder = LoadRecorder()
    monkeypatch.setattr(transcribe, "load_model", recorder)

    with pytest.raises(SystemExit) as stopped:
        transcribe.main([str(media), "--device", "cuda"])

    assert stopped.value.code == 2
    assert capsys.readouterr().err.strip() == f"STT_GPU_LOCK points to a missing file: {missing}"
    assert recorder.times == []


def test_us4_4_batch_keep_going_does_not_continue_after_missing_lock_file(
    tmp_path: Path, media: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    missing = tmp_path / "absent.lock"
    monkeypatch.setenv("STT_GPU_LOCK", str(missing))
    recorder = LoadRecorder()
    monkeypatch.setattr(transcribe, "load_model", recorder)
    second = tmp_path / "other.m4a"
    second.touch()
    options = {**batch.OPTION_DEFAULTS, "device": "cuda"}
    groups = [(options, [media]), ({**options, "beam": 1}, [second])]

    with pytest.raises(SystemExit) as stopped:
        batch.run_groups(groups, None, False, keep_going=True)

    assert stopped.value.code == 2
    assert capsys.readouterr().err.count("points to a missing file") == 1
    assert recorder.times == []
