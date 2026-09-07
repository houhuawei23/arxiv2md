"""Low-overhead performance measurements for conversion pipelines."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any


@dataclass
class StageMetric:
    seconds: float = 0.0
    counters: dict[str, float] = field(default_factory=dict)

    def as_dict(self, total: float) -> dict[str, Any]:
        result: dict[str, Any] = {"seconds": round(self.seconds, 6)}
        result["share"] = round(self.seconds / total, 4) if total else 0.0
        result.update(self.counters)
        for key, value in self.counters.items():
            if key.endswith("_count") and self.seconds:
                result[f"{key[:-6]}_per_second"] = round(value / self.seconds, 2)
        return result


class PerformanceMonitor:
    """Collect stage timings and lightweight counters for one conversion."""

    def __init__(self) -> None:
        self.started = perf_counter()
        self.stages: dict[str, StageMetric] = {}
        self.counters: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str, **counters: float) -> Iterator[None]:
        start = perf_counter()
        try:
            yield
        finally:
            metric = self.stages.setdefault(name, StageMetric())
            metric.seconds += perf_counter() - start
            for key, value in counters.items():
                metric.counters[key] = value

    def add_counter(self, name: str, value: float) -> None:
        self.counters[name] = value

    def snapshot(self) -> dict[str, Any]:
        total = perf_counter() - self.started
        return {
            "total_seconds": round(total, 6),
            "stages": {name: metric.as_dict(total) for name, metric in self.stages.items()},
            "counters": dict(self.counters),
        }
