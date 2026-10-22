"""Records tests: Job and Audio JSON round-trip, atomic save and load, sequence and queue helpers."""

from __future__ import annotations

import dataclasses
import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stt.server import deadlines
from stt.server.layout import Layout
from stt.server.records import (
    Audio,
    Failure,
    Job,
    load_audio,
    load_job,
    load_jobs,
    next_seq,
    queue_position,
    queued_in_order,
    save_audio,
    save_job,
)

REQUEST_ID = "0b9a8c2e-5f1d-4c3a-9e7b-2d6f4a1c8e30"
OTHER_ID = "7c1e5d90-3b2a-4f68-8d14-a9e0c3b5f712"
RECEIVED = datetime(2026, 3, 2, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def layout(root: Path) -> Layout:
    state = Layout(root)
    state.ensure_layout()
    return state


def make_job(seq: int = 1, state: str = "queued", request_id: str = REQUEST_ID, **changes: object) -> Job:
    job = Job(
        request_id=request_id,
        requested_by="professor-a",
        seq=seq,
        accepted_at=RECEIVED,
        audio_id=REQUEST_ID,
        language="ko",
        diarize=True,
        hints=("alpha", "beta"),
        delete_audio="after_job",
        duration_s=61.5,
        state=state,
        expires_at=deadlines.expires_at(RECEIVED),
    )
    return dataclasses.replace(job, **changes)


def make_audio(**changes: object) -> Audio:
    audio = Audio(
        audio_id=REQUEST_ID,
        original_name=f"{REQUEST_ID}.audio.wav",
        owner="professor-a",
        received_at=RECEIVED,
        hard_deadline=deadlines.hard_deadline(RECEIVED),
        policy={"after_job": REQUEST_ID},
        users=(REQUEST_ID,),
    )
    return dataclasses.replace(audio, **changes)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestJobRoundTrip:
    def test_queued_job_survives_save_and_load(self, layout: Layout) -> None:
        job = make_job()
        save_job(layout, job)
        assert load_job(layout, REQUEST_ID) == job

    def test_every_optional_field_survives(self, layout: Layout) -> None:
        later = RECEIVED + timedelta(minutes=5)
        job = make_job(
            state="failed",
            hints=None,
            started_at=later,
            finished_at=later + timedelta(seconds=30),
            failure=Failure(reason="audio_deleted", detail="The audio was deleted.", cause="midnight"),
            result_deleted_at=later + timedelta(hours=1),
            deleted_at=later + timedelta(hours=2),
            duplicate_refused_at=later + timedelta(hours=3),
        )
        save_job(layout, job)
        assert load_job(layout, REQUEST_ID) == job

    def test_failure_without_cause_omits_the_key(self, layout: Layout) -> None:
        job = make_job(state="failed", failure=Failure(reason="engine_failed", detail="The engine failed."))
        save_job(layout, job)
        raw = json.loads((layout.jobs / REQUEST_ID / "job.json").read_text(encoding="utf-8"))
        assert raw["failure"] == {"reason": "engine_failed", "detail": "The engine failed."}
        assert load_job(layout, REQUEST_ID).failure == Failure("engine_failed", "The engine failed.")

    def test_times_are_stored_as_contract_strings(self, layout: Layout) -> None:
        save_job(layout, make_job())
        raw = json.loads((layout.jobs / REQUEST_ID / "job.json").read_text(encoding="utf-8"))
        assert raw["accepted_at"] == deadlines.format_time(RECEIVED)
        assert raw["expires_at"] == deadlines.format_time(deadlines.expires_at(RECEIVED))
        assert raw["started_at"] is None

    def test_loaded_times_are_utc_aware(self, layout: Layout) -> None:
        save_job(layout, make_job())
        assert load_job(layout, REQUEST_ID).accepted_at.utcoffset() == timedelta(0)

    def test_hints_are_stored_as_a_list(self, layout: Layout) -> None:
        save_job(layout, make_job())
        raw = json.loads((layout.jobs / REQUEST_ID / "job.json").read_text(encoding="utf-8"))
        assert raw["hints"] == ["alpha", "beta"]

    def test_job_is_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            make_job().state = "running"  # type: ignore[misc]

    def test_failure_is_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            Failure("a", "b").reason = "c"  # type: ignore[misc]

    def test_replace_then_save_changes_the_stored_record(self, layout: Layout) -> None:
        job = make_job()
        save_job(layout, job)
        running = dataclasses.replace(job, state="running", started_at=RECEIVED + timedelta(seconds=9))
        save_job(layout, running)
        assert load_job(layout, REQUEST_ID) == running
        assert job.state == "queued"


class TestJobFiles:
    def test_record_path_and_mode(self, layout: Layout) -> None:
        save_job(layout, make_job())
        path = layout.jobs / REQUEST_ID / "job.json"
        assert path.is_file()
        assert mode_of(path) == 0o600

    def test_save_leaves_no_temporary_file(self, layout: Layout) -> None:
        save_job(layout, make_job())
        assert [p.name for p in (layout.jobs / REQUEST_ID).iterdir()] == ["job.json"]

    def test_job_folder_is_private(self, layout: Layout) -> None:
        save_job(layout, make_job())
        assert mode_of(layout.jobs / REQUEST_ID) == 0o700

    def test_load_missing_job_raises(self, layout: Layout) -> None:
        with pytest.raises(FileNotFoundError):
            load_job(layout, REQUEST_ID)

    def test_load_jobs_is_empty_without_jobs(self, layout: Layout) -> None:
        assert load_jobs(layout) == []

    def test_load_jobs_returns_all_ordered_by_seq(self, layout: Layout) -> None:
        third = make_job(seq=3, request_id="00000000-0000-4000-8000-000000000003")
        first = make_job(seq=1, request_id="00000000-0000-4000-8000-000000000009")
        second = make_job(seq=2, request_id="00000000-0000-4000-8000-000000000001")
        for job in (third, first, second):
            save_job(layout, job)
        assert load_jobs(layout) == [first, second, third]

    def test_load_jobs_ignores_folders_without_a_record(self, layout: Layout) -> None:
        save_job(layout, make_job())
        (layout.jobs / OTHER_ID).mkdir()
        assert load_jobs(layout) == [make_job()]


class TestAudioRoundTrip:
    def test_after_job_policy_survives(self, layout: Layout) -> None:
        audio = make_audio()
        save_audio(layout, audio)
        assert load_audio(layout, REQUEST_ID) == audio

    def test_at_policy_survives_as_datetime(self, layout: Layout) -> None:
        midnight = deadlines.next_midnight(RECEIVED)
        audio = make_audio(policy={"at": midnight})
        save_audio(layout, audio)
        loaded = load_audio(layout, REQUEST_ID)
        assert loaded == audio
        assert deadlines.audio_delete_at(loaded.policy, loaded.hard_deadline) == midnight

    def test_deleted_fields_survive(self, layout: Layout) -> None:
        audio = make_audio(deleted_at=RECEIVED + timedelta(hours=1), deleted_by="after_job")
        save_audio(layout, audio)
        assert load_audio(layout, REQUEST_ID) == audio

    def test_times_are_stored_as_contract_strings(self, layout: Layout) -> None:
        midnight = deadlines.next_midnight(RECEIVED)
        save_audio(layout, make_audio(policy={"at": midnight}))
        raw = json.loads((layout.audio / f"{REQUEST_ID}.json").read_text(encoding="utf-8"))
        assert raw["policy"] == {"at": deadlines.format_time(midnight)}
        assert raw["received_at"] == deadlines.format_time(RECEIVED)
        assert raw["users"] == [REQUEST_ID]
        assert raw["deleted_at"] is None
        assert raw["deleted_by"] is None

    def test_audio_is_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            make_audio().deleted_by = "request"  # type: ignore[misc]

    def test_record_is_outside_the_audio_file_folder(self, layout: Layout) -> None:
        (layout.audio / REQUEST_ID).mkdir()
        save_audio(layout, make_audio())
        assert (layout.audio / f"{REQUEST_ID}.json").is_file()
        assert list((layout.audio / REQUEST_ID).iterdir()) == []

    def test_record_survives_removing_the_file_folder(self, layout: Layout) -> None:
        (layout.audio / REQUEST_ID).mkdir()
        save_audio(layout, make_audio())
        (layout.audio / REQUEST_ID).rmdir()
        assert load_audio(layout, REQUEST_ID) == make_audio()

    def test_record_mode_and_no_temporary_file(self, layout: Layout) -> None:
        save_audio(layout, make_audio())
        assert mode_of(layout.audio / f"{REQUEST_ID}.json") == 0o600
        assert [p.name for p in layout.audio.iterdir()] == [f"{REQUEST_ID}.json"]

    def test_load_missing_audio_raises(self, layout: Layout) -> None:
        with pytest.raises(FileNotFoundError):
            load_audio(layout, REQUEST_ID)


class TestNextSeq:
    def test_first_seq_is_one(self) -> None:
        assert next_seq([]) == 1

    def test_is_max_plus_one_not_count(self) -> None:
        jobs = [make_job(seq=1, request_id="a"), make_job(seq=7, request_id="b"), make_job(seq=3, request_id="c")]
        assert next_seq(jobs) == 8

    def test_terminal_jobs_still_count(self) -> None:
        assert next_seq([make_job(seq=4, state="done")]) == 5


class TestQueuePosition:
    def jobs(self) -> list[Job]:
        return [
            make_job(seq=1, state="running", request_id="r1"),
            make_job(seq=2, state="done", request_id="d2"),
            make_job(seq=3, state="queued", request_id="q3"),
            make_job(seq=4, state="failed", request_id="f4"),
            make_job(seq=5, state="queued", request_id="q5"),
            make_job(seq=6, state="queued", request_id="q6"),
        ]

    def test_first_queued_is_one_even_with_a_running_job(self) -> None:
        jobs = self.jobs()
        assert queue_position(jobs[2], jobs) == 1

    def test_counts_only_earlier_queued_jobs(self) -> None:
        jobs = self.jobs()
        assert queue_position(jobs[4], jobs) == 2
        assert queue_position(jobs[5], jobs) == 3

    def test_independent_of_list_order(self) -> None:
        jobs = list(reversed(self.jobs()))
        assert queue_position(next(j for j in jobs if j.request_id == "q6"), jobs) == 3

    @pytest.mark.parametrize("state", ["running", "done", "failed", "deleted"])
    def test_none_unless_queued(self, state: str) -> None:
        job = make_job(seq=9, state=state, request_id="x")
        assert queue_position(job, [job]) is None


class TestQueuedInOrder:
    def test_only_queued_by_seq(self) -> None:
        jobs = [
            make_job(seq=5, state="queued", request_id="q5"),
            make_job(seq=1, state="running", request_id="r1"),
            make_job(seq=3, state="queued", request_id="q3"),
            make_job(seq=4, state="done", request_id="d4"),
        ]
        assert [job.request_id for job in queued_in_order(jobs)] == ["q3", "q5"]

    def test_empty(self) -> None:
        assert queued_in_order([]) == []
