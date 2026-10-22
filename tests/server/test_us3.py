"""US3: a second pass reuses the first job's recording without a new upload."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from stt.server import contract, intake
from stt.server.layout import Layout
from stt.server.records import load_audio, load_job

from .conftest import FAKE_ENGINE, FakeClock, leftovers, make_wav, new_id, run_sweep, run_worker, write_delete_request
from .inbox_helpers import make_layout, put_attachment, put_body, read_response, transcribe_body

SOURCE_UNAVAILABLE = "The source recording is not available."
TIME_KEYS = ("finished_at", "updated_at", "expires_at")
AFTER_PURGE = "2026-03-02T14:58:30Z"


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "source.wav", 2.0)


def submit(layout: Layout, wav: Path, clock: FakeClock, **changes: Any) -> str:
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, **changes))
    intake.run_once(layout, clock.now)
    return request_id


def submit_second(layout: Layout, clock: FakeClock, source: str, **changes: Any) -> str:
    request_id = new_id()
    changes.setdefault("audio", {"from_request": source})
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, **changes))
    intake.run_once(layout, clock.now)
    return request_id


def assert_valid_response(layout: Layout, request_id: str) -> dict[str, Any]:
    doc = read_response(layout, request_id)
    assert contract.validate_response(doc) is None
    return doc


def assert_source_unavailable(layout: Layout, request_id: str) -> dict[str, Any]:
    doc = assert_valid_response(layout, request_id)
    assert doc["state"] == "failed"
    assert "accepted_at" not in doc
    assert doc["failure"] == {"reason": "source_unavailable", "detail": SOURCE_UNAVAILABLE}
    assert not (layout.jobs / request_id).exists()
    return doc


def spec_copying_engine(tmp_path: Path, destination: Path) -> list[str]:
    """Returns an engine command that saves the spec it was given, then behaves like the fake engine."""
    script = tmp_path / "spec_copy_engine.py"
    script.write_text(
        "import json, shutil, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(FAKE_ENGINE.parent)!r})\n"
        "import fake_engine\n"
        "work = Path(sys.argv[-1])\n"
        "spec = json.loads((work / 'spec.json').read_text())\n"
        "spec['audio_file_existed'] = Path(spec['audio_path']).is_file()\n"
        f"Path({str(destination)!r}).write_text(json.dumps(spec))\n"
        "sys.exit(fake_engine.main(['--mode', 'done', str(work)]))\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_us3_1_second_pass_reuses_first_audio(layout: Layout, wav: Path, clock: FakeClock, tmp_path: Path) -> None:
    first = submit(layout, wav, clock)
    run_worker(layout, clock)
    assert (layout.outbox / first / "transcript.txt").read_bytes()
    clock.advance(60)
    second = submit_second(layout, clock, first, hints=["alpha"], diarize=True)
    seen = tmp_path / "spec.json"

    run_worker(layout, clock, engine_cmd=spec_copying_engine(tmp_path, seen))

    doc = assert_valid_response(layout, second)
    assert doc["state"] == "done"
    assert (layout.outbox / second / "transcript.txt").is_file()
    spec = json.loads(seen.read_text(encoding="utf-8"))
    assert spec["request_id"] == second
    assert spec["audio_path"] == str(layout.audio / first / "audio.wav")
    assert spec["original_name"] == f"{first}.audio.wav"
    assert spec["audio_file_existed"] is True
    assert spec["diarize"] is True
    assert not (layout.audio / second).exists()
    job = load_job(layout, second)
    assert job.audio_id == first
    assert job.duration_s == load_job(layout, first).duration_s
    audio = load_audio(layout, first)
    assert audio.users == (first, second)
    assert audio.deleted_at is None
    assert doc["audio"] == assert_valid_response(layout, first)["audio"] == {"delete_at": "2026-03-02T14:58:00Z"}
    assert doc["expires_at"] == "2026-03-02T14:58:00Z"


def test_first_and_second_pass_ending_keep_the_shared_recording(layout: Layout, wav: Path, clock: FakeClock) -> None:
    first = submit(layout, wav, clock)
    second = submit_second(layout, clock, first)

    assert run_worker(layout, clock) == 0

    assert assert_valid_response(layout, first)["state"] == "done"
    assert assert_valid_response(layout, second)["state"] == "done"
    assert (layout.audio / first / "audio.wav").is_file()
    assert load_audio(layout, first).deleted_at is None


@pytest.mark.parametrize("deleted_by", ["request", "purge"])
def test_us3_2_deleted_source_refused(
    layout: Layout, wav: Path, clock: FakeClock, capfd: pytest.CaptureFixture[str], deleted_by: str
) -> None:
    first = submit(layout, wav, clock)
    run_worker(layout, clock)
    if deleted_by == "request":
        write_delete_request(layout.inbox, request_id=new_id(), requested_by="professor-a", target=first)
        intake.run_once(layout, clock.now)
    else:
        clock.set(AFTER_PURGE)
        run_sweep(layout, clock)
    assert not (layout.audio / first).exists()
    capfd.readouterr()

    second = submit_second(layout, clock, first)

    assert_source_unavailable(layout, second)
    assert capfd.readouterr().out.splitlines() == [f"{second} failed source_unavailable"]
    assert list(layout.inbox.iterdir()) == []
    assert leftovers(layout.root) == []


def test_us3_3_other_requester_source_refused_without_disclosure(layout: Layout, wav: Path, clock: FakeClock) -> None:
    first = submit(layout, wav, clock, requested_by="professor-a")
    run_worker(layout, clock)
    clock.advance(60)

    foreign = submit_second(layout, clock, first, requested_by="professor-b")
    missing = submit_second(layout, clock, new_id(), requested_by="professor-b")

    shown = {name: assert_source_unavailable(layout, name) for name in (foreign, missing)}
    for doc, request_id in ((shown[foreign], foreign), (shown[missing], missing)):
        assert doc.pop("request_id") == request_id
        for key in TIME_KEYS:
            doc.pop(key)
    assert shown[foreign] == shown[missing]
    audio = load_audio(layout, first)
    assert audio.users == (first,)
    assert (layout.audio / first / "audio.wav").is_file()


def test_us3_4_unknown_source_refused(layout: Layout, clock: FakeClock) -> None:
    second = submit_second(layout, clock, new_id())

    assert_source_unavailable(layout, second)
    assert list(layout.inbox.iterdir()) == []


def test_unsupported_language_is_reported_before_the_source_is_checked(layout: Layout, clock: FakeClock) -> None:
    second = submit_second(layout, clock, new_id(), language="xx")

    doc = assert_valid_response(layout, second)
    assert doc["failure"]["reason"] == "invalid_request"
    assert doc["failure"]["detail"].startswith("language:")


def test_delete_request_on_the_first_pass_deletes_a_waiting_second_pass(
    layout: Layout, wav: Path, clock: FakeClock
) -> None:
    first = submit(layout, wav, clock)
    second = submit_second(layout, clock, first, hints=["alpha"])
    clock.advance(60)
    delete_id = new_id()
    write_delete_request(layout.inbox, request_id=delete_id, requested_by="professor-a", target=first)

    intake.run_once(layout, clock.now)

    assert assert_valid_response(layout, delete_id)["outcome"] == "deleted"
    for request_id in (first, second):
        doc = assert_valid_response(layout, request_id)
        assert doc["state"] == "deleted"
        assert doc["deleted"] == {"at": "2026-03-02T01:01:00Z"}
        assert "failure" not in doc
    assert not (layout.audio / first).exists()
    assert list(layout.queue.iterdir()) == []
    assert leftovers(layout.root) == []


@pytest.mark.parametrize("first_runs", [False, True], ids=["first_queued", "first_done"])
def test_edge_8_second_pass_after_the_purge_time_before_the_sweep_refused(
    layout: Layout, wav: Path, clock: FakeClock, first_runs: bool
) -> None:
    first = submit(layout, wav, clock)
    if first_runs:
        run_worker(layout, clock)
    clock.set(AFTER_PURGE)

    second = submit_second(layout, clock, first)

    assert_source_unavailable(layout, second)
    assert not (layout.audio / first).exists()
    audio = load_audio(layout, first)
    assert (audio.deleted_at, audio.deleted_by) == (clock.now(), "purge")
    doc = assert_valid_response(layout, first)
    assert doc["audio"]["deleted_at"] == AFTER_PURGE
    if first_runs:
        assert doc["state"] == "done"
    else:
        assert doc["failure"] == {
            "reason": "audio_deleted",
            "detail": "The recording was deleted before the job finished.",
        }
    assert leftovers(layout.root) == []
