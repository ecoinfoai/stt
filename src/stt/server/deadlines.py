"""Pure time functions: the daily purge time, processing limits and contract times."""

from __future__ import annotations

from datetime import datetime, time, timezone
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


def purge_time(t: datetime) -> datetime:
    """Returns the daily purge of `t`'s day: 23:58:00 Asia/Seoul on the Seoul date of `t`, in UTC.

    Every deadline is this value: a job's ``expires_at`` from its acceptance and a
    recording's ``delete_at`` from its receipt. For `t` between 23:58 and midnight it
    has already passed, so the next sweep removes what arrived then; nothing lives
    24 hours.

    Raises:
        ValueError: If `t` is naive.
    """
    _require_aware(t)
    purge = datetime.combine(t.astimezone(SEOUL).date(), _PURGE_TIME, tzinfo=SEOUL)
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
