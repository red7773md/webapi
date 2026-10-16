"""Spacing for checks of Gemini, shared by research jobs and chats.

Each job or chat keeps its own check time. A check that Gemini cannot answer doubles
the gap before the next one, up to MAX_CHECK_GAP_SECONDS, so a failing connection is
not hammered. A check that succeeds resets the gap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MAX_CHECK_GAP_SECONDS = 600.0


def error_text(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def seconds_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()


def check_is_due(record: dict[str, Any], gap: float) -> bool:
    """Whether enough time has passed since the record's last check to ask Gemini again."""
    failures = int(record.get("check_failures") or 0)
    needed = min(gap * 2 ** min(failures, 10), MAX_CHECK_GAP_SECONDS)
    return seconds_since(record.get("checked_at")) >= needed
