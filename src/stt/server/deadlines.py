"""Pure time functions: the daily purge time, processing limits and contract times."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
_PURGE_TIME = time(23, 58)
MIN_PROCESSING_S = 300.0
MAX_AUDIO_S = 10_800.0

_CONTRACT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_NAIVE_MESSAGE = "datetime must be timezone-aware"
_BAD_TIME_MESSAGE = "value is not a contract time (YYYY-MM-DDTHH:MM:SSZ)"


def _require_aware(t: datetime) -> None:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(_NAIVE_MESSAGE)


def next_purge(t: datetime) -> datetime:
    """Returns the daily purge that follows `t`: the first Asia/Seoul 23:58:00 strictly after it, in UTC.

    Every deadline is this value: a job's ``expires_at`` from its acceptance and a
    recording's ``delete_at`` from its receipt.

    Raises:
        ValueError: If `t` is naive.
    """
    _require_aware(t)
    local = t.astimezone(SEOUL)
    purge = datetime.combine(local.date(), _PURGE_TIME, tzinfo=SEOUL)
    if purge <= local:
        purge = datetime.combine(local.date() + timedelta(days=1), _PURGE_TIME, tzinfo=SEOUL)
    return purge.astimezone(timezone.utc)


def processing_limit(duration_s: float) -> float:
    """Returns the processing time limit in seconds: max(audio length, 5 minutes)."""
    return max(duration_s, MIN_PROCESSING_S)


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
