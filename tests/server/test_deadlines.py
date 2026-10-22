"""Deadline arithmetic tests: the daily purge time, processing limits and contract times."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest

from stt.server import deadlines

UTC = timezone.utc
SWEEP_PERIOD = timedelta(minutes=1)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (utc(2026, 10, 21, 15, 0, 0), utc(2026, 10, 22, 14, 58, 0)),
        (utc(2026, 10, 22, 0, 0, 0), utc(2026, 10, 22, 14, 58, 0)),
        (utc(2026, 10, 22, 14, 57, 59), utc(2026, 10, 22, 14, 58, 0)),
        (utc(2026, 10, 22, 14, 58, 0), utc(2026, 10, 22, 14, 58, 0)),
        (utc(2026, 10, 22, 14, 59, 59), utc(2026, 10, 22, 14, 58, 0)),
    ],
    ids=["kst_00_00_00", "kst_09_00_00", "kst_23_57_59", "kst_23_58_00", "kst_23_59_59"],
)
def test_purge_time_is_23_58_of_the_seoul_date(given: datetime, expected: datetime) -> None:
    assert deadlines.purge_time(given) == expected


def test_purge_time_accepts_non_utc_aware_input() -> None:
    kst = timezone(timedelta(hours=9))
    assert deadlines.purge_time(datetime(2026, 10, 22, 23, 59, 59, tzinfo=kst)) == utc(2026, 10, 22, 14, 58, 0)
    assert deadlines.purge_time(datetime(2026, 10, 23, 0, 0, 0, tzinfo=kst)) == utc(2026, 10, 23, 14, 58, 0)


def test_purge_time_returns_utc_aware_value() -> None:
    result = deadlines.purge_time(utc(2026, 10, 22, 1, 0, 0))
    assert result.utcoffset() == timedelta(0)


def _assert_gone_within_24_hours(t: datetime) -> None:
    assert deadlines.purge_time(t) + SWEEP_PERIOD < t + timedelta(hours=24), t


def test_purge_time_plus_one_sweep_is_within_24_hours_around_midnight() -> None:
    start = utc(2026, 10, 22, 14, 55, 0)
    for second in range(10 * 60 + 1):
        _assert_gone_within_24_hours(start + timedelta(seconds=second))


def test_purge_time_plus_one_sweep_is_within_24_hours_over_a_whole_day() -> None:
    start = utc(2026, 10, 22, 0, 0, 0)
    for step in range(0, 24 * 60 * 60 + 1, 17):
        _assert_gone_within_24_hours(start + timedelta(seconds=step, microseconds=999_999))


@pytest.mark.parametrize("function", [deadlines.purge_time, deadlines.format_time])
def test_naive_datetime_is_rejected(function: Callable[[datetime], object]) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        function(datetime(2026, 10, 22, 1, 0, 0))


@pytest.mark.parametrize(
    "name", ["RETENTION_DUE", "next_midnight", "next_purge", "hard_deadline", "expires_at", "audio_delete_at"]
)
def test_retention_and_policy_deadlines_are_gone(name: str) -> None:
    assert not hasattr(deadlines, name)


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
