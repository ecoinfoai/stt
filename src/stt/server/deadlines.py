"""Pure time functions: KST midnight, retention deadlines, limits and contract times."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
MIN_PROCESSING_S = 300.0
MAX_AUDIO_S = 10_800.0
RETENTION_DUE = timedelta(hours=23, minutes=58)

_CONTRACT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_NAIVE_MESSAGE = "datetime must be timezone-aware"
_BAD_TIME_MESSAGE = "value is not a contract time (YYYY-MM-DDTHH:MM:SSZ)"


def _require_aware(t: datetime) -> None:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(_NAIVE_MESSAGE)


def next_midnight(t: datetime) -> datetime:
    """Returns the first Asia/Seoul 00:00 strictly after `t`, as a UTC datetime.

    Raises:
        ValueError: If `t` is naive.
    """
    _require_aware(t)
    next_day = t.astimezone(SEOUL).date() + timedelta(days=1)
    return datetime.combine(next_day, time(), tzinfo=SEOUL).astimezone(timezone.utc)


def hard_deadline(received_at: datetime) -> datetime:
    """Returns the latest moment an audio file may still exist.

    Raises:
        ValueError: If `received_at` is naive.
    """
    _require_aware(received_at)
    return received_at + RETENTION_DUE


def expires_at(accepted_at: datetime) -> datetime:
    """Returns the moment everything a job holds must be gone.

    Raises:
        ValueError: If `accepted_at` is naive.
    """
    _require_aware(accepted_at)
    return accepted_at + RETENTION_DUE


def processing_limit(duration_s: float) -> float:
    """Returns the processing time limit in seconds: max(audio length, 5 minutes)."""
    return max(duration_s, MIN_PROCESSING_S)


def audio_delete_at(policy: Mapping[str, Any], hard: datetime) -> datetime:
    """Returns when an audio file is scheduled to be deleted for a given policy.

    Args:
        policy: `{"at": datetime}` or `{"after_job": request_id}`.
        hard: The audio's hard deadline.

    Raises:
        ValueError: If a datetime is naive.
    """
    _require_aware(hard)
    if "at" in policy:
        _require_aware(policy["at"])
        return min(policy["at"], hard)
    return hard


def format_time(t: datetime) -> str:
    """Formats an aware datetime as a UTC contract time without microseconds.

    Raises:
        ValueError: If `t` is naive.
    """
    _require_aware(t)
    return t.astimezone(timezone.utc).strftime(_CONTRACT_TIME_FORMAT)


def parse_time(text: str) -> datetime:
    """Parses a contract time (`YYYY-MM-DDTHH:MM:SSZ`) into a UTC-aware datetime.

    Raises:
        ValueError: If `text` is not in the contract form.
    """
    try:
        parsed = datetime.strptime(text, _CONTRACT_TIME_FORMAT)
    except ValueError:
        raise ValueError(_BAD_TIME_MESSAGE) from None
    return parsed.replace(tzinfo=timezone.utc)
