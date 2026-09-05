"""Recorded Pi usage and elapsed time; unknown values remain unknown."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

FIELDS = ("input", "output", "cache_read", "cache_write", "cache", "total")
PI_FIELDS = {
    "input": "input",
    "output": "output",
    "cache_read": "cacheRead",
    "cache_write": "cacheWrite",
}


def number(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def known_sum(values):
    return (
        sum(values, Decimal(0))
        if values and all(v is not None for v in values)
        else None
    )


def message_usage(usage) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    cost = usage.get("cost")
    cost = cost if isinstance(cost, dict) else {}
    result = {}
    for kind, source in (("tokens", usage), ("cost", cost)):
        values = {key: number(source.get(pi_key)) for key, pi_key in PI_FIELDS.items()}
        if kind == "tokens":
            values = {
                key: value if value is None or value == int(value) else None
                for key, value in values.items()
            }
        values["cache"] = known_sum([values["cache_read"], values["cache_write"]])
        total = number(source.get("totalTokens" if kind == "tokens" else "total"))
        if kind == "tokens" and total is not None and total != int(total):
            total = None
        values["total"] = (
            total if total is not None else known_sum([values[k] for k in PI_FIELDS])
        )
        result[kind] = {
            key: None
            if value is None
            else int(value)
            if kind == "tokens"
            else str(value)
            for key, value in values.items()
        }
    return result


def sum_usage(items: list[dict | None]) -> dict:
    result = {}
    for kind in ("tokens", "cost"):
        values = {
            key: known_sum(
                [number((item or {}).get(kind, {}).get(key)) for item in items]
            )
            for key in FIELDS
        }
        result[kind] = {
            key: None
            if value is None
            else int(value)
            if kind == "tokens"
            else str(value)
            for key, value in values.items()
        }
    result["complete"] = (
        bool(items)
        and all(item and item.get("complete", True) for item in items)
        and all(
            value is not None
            for kind in ("tokens", "cost")
            for value in result[kind].values()
        )
    )
    return result


def timestamp(value) -> datetime | None:
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone(UTC) if parsed.tzinfo else None
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            # Pi message timestamps are Unix milliseconds.
            return datetime.fromtimestamp(float(value) / 1000, UTC)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def session_metrics(sessions: list[dict], manifest: dict | None = None) -> dict:
    starts = [timestamp(session.get("started_at")) for session in sessions]
    ends = [timestamp(session.get("ended_at")) for session in sessions]
    duration = None
    source = None
    if (
        sessions
        and all(starts)
        and all(ends)
        and all(end >= start for start, end in zip(starts, ends))
    ):
        duration = (max(ends) - min(starts)).total_seconds()
        source = "session_span"
    if manifest is not None:
        recorded = number(manifest.get("duration_seconds"))
        start, end = (
            timestamp(manifest.get("started_at")),
            timestamp(manifest.get("completed_at")),
        )
        if recorded is not None:
            duration, source = float(recorded), "runner"
        elif start and end and end >= start:
            duration, source = (end - start).total_seconds(), "runner_timestamps"
    return {
        "usage": sum_usage([session.get("usage") for session in sessions]),
        "duration_seconds": duration,
        "duration_source": source,
    }
