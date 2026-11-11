"""Resilience primitives (A5): circuit breaker and bounded exponential backoff."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


class CircuitBreaker:
    """Consecutive-failure circuit breaker.

    closed     -> calls flow; ``failure_threshold`` consecutive failures open the circuit
    open       -> calls are refused immediately for ``reset_timeout_s``
    half_open  -> exactly one trial call is let through; success closes, failure re-opens
    """

    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"
    _GAUGE = {CLOSED: 0, HALF_OPEN: 1, OPEN: 2}

    def __init__(
        self,
        name: str,
        failure_threshold: int,
        reset_timeout_s: float,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[str, str], None] | None = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._on_change = on_change
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def gauge_value(self) -> int:
        return self._GAUGE[self.state]

    def _transition(self, new_state: str) -> None:
        if new_state == self._state:
            return
        old, self._state = self._state, new_state
        log.warning("circuit breaker transition", extra={"breaker": self.name, "from": old, "to": new_state})
        if self._on_change:
            self._on_change(self.name, new_state)

    def _maybe_half_open(self) -> None:
        if self._state == self.OPEN and self._clock() - self._opened_at >= self.reset_timeout_s:
            self._trial_in_flight = False
            self._transition(self.HALF_OPEN)

    def allow(self) -> bool:
        with self._lock:
            self._maybe_half_open()
            if self._state == self.OPEN:
                return False
            if self._state == self.HALF_OPEN:
                if self._trial_in_flight:
                    return False
                self._trial_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._trial_in_flight = False
            self._transition(self.CLOSED)

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._trial_in_flight = False
            if self._state == self.HALF_OPEN or self._failures >= self.failure_threshold:
                self._opened_at = self._clock()
                self._transition(self.OPEN)


def backoff_delay(attempt: int, base_s: float, max_s: float, rng: Callable[[], float] = random.random) -> float:
    """Exponential backoff with equal jitter: half the capped delay plus a random half."""
    capped = min(max_s, base_s * (2 ** max(0, attempt - 1)))
    return capped / 2 + rng() * capped / 2
