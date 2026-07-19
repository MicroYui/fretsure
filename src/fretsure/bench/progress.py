"""Deterministic append-only progress and ETA reporting for benchmark collection."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, TextIO, cast

from fretsure.bench.artifacts import parse_canonical_json_bytes
from fretsure.bench.contracts import (
    BenchmarkContractError,
    canonical_json_bytes,
    require_identifier,
)

BENCHMARK_PROGRESS_VERSION: Final = "benchmark-progress@0.1.0"
RECENT_WINDOW_SECONDS: Final = 15 * 60
THIRTY_MINUTE_REPORT_SECONDS: Final = 30 * 60
THROUGHPUT_REPORT_COOLDOWN_SECONDS: Final = 10 * 60
THROUGHPUT_CHANGE_RATIO: Final = 0.25

_MAX_PROGRESS_COUNT: Final = 1_000_000_000
_TRIGGER_ORDER: Final = (
    "start",
    "elapsed_30_minutes",
    "progress_5_percent",
    "throughput_change_25_percent",
)


class ProgressInputError(ValueError):
    """A deterministic progress input is invalid."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid progress {field}: {detail}")


@dataclass(frozen=True, slots=True)
class ProgressConfig:
    """Frozen run totals and the durable prefix present when this process starts."""

    run_id: str
    total_units: int
    resume_completed_units: int = 0
    resume_completed_calls: int = 0
    completed_control_rows: int = 0

    def __post_init__(self) -> None:
        try:
            require_identifier(self.run_id, path="progress.run_id")
        except BenchmarkContractError:
            raise ProgressInputError("run_id", "must be a bounded identifier") from None
        _count(self.total_units, "total_units", minimum=1)
        _count(
            self.resume_completed_units,
            "resume_completed_units",
            maximum=self.total_units,
        )
        _count(self.resume_completed_calls, "resume_completed_calls")
        _count(self.completed_control_rows, "completed_control_rows")


@dataclass(frozen=True, slots=True)
class ProgressRecord:
    """One exact JSON object already appended to the configured stream."""

    wire_json: bytes

    def __post_init__(self) -> None:
        if type(self.wire_json) is not bytes:
            raise ProgressInputError("wire_json", "must be exact bytes")
        value = parse_canonical_json_bytes(self.wire_json)
        if type(value) is not dict:
            raise ProgressInputError("wire_json", "must encode one object")

    def to_dict(self) -> dict[str, object]:
        return cast(
            dict[str, object],
            parse_canonical_json_bytes(self.wire_json),
        )


@dataclass(frozen=True, slots=True)
class _Point:
    elapsed_seconds: float
    completed_units: int
    completed_calls: int


@dataclass(frozen=True, slots=True)
class _Rates:
    sample_seconds: float
    units_per_second: float
    calls_per_second: float
    unit_delta: int
    call_delta: int


