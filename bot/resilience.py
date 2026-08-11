"""Retry, backoff and failure accounting for calls to flaky external services.

A bot that dies at 3am usually dies for a boring reason: one HTTP call raised,
nothing caught it, the process exited, and nothing was watching. This module is
the piece that prevents that.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, TypeVar

log = logging.getLogger("bot.resilience")

T = TypeVar("T")


class PermanentError(Exception):
    """Retrying will not help — bad token, malformed request, banned from a chat."""


class TransientError(Exception):
    """Worth retrying — timeout, 5xx, connection reset, rate limit."""


@dataclass
class RetryPolicy:
    """Exponential backoff with full jitter.

    Jitter matters more than people expect: without it, every retry across every
    worker lands in the same instant and the service that just failed gets a
    synchronised stampede the moment it comes back.
    """

    attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True

    def delay_for(self, attempt: int) -> float:
        """Delay before the given (1-based) retry."""
        raw = min(self.base_delay * (self.multiplier ** (attempt - 1)), self.max_delay)
        if not self.jitter:
            return raw
        # full jitter: uniform in [0, raw] — spreads a thundering herd flat
        return random.uniform(0, raw)


@dataclass
class Stats:
    """What actually happened, so the logs can say something useful."""

    calls: int = 0
    retries: int = 0
    failures: int = 0
    recovered: int = 0
    last_error: str = ""
    history: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "retries": self.retries,
            "failures": self.failures,
            "recovered": self.recovered,
            "last_error": self.last_error,
        }


def call_with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy | None = None,
    stats: Stats | None = None,
    label: str = "call",
    sleep: Callable[[float], None] = time.sleep,
    retry_on: Iterable[type[BaseException]] = (TransientError,),
) -> T:
    """Run `fn`, retrying transient failures with backoff.

    `PermanentError` is never retried — hammering a bad token five times just
    makes the log noisier and the ban longer.
    """
    policy = policy or RetryPolicy()
    stats = stats or Stats()
    retry_on = tuple(retry_on)
    stats.calls += 1

    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            result = fn()
            if attempt > 1:
                stats.recovered += 1
                stats.history.append(f"{label}: recovered on attempt {attempt}")
                log.info("recovered", extra={"event": "recovered", "label": label,
                                             "attempt": attempt})
            return result
        except PermanentError as exc:
            stats.failures += 1
            stats.last_error = str(exc)
            log.error("permanent failure", extra={"event": "permanent", "label": label,
                                                  "error": str(exc)})
            raise
        except retry_on as exc:
            last = exc
            stats.last_error = str(exc)
            if attempt == policy.attempts:
                break
            stats.retries += 1
            wait = policy.delay_for(attempt)
            stats.history.append(f"{label}: attempt {attempt} failed ({exc}), retry in {wait:.2f}s")
            log.warning("transient failure, retrying",
                        extra={"event": "retry", "label": label, "attempt": attempt,
                               "of": policy.attempts, "wait_s": round(wait, 2),
                               "error": str(exc)})
            sleep(wait)

    stats.failures += 1
    log.error("gave up", extra={"event": "gave_up", "label": label,
                                "attempts": policy.attempts, "error": str(last)})
    raise TransientError(f"{label} failed after {policy.attempts} attempts: {last}")
