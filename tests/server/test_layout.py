"""Layout tests: state-root paths, names, atomic writes, flock helpers, safe inbox opening."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from stt.server import layout as layout_module
from stt.server.layout import (
    Layout,
    UnsafeFileError,
    atomic_write_text,
    attachment_id,
    hold,
    is_request_name,
    open_attachment,
    read_request_bytes,
    try_hold,
)

REQUEST_ID = "0b9a8c2e-5f1d-4c3a-9e7b-2d6f4a1c8e30"
NOT_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_layout_paths_follow_the_state_root(root: Path) -> None:
    layout = Layout(root)

    assert layout.root == root
    assert layout.inbox == root / "requests" / "inbox"
    assert layout.outbox == root / "requests" / "outbox"
    assert layout.jobs == root / "jobs"
    assert layout.audio == root / "audio"
    assert layout.work == root / "work"
    assert layout.queue == root / "queue"
    assert layout.state_lock == root / "state.lock"
    assert layout.worker_lock == root / "worker.lock"
    assert layout.intake_lock == root / "intake.lock"
    assert layout.sweep_lock == root / "sweep.lock"


def test_ensure_layout_creates_missing_internal_folders_as_0700(root: Path) -> None:
    layout = Layout(root)

    layout.ensure_layout()

    for folder in (layout.jobs, layout.audio, layout.work, layout.queue):
        assert folder.is_dir()
        assert mode_of(folder) == 0o700
    assert layout.inbox.is_dir()
    assert layout.outbox.is_dir()


def test_ensure_layout_keeps_the_mode_of_existing_folders(root: Path) -> None:
    layout = Layout(root)
    layout.jobs.mkdir(mode=0o755)
    os.chmod(layout.jobs, 0o755)
    layout.inbox.mkdir(parents=True)
    os.chmod(layout.inbox, 0o2770)

    layout.ensure_layout()
    layout.ensure_layout()

    assert mode_of(layout.jobs) == 0o755
    assert mode_of(layout.inbox) == 0o2770


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (f"{REQUEST_ID}.json", True),
        (f"{REQUEST_ID}.json.tmp", False),
        (f"{REQUEST_ID.upper()}.json", False),
        (f"{REQUEST_ID}.json\n", False),
        (f"{REQUEST_ID}.audio.m4a", False),
        (f".{REQUEST_ID}.json", False),
        ("notes.json", False),
        ("", False),
    ],
)
def test_is_request_name_accepts_only_lowercase_uuid_json(name: str, expected: bool) -> None:
    assert is_request_name(name) is expected


@pytest.mark.parametrize("ext", ["m4a", "mp3", "wav", "flac", "ogg", "opus", "aac", "wma"])
def test_attachment_id_returns_the_request_id_for_each_extension(ext: str) -> None:
    assert attachment_id(f"{REQUEST_ID}.audio.{ext}") == REQUEST_ID


@pytest.mark.parametrize(
    "name",
    [
        f"{REQUEST_ID}.audio.mp4",
        f"{REQUEST_ID}.audio.M4A",
        f"{REQUEST_ID.upper()}.audio.m4a",
        f"{REQUEST_ID}.audio.m4a.tmp",
        f"{REQUEST_ID}.json",
        f"x{REQUEST_ID}.audio.m4a",
        "audio.m4a",
    ],
)
def test_attachment_id_returns_none_for_other_names(name: str) -> None:
    assert attachment_id(name) is None


def test_atomic_write_text_sets_the_exact_mode_and_content(tmp_path: Path) -> None:
    target = tmp_path / "out.json"

    atomic_write_text(target, "hello\n")

    assert target.read_text(encoding="utf-8") == "hello\n"
    assert mode_of(target) == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json"]


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o660])
def test_atomic_write_text_mode_is_exact_despite_umask(tmp_path: Path, mode: int) -> None:
    old = os.umask(0o077)
    try:
        atomic_write_text(tmp_path / "f.json", "x", mode=mode)
    finally:
        os.umask(old)

    assert mode_of(tmp_path / "f.json") == mode


def test_atomic_write_text_replaces_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    target.write_text("old", encoding="utf-8")

    atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"


def test_atomic_write_text_uses_a_hidden_tmp_name_in_the_same_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real_replace = os.replace

    def spy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        seen.append(Path(src).name)
        assert Path(src).parent == tmp_path
        real_replace(src, dst)

    monkeypatch.setattr(layout_module.os, "replace", spy)

    atomic_write_text(tmp_path / "out.json", "x")

    assert len(seen) == 1
    assert seen[0].startswith(".out.json.")
    assert seen[0].endswith(".tmp")


def test_atomic_write_text_leaves_no_temp_file_when_the_rename_fails(tmp_path: Path) -> None:
    target = tmp_path / "taken"
    target.mkdir()

    with pytest.raises(OSError):
        atomic_write_text(target, "x")

    assert [p.name for p in tmp_path.iterdir()] == ["taken"]


def test_atomic_write_text_leaves_no_temp_file_when_writing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(layout_module.os, "fsync", boom)

    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(tmp_path / "out.json", "x")

    assert list(tmp_path.iterdir()) == []


def _flock_probe(path: Path) -> str:
    code = (
        "import fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "    print('busy')\n"
        "else:\n"
        "    print('free')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def test_hold_is_exclusive_while_held_and_creates_the_lock_file(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"

    with hold(lock):
        assert lock.is_file()
        assert _flock_probe(lock) == "busy"

    assert _flock_probe(lock) == "free"


def test_hold_releases_the_lock_when_the_body_raises(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"

    with pytest.raises(RuntimeError):
        with hold(lock):
            raise RuntimeError("body failed")

    assert _flock_probe(lock) == "free"


def test_try_hold_returns_none_when_another_process_holds_the_lock(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    code = (
        "import fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('ready', flush=True)\n"
        "sys.stdin.readline()\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", code, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        assert try_hold(lock) is None
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(timeout=10)

    guard = try_hold(lock)
    assert guard is not None
    with guard:
        assert _flock_probe(lock) == "busy"
    assert _flock_probe(lock) == "free"


def test_try_hold_returns_none_for_a_second_holder_in_the_same_process(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    first = try_hold(lock)
    assert first is not None

    with first:
        assert try_hold(lock) is None


def test_lock_descriptors_are_not_inherited_by_child_processes(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    code = (
        "import os, sys\n"
        "target = os.path.realpath(sys.argv[1])\n"
        "links = [os.path.realpath('/proc/self/fd/' + n) for n in os.listdir('/proc/self/fd')]\n"
        "print(target in links)\n"
    )
    with hold(lock):
        done = subprocess.run(
            [sys.executable, "-c", code, str(lock)],
            capture_output=True,
            text=True,
            check=True,
            close_fds=False,
        )

    assert done.stdout.strip() == "False"


def test_read_request_bytes_reads_a_regular_file(tmp_path: Path) -> None:
    path = tmp_path / f"{REQUEST_ID}.json"
    path.write_bytes(b'{"a": 1}')

    assert read_request_bytes(path) == b'{"a": 1}'


def test_read_request_bytes_accepts_exactly_the_limit_and_rejects_one_more(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_bytes(b"x" * 1024)
    assert read_request_bytes(path, limit=1024) == b"x" * 1024

    path.write_bytes(b"x" * 1025)
    with pytest.raises(UnsafeFileError) as caught:
        read_request_bytes(path, limit=1024)
    assert caught.value.reason == "too_large"


def test_read_request_bytes_default_limit_is_one_mebibyte(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_bytes(b"x" * (1024 * 1024))
    assert len(read_request_bytes(path)) == 1024 * 1024

    path.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(UnsafeFileError) as caught:
        read_request_bytes(path)
    assert caught.value.reason == "too_large"


def _make_symlink(tmp_path: Path) -> Path:
    target = tmp_path / "target.bin"
    target.write_bytes(b"secret")
    link = tmp_path / "link.bin"
    link.symlink_to(target)
    return link


def _make_dangling_symlink(tmp_path: Path) -> Path:
    link = tmp_path / "dangling.bin"
    link.symlink_to(tmp_path / "missing.bin")
    return link


def _make_hardlink(tmp_path: Path) -> Path:
    first = tmp_path / "first.bin"
    first.write_bytes(b"secret")
    second = tmp_path / "second.bin"
    os.link(first, second)
    return second


def _make_fifo(tmp_path: Path) -> Path:
    fifo = tmp_path / "pipe.bin"
    os.mkfifo(fifo)
    return fifo


def _make_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "folder.bin"
    folder.mkdir()
    return folder


UNSAFE_CASES = [
    pytest.param(_make_symlink, "symlink", id="symlink"),
    pytest.param(_make_dangling_symlink, "symlink", id="dangling-symlink"),
    pytest.param(_make_hardlink, "hardlink", id="hardlink"),
    pytest.param(_make_fifo, "not_regular", id="fifo"),
    pytest.param(_make_folder, "not_regular", id="folder"),
]


@pytest.mark.parametrize(("make", "reason"), UNSAFE_CASES)
def test_read_request_bytes_refuses_unsafe_files(tmp_path: Path, make, reason: str) -> None:
    path = make(tmp_path)

    with pytest.raises(UnsafeFileError) as caught:
        read_request_bytes(path)

    assert caught.value.reason == reason
    assert str(path) not in str(caught.value)
    assert path.name not in str(caught.value)


@pytest.mark.parametrize(("make", "reason"), UNSAFE_CASES)
def test_open_attachment_refuses_unsafe_files(tmp_path: Path, make, reason: str) -> None:
    path = make(tmp_path)

    with pytest.raises(UnsafeFileError) as caught:
        open_attachment(path)

    assert caught.value.reason == reason
    assert str(path) not in str(caught.value)
    assert path.name not in str(caught.value)


@NOT_ROOT
def test_read_request_bytes_refuses_an_unreadable_file(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_bytes(b"{}")
    os.chmod(path, 0)

    with pytest.raises(UnsafeFileError) as caught:
        read_request_bytes(path)

    assert caught.value.reason == "unreadable"
    assert str(path) not in str(caught.value)


@NOT_ROOT
def test_open_attachment_refuses_an_unreadable_file(tmp_path: Path) -> None:
    path = tmp_path / "a.m4a"
    path.write_bytes(b"audio")
    os.chmod(path, 0)

    with pytest.raises(UnsafeFileError) as caught:
        open_attachment(path)

    assert caught.value.reason == "unreadable"


def test_missing_files_are_reported_as_unreadable(tmp_path: Path) -> None:
    with pytest.raises(UnsafeFileError) as caught:
        open_attachment(tmp_path / "gone.m4a")

    assert caught.value.reason == "unreadable"


def test_open_attachment_returns_a_binary_handle_with_the_content(tmp_path: Path) -> None:
    path = tmp_path / f"{uuid.uuid4()}.audio.wav"
    path.write_bytes(b"RIFFdata")

    with open_attachment(path) as handle:
        assert handle.read() == b"RIFFdata"


def test_open_attachment_has_no_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    path.write_bytes(b"x" * (2 * 1024 * 1024))

    with open_attachment(path) as handle:
        assert len(handle.read()) == 2 * 1024 * 1024