def _count(
    value: object,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = _MAX_PROGRESS_COUNT,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProgressInputError(
            field,
            f"must be an exact integer in {minimum}..{maximum}",
        )
    return value


def _clock_value(value: object) -> float:
    if type(value) not in (int, float):
        raise ProgressInputError("clock", "must return a finite nonnegative number")
    exact = float(cast(int | float, value))
    if not math.isfinite(exact) or exact < 0.0:
        raise ProgressInputError("clock", "must return a finite nonnegative number")
    return exact


def _rounded(value: float) -> float:
    return round(value, 6)


def _rate_wire(value: _Rates) -> dict[str, object]:
    return {
        "call_delta": value.call_delta,
        "calls_per_hour": _rounded(value.calls_per_second * 3600.0),
        "sample_seconds": _rounded(value.sample_seconds),
        "unit_delta": value.unit_delta,
        "units_per_hour": _rounded(value.units_per_second * 3600.0),
    }


def _eta_seconds(remaining_units: int, units_per_second: float) -> int | None:
    if remaining_units == 0:
        return 0
    if units_per_second <= 0.0:
        return None
    return math.ceil(remaining_units / units_per_second)


def _rate_changed(previous: float, current: float) -> bool:
    if previous == current:
        return False
    if previous <= 0.0:
        return current > 0.0
    return abs(current - previous) / previous >= THROUGHPUT_CHANGE_RATIO


class ProgressReporter:
    """Append deterministic progress records from absolute durable counters.

    The reporter owns no worker or timer. Callers invoke :meth:`tick` after a
    durable scheduled-unit checkpoint or from an existing monitoring loop.
    """

    def __init__(
        self,
        stream: TextIO,
        config: ProgressConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(config) is not ProgressConfig:
            raise ProgressInputError("config", "must be an exact ProgressConfig")
        if not callable(clock):
            raise ProgressInputError("clock", "must be callable")
        if not callable(getattr(stream, "write", None)) or not callable(
            getattr(stream, "flush", None)
        ):
            raise ProgressInputError("stream", "must be a writable TextIO")
        started_at = _clock_value(clock())
        self._stream = stream
        self._config = config
        self._clock = clock
        self._started_at = started_at
        self._last_clock = started_at
        self._last_units = config.resume_completed_units
        self._last_calls = config.resume_completed_calls
        self._points = [
            _Point(
                0.0,
                config.resume_completed_units,
                config.resume_completed_calls,
            )
        ]
        self._sequence = 0
        self._last_report_elapsed = 0.0
        self._thirty_minute_reported = False
        self._next_progress_step = (
            config.resume_completed_units * 20 // config.total_units + 1
        )
        self._last_record = self._emit(
            ("start",),
            elapsed_seconds=0.0,
            completed_units=config.resume_completed_units,
            completed_calls=config.resume_completed_calls,
        )

    @property
    def last_record(self) -> ProgressRecord:
        return self._last_record

    def tick(
        self,
        *,
        completed_units: int,
        completed_calls: int,
    ) -> ProgressRecord | None:
        """Observe absolute durable counters and emit only when a trigger fires."""

        units = _count(
            completed_units,
            "completed_units",
            maximum=self._config.total_units,
        )
        calls = _count(completed_calls, "completed_calls")
        if units < self._last_units:
            raise ProgressInputError("completed_units", "must be monotonic")
        if calls < self._last_calls:
            raise ProgressInputError("completed_calls", "must be monotonic")

        now = _clock_value(self._clock())
        if now < self._last_clock:
            raise ProgressInputError("clock", "must be monotonic")
        elapsed = now - self._started_at
        self._last_clock = now
        self._last_units = units
        self._last_calls = calls
        self._points.append(_Point(elapsed, units, calls))

        triggers: set[str] = set()
        if (
            not self._thirty_minute_reported
            and elapsed >= THIRTY_MINUTE_REPORT_SECONDS
        ):
            self._thirty_minute_reported = True
            triggers.add("elapsed_30_minutes")

        if (
            self._next_progress_step <= 20
            and units * 20 >= self._next_progress_step * self._config.total_units
        ):
            triggers.add("progress_5_percent")
            self._next_progress_step = units * 20 // self._config.total_units + 1

        if (
            elapsed >= 2 * RECENT_WINDOW_SECONDS
            and elapsed - self._last_report_elapsed
            >= THROUGHPUT_REPORT_COOLDOWN_SECONDS
            and self._adjacent_window_changed(elapsed, units, calls)
        ):
            triggers.add("throughput_change_25_percent")

        if not triggers:
            return None
        ordered = tuple(trigger for trigger in _TRIGGER_ORDER if trigger in triggers)
        self._last_record = self._emit(
            ordered,
            elapsed_seconds=elapsed,
            completed_units=units,
            completed_calls=calls,
        )
        return self._last_record

    def _point_at(self, elapsed_seconds: float) -> _Point:
        for point in reversed(self._points):
            if point.elapsed_seconds <= elapsed_seconds:
                return point
        return self._points[0]

    def _rates(
        self,
        *,
        elapsed_seconds: float,
        completed_units: int,
        completed_calls: int,
    ) -> tuple[_Rates, _Rates]:
        baseline = self._points[0]
        overall_seconds = elapsed_seconds
        if overall_seconds <= 0.0:
            overall = _Rates(0.0, 0.0, 0.0, 0, 0)
        else:
            overall_units = completed_units - baseline.completed_units
            overall_calls = completed_calls - baseline.completed_calls
            overall = _Rates(
                overall_seconds,
                overall_units / overall_seconds,
                overall_calls / overall_seconds,
                overall_units,
                overall_calls,
            )

        recent_seconds = min(elapsed_seconds, float(RECENT_WINDOW_SECONDS))
        if recent_seconds <= 0.0:
            recent = _Rates(0.0, 0.0, 0.0, 0, 0)
        else:
            anchor = self._point_at(elapsed_seconds - recent_seconds)
            recent_units = completed_units - anchor.completed_units
            recent_calls = completed_calls - anchor.completed_calls
            recent = _Rates(
                recent_seconds,
                recent_units / recent_seconds,
                recent_calls / recent_seconds,
                recent_units,
                recent_calls,
            )
        return overall, recent

    def _adjacent_window_changed(
        self,
        elapsed_seconds: float,
        completed_units: int,
        completed_calls: int,
    ) -> bool:
        middle = self._point_at(elapsed_seconds - RECENT_WINDOW_SECONDS)
        start = self._point_at(elapsed_seconds - 2 * RECENT_WINDOW_SECONDS)
        previous_units = (
            middle.completed_units - start.completed_units
        ) / RECENT_WINDOW_SECONDS
        current_units = (
            completed_units - middle.completed_units
        ) / RECENT_WINDOW_SECONDS
        previous_calls = (
            middle.completed_calls - start.completed_calls
        ) / RECENT_WINDOW_SECONDS
        current_calls = (
            completed_calls - middle.completed_calls
        ) / RECENT_WINDOW_SECONDS
        return _rate_changed(previous_units, current_units) or _rate_changed(
            previous_calls,
            current_calls,
        )

    def _emit(
        self,
        triggers: tuple[str, ...],
        *,
        elapsed_seconds: float,
        completed_units: int,
        completed_calls: int,
    ) -> ProgressRecord:
        overall, recent = self._rates(
            elapsed_seconds=elapsed_seconds,
            completed_units=completed_units,
            completed_calls=completed_calls,
        )
        remaining_units = self._config.total_units - completed_units
        complete = remaining_units == 0
        stalled = (
            not complete
            and elapsed_seconds >= RECENT_WINDOW_SECONDS
            and recent.unit_delta == 0
        )
        optimistic_rate = max(
            overall.units_per_second,
            recent.units_per_second,
        )
        conservative_rate = min(
            overall.units_per_second,
            recent.units_per_second,
        )
        payload: dict[str, object] = {
            "elapsed_seconds": _rounded(elapsed_seconds),
            "eta_seconds": {
                "conservative": (
                    None
                    if stalled
                    else _eta_seconds(remaining_units, conservative_rate)
                ),
                "median": _eta_seconds(
                    remaining_units,
                    overall.units_per_second,
                ),
                "optimistic": _eta_seconds(remaining_units, optimistic_rate),
            },
            "progress": {
                "completed_calls": completed_calls,
                "completed_rows": (
                    self._config.completed_control_rows + completed_units
                ),
                "completed_units": completed_units,
                "percent": _rounded(
                    completed_units * 100.0 / self._config.total_units
                ),
                "remaining_units": remaining_units,
                "resume_completed_calls": self._config.resume_completed_calls,
                "resume_completed_units": self._config.resume_completed_units,
                "segment_completed_calls": (
                    completed_calls - self._config.resume_completed_calls
                ),
                "segment_completed_units": (
                    completed_units - self._config.resume_completed_units
                ),
                "total_rows": (
                    self._config.completed_control_rows + self._config.total_units
                ),
                "total_units": self._config.total_units,
            },
            "run_id": self._config.run_id,
            "sequence": self._sequence,
            "stalled": stalled,
            "throughput": {
                "overall": _rate_wire(overall),
                "recent_15_minutes": _rate_wire(recent),
            },
            "triggers": list(triggers),
            "version": BENCHMARK_PROGRESS_VERSION,
        }
        encoded = canonical_json_bytes(payload)
        text = encoded.decode("utf-8") + "\n"
        written = self._stream.write(text)
        if written is not None and written != len(text):
            raise ProgressInputError("stream", "performed a short write")
        self._stream.flush()
        record = ProgressRecord(encoded)
        self._sequence += 1
        self._last_report_elapsed = elapsed_seconds
        return record


__all__ = [
    "BENCHMARK_PROGRESS_VERSION",
    "ProgressConfig",
    "ProgressInputError",
    "ProgressRecord",
    "ProgressReporter",
    "RECENT_WINDOW_SECONDS",
    "THIRTY_MINUTE_REPORT_SECONDS",
    "THROUGHPUT_CHANGE_RATIO",
    "THROUGHPUT_REPORT_COOLDOWN_SECONDS",
]
