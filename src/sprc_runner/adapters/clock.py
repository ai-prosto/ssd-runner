"""Единственный источник времени; в тестах подменяется `FakeClock`."""

import time


class SystemClock:
    """Реализация `ClockPort` поверх системных часов."""

    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()
