"""Deadline arithmetic tests: KST midnight, retention limits, audio delete time, contract times."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest

from stt.server import deadlines

UTC = timezone.utc


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (utc(2026, 10, 22, 14, 59, 59), utc(2026, 10, 22, 15, 0, 0)),
        (utc(2026, 10, 22, 15, 0, 0), utc(2026, 10, 23, 15, 0, 0)),
        (utc(2026, 10, 22, 15, 0, 1), utc(2026, 10, 23, 15, 0, 0)),
        (utc(2026, 10, 22, 0, 0, 0), utc(2026, 10, 22, 15, 0, 0)),
    ],
)
def test_next_midnight_is_the_following_seoul_midnight(given: datetime, expected: datetime) -> None:
    assert deadlines.next_midnight(given) == expected


def test_next_midnight_accepts_non_utc_aware_input() -> None:
    kst = timezone(timedelta(hours=9))
    assert deadlines.next_midnight(datetime(2026, 10, 22, 23, 59, 59, tzinfo=kst)) == utc(
        2026, 10, 22, 15, 0, 0
    )


def test_next_midnight_returns_utc_aware_value() -> None:
    result = deadlines.next_midnight(utc(2026, 10, 22, 1, 0, 0))
    assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "function",
    [deadlines.next_midnight, deadlines.hard_deadline, deadlines.expires_at, deadlines.format_time],
)
def test_naive_datetime_is_rejected(function: Callable[[datetime], object]) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        function(datetime(2026, 10, 22, 1, 0, 0))


def test_retention_due_leaves_two_minutes_of_slack() -> None:
    assert deadlines.RETENTION_DUE == timedelta(hours=23, minutes=58)
    assert timedelta(hours=24) - deadlines.RETENTION_DUE == timedelta(minutes=2)


def test_hard_deadline_and_expires_at_are_retention_after_the_start() -> None:
    t = utc(2026, 10, 22, 3, 4, 5)
    assert deadlines.hard_deadline(t) == t + timedelta(hours=23, minutes=58)
    assert deadlines.expires_at(t) == t + timedelta(hours=23, minutes=58)


@pytest.mark.parametrize(
    ("duration_s", "expected"),
    [(10.0, 300.0), (299.9, 300.0), (300.0, 300.0), (3600.0, 3600.0)],
)
def test_processing_limit_is_the_longer_of_audio_length_and_five_minutes(
    duration_s: float, expected: float
) -> None:
    assert deadlines.processing_limit(duration_s) == expected


def test_audio_limits_constants() -> None:
    assert deadlines.MIN_PROCESSING_S == 300.0
    assert deadlines.MAX_AUDIO_S == 10_800.0


def test_audio_delete_at_for_a_time_policy_is_the_earlier_of_policy_and_hard_deadline() -> None:
    hard = utc(2026, 10, 23, 12, 0, 0)
    early = utc(2026, 10, 22, 15, 0, 0)
    late = utc(2026, 10, 24, 15, 0, 0)
    assert deadlines.audio_delete_at({"at": early}, hard) == early
    assert deadlines.audio_delete_at({"at": late}, hard) == hard
    assert deadlines.audio_delete_at({"at": hard}, hard) == hard


def test_audio_delete_at_for_an_after_job_policy_is_the_hard_deadline() -> None:
    hard = utc(2026, 10, 23, 12, 0, 0)
    assert deadlines.audio_delete_at({"after_job": "rid"}, hard) == hard


def test_audio_delete_at_rejects_naive_datetimes() -> None:
    hard = utc(2026, 10, 23, 12, 0, 0)
    naive = datetime(2026, 10, 22, 15, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        deadlines.audio_delete_at({"at": naive}, hard)
    with pytest.raises(ValueError, match="timezone-aware"):
        deadlines.audio_delete_at({"after_job": "rid"}, naive)


def test_format_time_drops_microseconds_and_ends_with_z() -> None:
    assert deadlines.format_time(datetime(2026, 10, 22, 1, 2, 3, 999999, tzinfo=UTC)) == (
        "2026-10-22T01:02:03Z"
    )


def test_format_time_converts_other_offsets_to_utc() -> None:
    kst = timezone(timedelta(hours=9))
    assert deadlines.format_time(datetime(2026, 10, 22, 9, 0, 0, tzinfo=kst)) == (
        "2026-10-22T00:00:00Z"
    )


def test_parse_time_round_trips_format_time() -> None:
    t = utc(2026, 10, 22, 1, 2, 3)
    assert deadlines.parse_time(deadlines.format_time(t)) == t
    assert deadlines.parse_time("2026-10-22T01:02:03Z").utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "text", ["", "2026-10-22", "2026-10-22T01:02:03", "2026-10-22T01:02:03+09:00", "2026-10-22 01:02:03Z"]
)
def test_parse_time_rejects_non_contract_forms(text: str) -> None:
    with pytest.raises(ValueError, match="contract time"):
        deadlines.parse_time(text)
