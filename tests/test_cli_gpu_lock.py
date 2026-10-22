"""CLI GPU lock tests: `STT_GPU_LOCK` makes transcribe, batch and enroll queue behind the server."""

from __future__ import annotations

import subprocess
import sys
import time
import weakref
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


@pytest.fixture(autouse=True)
def one_cuda_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes the GPU-only device check see one CUDA device."""
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)


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
    tmp_path: Path,
    media: Path,
    lock_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    monkeypatch.setattr(transcribe, "load_model", lambda name, device: FakeModel())
    monkeypatch.delenv("STT_GPU_LOCK", raising=False)
    assert transcribe.main([str(media), "--device", "cuda"]) == 0
    plain = capsys.readouterr()
    plain_text = (tmp_path / "talk.txt").read_text(encoding="utf-8")
    (tmp_path / "talk.txt").unlink()

    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    assert transcribe.main([str(media), "--device", "cuda"]) == 0
    locked = capsys.readouterr()

    assert locked.out == plain.out
    assert locked.err == plain.err == ""
    assert (tmp_path / "talk.txt").read_text(encoding="utf-8") == plain_text


def test_us4_1_lock_is_taken_with_device_auto(
    media: Path,
    lock_file: Path,
    holder: Holder,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    recorder = LoadRecorder(lock_file)
    monkeypatch.setattr(transcribe, "load_model", recorder)

    assert transcribe.main([str(media), "--device", "auto"]) == 0

    assert capsys.readouterr().err.count(gpu_lock.WAIT_MESSAGE) == 1
    assert recorder.times[0] >= holder.released_at()
    assert recorder.lock_busy_during_load == [True]


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


class FakeTorch:
    """Stand-in for the `torch` module that records the state at `empty_cache` time."""

    def __init__(self, lock_file: Path, pipeline_ref: list[Any]) -> None:
        self.cuda = self
        self.empty_cache_calls = 0
        self.lock_busy: list[bool] = []
        self.pipeline_gone: list[bool] = []
        self._lock_file = lock_file
        self._pipeline_ref = pipeline_ref

    def is_available(self) -> bool:
        return True

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1
        self.lock_busy.append(gpu_lock.try_acquire(self._lock_file) is None)
        self.pipeline_gone.append(all(ref() is None for ref in self._pipeline_ref))


class FakePipeline:
    """Weak-referenceable stand-in for the diarization pipeline."""


def _track_pipeline(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    refs: list[Any] = []

    def fake_load_pipeline(device: str) -> FakePipeline:
        pipeline = FakePipeline()
        refs.append(weakref.ref(pipeline))
        return pipeline

    monkeypatch.setattr(diarize, "load_pipeline", fake_load_pipeline)
    return refs


def test_us4_cli_returns_cached_gpu_memory_before_releasing_lock(
    lock_file: Path, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    refs = _track_pipeline(monkeypatch)
    fake_torch = FakeTorch(lock_file, refs)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(transcribe, "run_with_fallback", lambda *args, **kwargs: None)

    assert transcribe.main([str(media), "--device", "cuda", "--diarize"]) == 0

    assert fake_torch.empty_cache_calls == 1
    assert fake_torch.lock_busy == [True]
    assert fake_torch.pipeline_gone == [True]


def test_us4_enroll_returns_cached_gpu_memory_before_releasing_lock(
    lock_file: Path, media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    refs = _track_pipeline(monkeypatch)
    fake_torch = FakeTorch(lock_file, refs)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    enroll_map = tmp_path / "enroll.yaml"
    enroll_map.write_text(
        f"- media: {media}\n  speakers:\n    SPEAKER_00: Alice\n", encoding="utf-8"
    )
    monkeypatch.setattr(diarize, "to_wav", lambda src, dst: dst)
    monkeypatch.setattr(
        diarize, "diarize_embeddings", lambda pipeline, wav: (["SPEAKER_00"], [[1.0, 0.0]])
    )

    code = enroll.main(
        ["--map", str(enroll_map), "--db", str(tmp_path / "voices.json"), "--device", "cuda"]
    )

    assert code == 0
    assert fake_torch.empty_cache_calls == 1
    assert fake_torch.lock_busy == [True]
    assert fake_torch.pipeline_gone == [True]


def test_us4_cli_does_not_touch_torch_without_lock(
    lock_file: Path, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STT_GPU_LOCK", raising=False)
    fake_torch = FakeTorch(lock_file, [])
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(transcribe, "load_model", lambda name, device: FakeModel())

    assert transcribe.main([str(media), "--device", "cuda"]) == 0
    assert fake_torch.empty_cache_calls == 0


def test_us4_cli_does_not_import_torch_to_release_memory(
    lock_file: Path, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STT_GPU_LOCK", str(lock_file))
    monkeypatch.setattr(transcribe, "load_model", lambda name, device: FakeModel())
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    assert transcribe.main([str(media), "--device", "cuda"]) == 0
    assert "torch" not in sys.modules


_WITHOUT_FCNTL = "import sys\nsys.modules['fcntl'] = None\n"


@pytest.mark.parametrize(
    "script",
    [
        _WITHOUT_FCNTL + "import stt.cli\nraise SystemExit(stt.cli.main(['--help']))\n",
        _WITHOUT_FCNTL
        + "import runpy\nsys.argv = ['stt', 'transcribe', '--help']\nrunpy.run_module('stt', run_name='__main__')\n",
    ],
    ids=["stt-help", "python-m-stt-transcribe-help"],
)
def test_cli_imports_and_shows_help_without_fcntl(script: str) -> None:
    finished = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)

    assert finished.returncode == 0, finished.stderr
    assert "transcribe" in finished.stdout
