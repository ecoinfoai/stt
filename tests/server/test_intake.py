"""Intake acceptance tests: records, queue markers, responses, ordering, logs and PyAV probing."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from stt.server import contract, intake
from stt.server.deadlines import next_purge
from stt.server.layout import Layout
from stt.server.records import load_audio, load_job, load_jobs

from .conftest import FakeClock, make_wav, new_id
from .inbox_helpers import inbox_names, make_layout, put_attachment, put_body, read_response, transcribe_body


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "source.wav", 2.0)


def submit(layout: Layout, wav: Path, **changes: object) -> str:
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, **changes))
    return request_id


def test_accept_writes_records_marker_response_and_copies_attachment(
    layout: Layout, wav: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    request_id = submit(layout, wav, hints=["term one", "term two"], diarize=True)

    intake.run_once(layout, clock.now)

    job = load_job(layout, request_id)
    assert job.state == "queued"
    assert job.seq == 1
    assert job.accepted_at == clock.now()
    assert job.audio_id == request_id
    assert job.requested_by == "professor-a"
    assert job.hints == ("term one", "term two")
    assert job.diarize is True
    assert job.duration_s == pytest.approx(2.0, abs=0.05)
    assert job.expires_at == next_purge(clock.now())
    audio = load_audio(layout, request_id)
    assert audio.delete_at == next_purge(clock.now())
    assert audio.users == (request_id,)
    assert audio.original_name == f"{request_id}.audio.wav"
    assert audio.owner == "professor-a"
    assert audio.received_at == clock.now()
    copy = layout.audio / request_id / "audio.wav"
    assert copy.read_bytes() == wav.read_bytes()
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert [path.name for path in layout.queue.iterdir()] == [f"00000001-{request_id}"]
    response_path = layout.outbox / f"{request_id}.json"
    assert stat.S_IMODE(response_path.stat().st_mode) == 0o640
    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["state"] == "queued"
    assert response["queue_position"] == 1
    assert inbox_names(layout) == []
    assert capfd.readouterr().out.splitlines() == [f"{request_id} queued"]


@pytest.mark.parametrize(
    ("accepted", "purge"),
    [("2026-03-02T14:57:59Z", "2026-03-02T14:58:00Z"), ("2026-03-02T14:58:00Z", "2026-03-03T14:58:00Z")],
    ids=["before_23_58_kst", "at_23_58_kst"],
)
def test_accept_sets_the_next_daily_purge_for_job_and_recording(
    layout: Layout, wav: Path, accepted: str, purge: str
) -> None:
    clock = FakeClock(accepted)
    request_id = submit(layout, wav)

    intake.run_once(layout, clock.now)

    response = read_response(layout, request_id)
    assert response["expires_at"] == purge
    assert response["audio"] == {"delete_at": purge}


def test_accepted_jobs_get_increasing_positions_in_mtime_order(layout: Layout, wav: Path) -> None:
    clock = FakeClock()
    ids = [submit(layout, wav) for _ in range(3)]
    base = 1_700_000_000
    for offset, request_id in zip((20, 0, 10), ids):
        os.utime(layout.inbox / f"{request_id}.json", (base + offset, base + offset))
    expected_order = [ids[1], ids[2], ids[0]]

    intake.run_once(layout, clock.now)

    assert [job.request_id for job in load_jobs(layout)] == expected_order
    assert [read_response(layout, request_id)["queue_position"] for request_id in expected_order] == [1, 2, 3]


def test_next_run_rewrites_earlier_queued_responses(layout: Layout, wav: Path) -> None:
    clock = FakeClock()
    first = submit(layout, wav)
    intake.run_once(layout, clock.now)
    clock.advance(5)
    second = submit(layout, wav)
    intake.run_once(layout, clock.now)

    assert read_response(layout, first)["queue_position"] == 1
    assert read_response(layout, first)["updated_at"] == "2026-03-02T01:00:05Z"
    assert read_response(layout, second)["queue_position"] == 2
    assert load_job(layout, second).seq == 2


def test_refusal_log_line_carries_only_id_and_state(layout: Layout, capfd: pytest.CaptureFixture[str]) -> None:
    request_id = new_id()
    (layout.inbox / f"{request_id}.json").write_bytes(b"{")

    intake.run_once(layout, FakeClock().now)

    assert capfd.readouterr().out.splitlines() == [f"{request_id} refused"]


def test_non_file_os_error_propagates_and_leaves_the_request(
    layout: Layout, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_id = submit(layout, wav)

    def too_many_files(path: Path) -> bytes:
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(intake, "read_request_bytes", too_many_files)

    with pytest.raises(OSError) as raised:
        intake.run_once(layout, FakeClock().now)

    assert raised.value.errno == errno.EMFILE
    assert (layout.inbox / f"{request_id}.json").exists()
    assert not (layout.outbox / f"{request_id}.json").exists()


def test_probe_failure_leaves_no_audio_copy_and_no_record(
    layout: Layout, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_id = submit(layout, wav)

    def broken(path: Path) -> float:
        raise RuntimeError("probe broke")

    monkeypatch.setattr(intake, "probe_duration", broken)

    with pytest.raises(RuntimeError):
        intake.run_once(layout, FakeClock().now)

    assert list(layout.audio.iterdir()) == []
    assert not (layout.jobs / request_id).exists()


def test_probe_duration_reads_container_duration(wav: Path) -> None:
    assert intake.probe_duration(wav) == pytest.approx(2.0, abs=0.01)


def test_probe_duration_sums_packet_durations_without_container_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import av

    source = make_wav(tmp_path / "no_duration.wav", 3.0)
    real_open = av.open

    class NoDuration:
        def __init__(self, container: object) -> None:
            self._container = container

        duration = None

        def __getattr__(self, name: str) -> object:
            return getattr(self._container, name)

        def __enter__(self) -> "NoDuration":
            return self

        def __exit__(self, *exc: object) -> None:
            self._container.close()

    monkeypatch.setattr(av, "open", lambda *a, **k: NoDuration(real_open(*a, **k)))

    assert intake.probe_duration(source) == pytest.approx(3.0, abs=0.05)
