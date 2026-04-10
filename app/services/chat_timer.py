from __future__ import annotations

import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass


@dataclass
class TimerSegment:
    name: str
    category: str
    duration_ms: float


class ChatTimer:
    """Collects timing data throughout the chat message lifecycle."""

    def __init__(self) -> None:
        self._start: float = time.perf_counter()
        self._segments: list[TimerSegment] = []

    @contextmanager
    def track(self, name: str, category: str = "other"):
        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self._segments.append(TimerSegment(name=name, category=category, duration_ms=duration_ms))

    @asynccontextmanager
    async def track_async(self, name: str, category: str = "other"):
        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self._segments.append(TimerSegment(name=name, category=category, duration_ms=duration_ms))

    def record(self, name: str, category: str, duration_ms: float) -> None:
        self._segments.append(TimerSegment(name=name, category=category, duration_ms=duration_ms))

    def get_total_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000

    def get_category_total(self, category: str) -> float:
        return sum(s.duration_ms for s in self._segments if s.category == category)

    def get_segments_by_category(self, category: str) -> list[TimerSegment]:
        return [s for s in self._segments if s.category == category]

    @property
    def segments(self) -> list[TimerSegment]:
        return list(self._segments)
