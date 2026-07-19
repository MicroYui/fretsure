from __future__ import annotations

import json
from io import StringIO

import pytest

from fretsure.bench.progress import (
    BENCHMARK_PROGRESS_VERSION,
    ProgressConfig,
    ProgressInputError,
    ProgressReporter,
)


class _Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _lines(stream: StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_start_and_five_percent_report_use_resume_segment_throughput_and_eta() -> None:
    stream = StringIO()
    clock = _Clock(100.0)
    reporter = ProgressReporter(
        stream,
        ProgressConfig(
            "progress-run",
            total_units=100,
            resume_completed_units=20,
            resume_completed_calls=40,
            completed_control_rows=3,
        ),
        clock=clock,
    )

    start = reporter.last_record.to_dict()
    assert start["version"] == BENCHMARK_PROGRESS_VERSION
    assert start["sequence"] == 0
    assert start["triggers"] == ["start"]
    assert start["eta_seconds"] == {
        "conservative": None,
        "median": None,
        "optimistic": None,
    }

    clock.advance(360.0)
    record = reporter.tick(completed_units=25, completed_calls=50)
    assert record is not None
    value = record.to_dict()
    assert value["triggers"] == ["progress_5_percent"]
    assert value["progress"] == {
        "completed_calls": 50,
        "completed_rows": 28,
        "completed_units": 25,
        "percent": 25.0,
        "remaining_units": 75,
        "resume_completed_calls": 40,
        "resume_completed_units": 20,
        "segment_completed_calls": 10,
        "segment_completed_units": 5,
        "total_rows": 103,
        "total_units": 100,
    }
    throughput = value["throughput"]
    assert isinstance(throughput, dict)
    assert throughput["overall"] == {
        "call_delta": 10,
        "calls_per_hour": 100.0,
        "sample_seconds": 360.0,
        "unit_delta": 5,
        "units_per_hour": 50.0,
    }
    assert throughput["recent_15_minutes"] == throughput["overall"]
    assert value["eta_seconds"] == {
        "conservative": 5400,
        "median": 5400,
        "optimistic": 5400,
    }
    assert reporter.tick(completed_units=25, completed_calls=50) is None

    encoded_lines = stream.getvalue().splitlines()
    assert len(encoded_lines) == 2
    assert encoded_lines[-1].encode("utf-8") == record.wire_json


def test_thirty_minutes_reports_stall_with_null_conservative_eta() -> None:
    stream = StringIO()
    clock = _Clock()
    reporter = ProgressReporter(
        stream,
        ProgressConfig("stalled-run", total_units=100),
        clock=clock,
    )

    clock.advance(600.0)
    assert reporter.tick(completed_units=10, completed_calls=20) is not None
    clock.advance(1200.0)
    record = reporter.tick(completed_units=10, completed_calls=20)

    assert record is not None
    value = record.to_dict()
    assert value["triggers"] == [
        "elapsed_30_minutes",
        "throughput_change_25_percent",
    ]
    assert value["stalled"] is True
    assert value["eta_seconds"] == {
        "conservative": None,
        "median": 16200,
        "optimistic": 16200,
    }
    recent = value["throughput"]  # type: ignore[index]
    assert recent["recent_15_minutes"]["unit_delta"] == 0  # type: ignore[index]
    assert recent["recent_15_minutes"]["calls_per_hour"] == 0.0  # type: ignore[index]


def test_adjacent_fifteen_minute_change_obeys_ten_minute_report_cooldown() -> None:
    stream = StringIO()
    clock = _Clock()
    reporter = ProgressReporter(
        stream,
        ProgressConfig("change-run", total_units=10_000),
        clock=clock,
    )

    clock.advance(900.0)
    assert reporter.tick(completed_units=10, completed_calls=20) is None
    clock.advance(900.0)
    thirty = reporter.tick(completed_units=20, completed_calls=40)
    assert thirty is not None
    assert thirty.to_dict()["triggers"] == ["elapsed_30_minutes"]

    clock.advance(300.0)
    assert reporter.tick(completed_units=30, completed_calls=60) is None
    clock.advance(300.0)
    changed = reporter.tick(completed_units=50, completed_calls=100)
    assert changed is not None
    assert changed.to_dict()["triggers"] == [
        "throughput_change_25_percent"
    ]
    throughput = changed.to_dict()["throughput"]
    assert throughput["overall"]["units_per_hour"] == 75.0  # type: ignore[index]
    assert throughput["recent_15_minutes"]["units_per_hour"] == 160.0  # type: ignore[index]


def test_resume_baseline_uses_next_overall_five_percent_threshold() -> None:
    stream = StringIO()
    clock = _Clock()
    reporter = ProgressReporter(
        stream,
        ProgressConfig(
            "resume-run",
            total_units=20,
            resume_completed_units=7,
            resume_completed_calls=100,
        ),
        clock=clock,
    )

    assert reporter.last_record.to_dict()["progress"]["percent"] == 35.0  # type: ignore[index]
    clock.advance(60.0)
    record = reporter.tick(completed_units=8, completed_calls=105)
    assert record is not None
    value = record.to_dict()
    assert value["triggers"] == ["progress_5_percent"]
    assert value["progress"]["segment_completed_units"] == 1  # type: ignore[index]
    assert value["progress"]["segment_completed_calls"] == 5  # type: ignore[index]


def test_completion_has_zero_eta_and_jsonl_is_deterministic() -> None:
    def render() -> str:
        stream = StringIO()
        clock = _Clock(5.0)
        reporter = ProgressReporter(
            stream,
            ProgressConfig("complete-run", total_units=4),
            clock=clock,
        )
        clock.advance(60.0)
        record = reporter.tick(completed_units=4, completed_calls=9)
        assert record is not None
        assert record.to_dict()["eta_seconds"] == {
            "conservative": 0,
            "median": 0,
            "optimistic": 0,
        }
        assert record.to_dict()["stalled"] is False
        return stream.getvalue()

    assert render() == render()


def test_rejects_counter_and_clock_regressions() -> None:
    stream = StringIO()
    clock = _Clock(10.0)
    reporter = ProgressReporter(
        stream,
        ProgressConfig(
            "validation-run",
            total_units=10,
            resume_completed_units=2,
            resume_completed_calls=3,
        ),
        clock=clock,
    )

    with pytest.raises(ProgressInputError, match="completed_units"):
        reporter.tick(completed_units=1, completed_calls=3)
    with pytest.raises(ProgressInputError, match="completed_calls"):
        reporter.tick(completed_units=2, completed_calls=2)

    clock.value = 9.0
    with pytest.raises(ProgressInputError, match="clock"):
        reporter.tick(completed_units=2, completed_calls=3)


def test_stream_contains_only_canonical_one_object_per_emission() -> None:
    stream = StringIO()
    clock = _Clock()
    reporter = ProgressReporter(
        stream,
        ProgressConfig("jsonl-run", total_units=20),
        clock=clock,
    )
    clock.advance(30.0)
    reporter.tick(completed_units=1, completed_calls=2)

    values = _lines(stream)
    assert [value["sequence"] for value in values] == [0, 1]
    assert stream.getvalue().endswith("\n")
