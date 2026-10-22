"""Responses tests: every response document is valid against the response schema."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from stt.server import contract, deadlines
from stt.server.records import Audio, Failure, Job
from stt.server.responses import delete_outcome, job_status, refusal, rejected_status

REQUEST_ID = "0b9a8c2e-5f1d-4c3a-9e7b-2d6f4a1c8e30"
OTHER_ID = "7c1e5d90-3b2a-4f68-8d14-a9e0c3b5f712"
THIRD_ID = "d4a7f2b1-6c8e-4a90-b3d5-1e2f7a9c0b64"
ACCEPTED = datetime(2026, 3, 2, 1, 0, 0, tzinfo=timezone.utc)
STARTED = ACCEPTED + timedelta(minutes=2)
FINISHED = ACCEPTED + timedelta(minutes=9)
NOW = ACCEPTED + timedelta(minutes=10)
MIDNIGHT = datetime(2026, 3, 2, 15, 0, 0, tzinfo=timezone.utc)
FAILURE_DETAIL = "The recording could not be decoded."


def make_job(state: str = "queued", seq: int = 1, request_id: str = REQUEST_ID, **changes: object) -> Job:
    job = Job(
        request_id=request_id,
        requested_by="professor-a",
        seq=seq,
        accepted_at=ACCEPTED,
        audio_id=REQUEST_ID,
        language="ko",
        diarize=True,
        hints=None,
        delete_audio="after_job",
        duration_s=61.5,
        state=state,
        expires_at=deadlines.expires_at(ACCEPTED),
    )
    return dataclasses.replace(job, **changes)


def make_audio(**changes: object) -> Audio:
    audio = Audio(
        audio_id=REQUEST_ID,
        original_name=f"{REQUEST_ID}.audio.wav",
        owner="professor-a",
        received_at=ACCEPTED,
        hard_deadline=deadlines.hard_deadline(ACCEPTED),
        policy={"after_job": REQUEST_ID},
        users=(REQUEST_ID,),
    )
    return dataclasses.replace(audio, **changes)


def valid(doc: dict[str, Any]) -> dict[str, Any]:
    assert contract.validate_response(doc) is None
    return doc


def done_job(state: str = "done", **changes: object) -> Job:
    return make_job(state, started_at=STARTED, finished_at=FINISHED, **changes)


class TestQueued:
    def test_queued_job_has_position_and_no_run_times(self) -> None:
        job = make_job("queued", seq=3)
        jobs = [make_job("queued", seq=1, request_id=OTHER_ID), make_job("running", seq=2, request_id=THIRD_ID), job]
        doc = valid(job_status(job, make_audio(), jobs, NOW))
        assert doc["schema_version"] == 1
        assert doc["kind"] == "transcribe"
        assert doc["request_id"] == REQUEST_ID
        assert doc["state"] == "queued"
        assert doc["queue_position"] == 2
        assert doc["accepted_at"] == "2026-03-02T01:00:00Z"
        assert doc["updated_at"] == "2026-03-02T01:10:00Z"
        assert doc["expires_at"] == "2026-03-03T00:58:00Z"
        for absent in ("started_at", "finished_at", "result", "failure", "deleted", "duplicate_refused_at"):
            assert absent not in doc

    def test_documents_carry_no_requester_hints_or_text(self) -> None:
        job = make_job("queued", hints=("alpha",))
        doc = valid(job_status(job, make_audio(), [job], NOW))
        assert "requested_by" not in doc
        assert "hints" not in doc
        assert "professor-a" not in repr(doc)
        assert "alpha" not in repr(doc)


class TestRunning:
    def test_running_job_has_started_at_and_no_position(self) -> None:
        job = make_job("running", started_at=STARTED)
        doc = valid(job_status(job, make_audio(), [job], NOW))
        assert doc["state"] == "running"
        assert doc["started_at"] == "2026-03-02T01:02:00Z"
        assert "queue_position" not in doc
        assert "finished_at" not in doc


class TestDone:
    def test_done_job_points_at_transcript_and_data(self) -> None:
        job = done_job()
        doc = valid(job_status(job, make_audio(), [job], NOW))
        assert doc["state"] == "done"
        assert doc["result"] == {"transcript": f"{REQUEST_ID}/transcript.txt", "data": f"{REQUEST_ID}/result.json"}
        assert doc["finished_at"] == "2026-03-02T01:09:00Z"
        assert "queue_position" not in doc

    def test_deleted_result_adds_result_deleted_at(self) -> None:
        job = done_job(result_deleted_at=NOW)
        doc = valid(job_status(job, make_audio(), [job], NOW))
        assert doc["state"] == "done"
        assert doc["result"]["deleted_at"] == "2026-03-02T01:10:00Z"
        assert doc["result"]["transcript"] == f"{REQUEST_ID}/transcript.txt"


class TestFailed:
    def test_failed_job_carries_reason_and_detail(self) -> None:
        job = make_job("failed", started_at=STARTED, finished_at=FINISHED, failure=Failure("engine_error", FAILURE_DETAIL))
        doc = valid(job_status(job, make_audio(), [job], NOW))
        assert doc["state"] == "failed"
        assert doc["failure"] == {"reason": "engine_error", "detail": FAILURE_DETAIL}
        assert doc["finished_at"] == "2026-03-02T01:09:00Z"
        assert "result" not in doc

    def test_audio_deleted_failure_carries_cause(self) -> None:
        failure = Failure("audio_deleted", FAILURE_DETAIL, cause="midnight")
        job = make_job("failed", finished_at=FINISHED, failure=failure)
        doc = valid(job_status(job, make_audio(deleted_at=FINISHED), [job], NOW))
        assert doc["failure"] == {"reason": "audio_deleted", "detail": FAILURE_DETAIL, "cause": "midnight"}
        assert "started_at" not in doc


class TestDeleted:
    def test_deleted_job_has_deleted_at_and_no_failure(self) -> None:
        failure = Failure("engine_error", FAILURE_DETAIL)
        job = make_job("deleted", started_at=STARTED, finished_at=FINISHED, failure=failure, deleted_at=NOW)
        doc = valid(job_status(job, make_audio(), [job], NOW))
        assert doc["state"] == "deleted"
        assert doc["deleted"] == {"at": "2026-03-02T01:10:00Z"}
        assert "failure" not in doc
        assert "result" not in doc
        assert doc["started_at"] == "2026-03-02T01:02:00Z"
        assert doc["finished_at"] == "2026-03-02T01:09:00Z"

    def test_deleted_job_keeps_result_with_deleted_at_when_it_had_one(self) -> None:
        job = done_job(state="deleted", deleted_at=NOW, result_deleted_at=NOW)
        doc = valid(job_status(job, make_audio(), [job], NOW))
        assert doc["state"] == "deleted"
        assert doc["result"] == {
            "transcript": f"{REQUEST_ID}/transcript.txt",
            "data": f"{REQUEST_ID}/result.json",
            "deleted_at": "2026-03-02T01:10:00Z",
        }
        assert doc["started_at"] == "2026-03-02T01:02:00Z"
        assert doc["finished_at"] == "2026-03-02T01:09:00Z"
        assert "failure" not in doc

    def test_deleted_while_queued_has_neither_result_nor_run_times(self) -> None:
        job = make_job("deleted", deleted_at=NOW)
        doc = valid(job_status(job, make_audio(), [job], NOW))
        for absent in ("result", "started_at", "finished_at", "queue_position"):
            assert absent not in doc


class TestAudioBlock:
    def test_after_job_policy_names_the_deciding_request(self) -> None:
        job = make_job("queued")
        audio = make_audio(policy={"after_job": OTHER_ID})
        doc = valid(job_status(job, audio, [job], NOW))
        assert doc["audio"] == {
            "delete_policy": "after_job",
            "delete_after": OTHER_ID,
            "delete_at": "2026-03-03T00:58:00Z",
        }

    def test_at_midnight_policy_has_no_delete_after(self) -> None:
        job = make_job("queued", delete_audio="at_midnight")
        audio = make_audio(policy={"at": MIDNIGHT})
        doc = valid(job_status(job, audio, [job], NOW))
        assert doc["audio"] == {"delete_policy": "at_midnight", "delete_at": "2026-03-02T15:00:00Z"}

    def test_delete_at_is_capped_by_the_hard_deadline(self) -> None:
        job = make_job("queued", delete_audio="at_midnight")
        late = ACCEPTED + timedelta(days=3)
        audio = make_audio(policy={"at": late})
        doc = valid(job_status(job, audio, [job], NOW))
        assert doc["audio"]["delete_at"] == "2026-03-03T00:58:00Z"

    def test_deleted_audio_adds_deleted_at(self) -> None:
        job = done_job()
        audio = make_audio(deleted_at=FINISHED, deleted_by="after_job")
        doc = valid(job_status(job, audio, [job], NOW))
        assert doc["audio"]["deleted_at"] == "2026-03-02T01:09:00Z"
        assert "deleted_by" not in doc["audio"]


class TestDuplicate:
    def test_duplicate_refused_at_is_included_when_set(self) -> None:
        job = make_job("queued", duplicate_refused_at=NOW)
        doc = valid(job_status(job, make_audio(), [job], NOW))
        assert doc["duplicate_refused_at"] == "2026-03-02T01:10:00Z"


class TestRejectedStatus:
    def test_rejected_transcribe_is_failed_without_accepted_at(self) -> None:
        doc = valid(rejected_status(REQUEST_ID, "unreadable_audio", FAILURE_DETAIL, NOW))
        assert doc["kind"] == "transcribe"
        assert doc["state"] == "failed"
        assert doc["failure"] == {"reason": "unreadable_audio", "detail": FAILURE_DETAIL}
        assert doc["finished_at"] == "2026-03-02T01:10:00Z"
        assert doc["updated_at"] == "2026-03-02T01:10:00Z"
        assert doc["expires_at"] == "2026-03-03T01:08:00Z"
        for absent in ("accepted_at", "started_at", "audio", "result", "queue_position", "duplicate_refused_at"):
            assert absent not in doc

    def test_rejected_status_accepts_duplicate_refused_at(self) -> None:
        later = NOW + timedelta(minutes=1)
        doc = valid(rejected_status(REQUEST_ID, "invalid_request", FAILURE_DETAIL, NOW, duplicate_refused_at=later))
        assert doc["duplicate_refused_at"] == "2026-03-02T01:11:00Z"

    def test_none_duplicate_leaves_the_field_out(self) -> None:
        doc = rejected_status(REQUEST_ID, "invalid_request", FAILURE_DETAIL, NOW, duplicate_refused_at=None)
        assert "duplicate_refused_at" not in doc


class TestDeleteOutcome:
    @pytest.mark.parametrize("outcome", ["deleted", "already_deleted", "no_result", "refused"])
    @pytest.mark.parametrize("scope", ["result", "everything"])
    def test_every_outcome_and_scope_is_valid(self, outcome: str, scope: str) -> None:
        doc = valid(delete_outcome(OTHER_ID, REQUEST_ID, scope, outcome, NOW))
        assert doc == {
            "schema_version": 1,
            "kind": "delete",
            "request_id": OTHER_ID,
            "target": REQUEST_ID,
            "scope": scope,
            "outcome": outcome,
            "processed_at": "2026-03-02T01:10:00Z",
            "expires_at": "2026-03-03T01:08:00Z",
        }

    def test_delete_outcome_accepts_duplicate_refused_at(self) -> None:
        later = NOW + timedelta(minutes=1)
        doc = valid(delete_outcome(OTHER_ID, REQUEST_ID, "result", "deleted", NOW, duplicate_refused_at=later))
        assert doc["duplicate_refused_at"] == "2026-03-02T01:11:00Z"

    def test_none_duplicate_leaves_the_field_out(self) -> None:
        doc = delete_outcome(OTHER_ID, REQUEST_ID, "result", "deleted", NOW, duplicate_refused_at=None)
        assert "duplicate_refused_at" not in doc


class TestRefusal:
    def test_refusal_is_invalid_kind_with_invalid_request_failure(self) -> None:
        doc = valid(refusal(REQUEST_ID, "kind: must be transcribe or delete", NOW))
        assert doc == {
            "schema_version": 1,
            "kind": "invalid",
            "request_id": REQUEST_ID,
            "failure": {"reason": "invalid_request", "detail": "kind: must be transcribe or delete"},
            "processed_at": "2026-03-02T01:10:00Z",
            "expires_at": "2026-03-03T01:08:00Z",
        }

    def test_refusal_accepts_duplicate_refused_at(self) -> None:
        later = NOW + timedelta(minutes=1)
        doc = valid(refusal(REQUEST_ID, "request: file is not valid JSON", NOW, duplicate_refused_at=later))
        assert doc["duplicate_refused_at"] == "2026-03-02T01:11:00Z"

    def test_none_duplicate_leaves_the_field_out(self) -> None:
        assert "duplicate_refused_at" not in refusal(REQUEST_ID, "request: file is not valid JSON", NOW, None)
