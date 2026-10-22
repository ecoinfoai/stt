"""CLI tests for ``stt server``: usage errors, stage locks, exit codes and stage wiring."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest

from stt import cli
from stt.server import intake, worker
from stt.server.layout import Layout, try_hold

_HOLDER = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('ready', flush=True)\n"
    "sys.stdin.readline()\n"
)


@pytest.fixture
def models(tmp_path: Path) -> Path:
    folder = tmp_path / "models"
    folder.mkdir()
    return folder


def hold_in_subprocess(lock: Path) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    return holder


@pytest.fixture
def lock_holder() -> Iterator[Any]:
    started: list[subprocess.Popen[str]] = []

    def start(lock: Path) -> None:
        started.append(hold_in_subprocess(lock))

    yield start
    for holder in started:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)
        if holder.stdin:
            holder.stdin.close()
        if holder.stdout:
            holder.stdout.close()


def test_server_without_a_subcommand_prints_usage_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["server"]) == 2

    captured = capsys.readouterr()
    assert "usage: stt server" in captured.err
    assert "intake" in captured.err and "work" in captured.err


def test_server_with_an_unknown_subcommand_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["server", "bogus"]) == 2

    assert "bogus" in capsys.readouterr().err


def test_server_with_an_unknown_option_exits_2(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["server", "intake", "--root", str(root), "--bogus"]) == 2

    assert "--bogus" in capsys.readouterr().err


def test_top_level_help_lists_the_server_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0

    assert "server" in capsys.readouterr().out


def test_server_help_exits_0_with_english_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["server", "work", "--help"]) == 0

    out = capsys.readouterr().out
    assert "--root" in out and "--models" in out
    assert out.isascii()


def test_intake_exits_75_while_another_process_holds_intake_lock(
    root: Path, lock_holder: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_holder(root / "intake.lock")

    assert cli.main(["server", "intake", "--root", str(root)]) == 75

    err = capsys.readouterr().err
    assert err.strip() == "Another intake run is in progress."


def test_work_exits_75_while_another_process_holds_worker_lock(
    root: Path, models: Path, lock_holder: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_holder(root / "worker.lock")

    assert cli.main(["server", "work", "--root", str(root), "--models", str(models)]) == 75

    err = capsys.readouterr().err
    assert err.strip() == "Another worker run is in progress."


def test_sweep_exits_75_while_another_process_holds_sweep_lock(
    root: Path, lock_holder: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_holder(root / "sweep.lock")

    assert cli.main(["server", "sweep", "--root", str(root)]) == 75

    err = capsys.readouterr().err
    assert err.strip() == "Another sweep run is in progress."


def test_sweep_exits_0_on_an_empty_root_and_releases_its_lock(root: Path) -> None:
    for _ in range(2):
        assert cli.main(["server", "sweep", "--root", str(root)]) == 0

    context = try_hold(root / "sweep.lock")
    assert context is not None
    with context:
        pass


def test_sweep_is_not_blocked_by_the_intake_or_worker_locks(root: Path, lock_holder: Any) -> None:
    lock_holder(root / "intake.lock")
    lock_holder(root / "worker.lock")

    assert cli.main(["server", "sweep", "--root", str(root)]) == 0


def test_sweep_runs_the_stage_under_its_lock_with_a_utc_clock(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run_once(layout: Layout, now: Any) -> None:
        seen["root"] = layout.root
        seen["offset"] = now().utcoffset()
        seen["lock"] = try_hold(layout.sweep_lock)

    monkeypatch.setattr("stt.server.sweep.run_once", fake_run_once)

    assert cli.main(["server", "sweep", "--root", str(root)]) == 0

    assert seen["root"] == root
    assert seen["offset"] == timedelta(0)
    assert seen["lock"] is None


def test_sweep_help_exits_0_with_english_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["server", "sweep", "--help"]) == 0

    out = capsys.readouterr().out
    assert "--root" in out and "--models" not in out
    assert out.isascii()


def test_intake_is_not_blocked_by_the_worker_lock(root: Path, lock_holder: Any) -> None:
    lock_holder(root / "worker.lock")

    assert cli.main(["server", "intake", "--root", str(root)]) == 0


def test_both_stages_exit_0_on_an_empty_root_and_release_their_locks(root: Path, models: Path) -> None:
    for _ in range(2):
        assert cli.main(["server", "intake", "--root", str(root)]) == 0
        assert cli.main(["server", "work", "--root", str(root), "--models", str(models)]) == 0

    for lock in (root / "intake.lock", root / "worker.lock"):
        context = try_hold(lock)
        assert context is not None
        with context:
            pass


def test_intake_creates_the_layout_before_taking_the_lock(tmp_path: Path) -> None:
    state_root = tmp_path / "fresh"
    state_root.mkdir()

    assert cli.main(["server", "intake", "--root", str(state_root)]) == 0

    assert (state_root / "requests" / "inbox").is_dir()
    assert (state_root / "intake.lock").is_file()


def test_intake_runs_the_stage_under_its_lock_with_a_utc_clock(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_run_once(layout: Layout, now: Any) -> None:
        seen["root"] = layout.root
        seen["offset"] = now().utcoffset()
        seen["lock"] = try_hold(layout.intake_lock)

    monkeypatch.setattr(intake, "run_once", fake_run_once)

    assert cli.main(["server", "intake", "--root", str(root)]) == 0

    assert seen["root"] == root
    assert seen["offset"] == timedelta(0)
    assert seen["lock"] is None


def test_work_passes_the_engine_models_and_clock_to_the_worker_under_its_lock(
    root: Path, models: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(layout: Layout, engine_cmd: Any, now: Any, *, poll_s: float = 2.0, models_dir: Path) -> int:
        seen.update(root=layout.root, engine=tuple(engine_cmd), models=models_dir)
        seen["clock"] = now()
        seen["lock"] = try_hold(layout.worker_lock)
        return 0

    monkeypatch.setattr(worker, "run", fake_run)

    assert cli.main(["server", "work", "--root", str(root), "--models", str(models)]) == 0

    assert seen["root"] == root
    assert seen["engine"] == worker.DEFAULT_ENGINE_CMD
    assert seen["models"] == models
    assert isinstance(seen["clock"], datetime) and seen["clock"].utcoffset() == timedelta(0)
    assert seen["lock"] is None


def test_the_stage_lock_is_released_when_the_stage_raises(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(layout: Layout, now: Any) -> None:
        raise RuntimeError("stage failed")

    monkeypatch.setattr(intake, "run_once", failing)

    with pytest.raises(RuntimeError):
        cli.main(["server", "intake", "--root", str(root)])

    context = try_hold(root / "intake.lock")
    assert context is not None
    with context:
        pass


def test_defaults_are_the_production_state_and_model_folders() -> None:
    from stt.server import cli as server_cli

    parser = server_cli.build_parser()

    intake_args = parser.parse_args(["intake"])
    work_args = parser.parse_args(["work"])
    sweep_args = parser.parse_args(["sweep"])
    assert intake_args.root == Path("/var/lib/stt")
    assert work_args.root == Path("/var/lib/stt")
    assert sweep_args.root == Path("/var/lib/stt")
    assert work_args.models == Path("/var/lib/stt-models")
