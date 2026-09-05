import json
from decimal import Decimal

from run_site.metrics import message_usage, session_metrics, sum_usage
from run_site.stages import session_metadata


def test_reported_totals_and_decimal_costs_are_preserved():
    first = message_usage(
        {
            "input": 10,
            "output": 3,
            "cacheRead": 4,
            "cacheWrite": 2,
            "reasoning": 100,
            "totalTokens": 22,
            "cost": {
                "input": 0.1,
                "output": 0.2,
                "cacheRead": 0.01,
                "cacheWrite": 0.02,
                "total": 0.35,
            },
        }
    )
    second = message_usage(
        {
            "input": 1,
            "output": 2,
            "cacheRead": 0,
            "cacheWrite": 0,
            "cost": {"input": 0.2, "output": 0.1, "cacheRead": 0, "cacheWrite": 0},
        }
    )
    total = sum_usage([first, second])
    assert total["tokens"] == {
        "input": 11,
        "output": 5,
        "cache_read": 4,
        "cache_write": 2,
        "cache": 6,
        "total": 25,
    }
    assert Decimal(total["cost"]["input"]) == Decimal("0.3")
    assert Decimal(total["cost"]["cache"]) == Decimal("0.03")
    assert Decimal(total["cost"]["total"]) == Decimal("0.65")
    assert total["complete"]


def test_missing_usage_is_unknown_and_reported_zero_is_zero():
    usage = message_usage(
        {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": {"total": 0}}
    )
    assert usage["tokens"]["total"] == 0
    assert usage["cost"]["total"] == "0"
    assert usage["cost"]["input"] is None
    assert not sum_usage([usage])["complete"]
    assert sum_usage([usage, None])["tokens"]["total"] is None
    assert sum_usage([])["cost"]["total"] is None
    invalid = message_usage(
        {"input": -1, "output": 1.5, "cacheRead": True, "cacheWrite": "NaN"}
    )
    assert all(value is None for value in invalid["tokens"].values())


def test_session_usage_counts_assistant_messages_and_ignores_late_renames(tmp_path):
    path = tmp_path / "session.jsonl"
    usage = {
        "input": 10,
        "output": 2,
        "cacheRead": 3,
        "cacheWrite": 0,
        "cost": {"total": 0.1},
    }
    records = [
        {"type": "session", "timestamp": "2026-01-01T00:00:00Z"},
        {
            "type": "message",
            "timestamp": "2026-01-01T00:00:10Z",
            "message": {"role": "user", "content": "Analyze", "usage": usage},
        },
        {
            "type": "message",
            "timestamp": "2026-01-01T00:01:00Z",
            "message": {"role": "assistant", "usage": usage},
        },
        {
            "type": "message",
            "timestamp": "2026-01-01T00:01:30Z",
            "message": {"role": "toolResult", "usage": usage},
        },
        # Message timestamp fallback is milliseconds; records need not be chronological.
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "timestamp": 1767225680000,
                "usage": usage,
            },
        },
        {
            "type": "session_info",
            "timestamp": "2026-02-01T00:00:00Z",
            "name": "Later rename",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records), encoding="utf-8"
    )
    session = session_metadata(path)
    assert session["usage"]["tokens"]["total"] == 30
    assert Decimal(session["usage"]["cost"]["total"]) == Decimal("0.2")
    assert session_metrics([session])["duration_seconds"] == 90


def test_stage_retry_span_and_runner_duration_precedence():
    first = {"started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:01:00Z"}
    retry = {
        "started_at": "2026-01-01T01:02:00+01:00",
        "ended_at": "2026-01-01T01:03:00+01:00",
    }
    assert session_metrics([first, retry])["duration_seconds"] == 180
    result = session_metrics([first, retry], {"duration_seconds": 200.125})
    assert result["duration_seconds"] == 200.125
    assert result["duration_source"] == "runner"
    assert session_metrics([first], {"duration_seconds": -1})["duration_seconds"] == 60
    assert (
        session_metrics(
            [], {"started_at": first["started_at"], "completed_at": first["ended_at"]}
        )["duration_seconds"]
        == 60
    )
    assert session_metrics([first, {}])["duration_seconds"] is None
