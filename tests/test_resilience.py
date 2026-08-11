"""The retry layer is the reason this repo exists, so it gets the tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from bot.resilience import (  # noqa: E402
    PermanentError, RetryPolicy, Stats, TransientError, call_with_retry,
)


def collect_sleeps():
    """Replace time.sleep so tests assert on backoff without waiting for it."""
    slept = []
    return slept, slept.append


def test_succeeds_first_try_without_sleeping():
    slept, sleep = collect_sleeps()
    stats = Stats()
    assert call_with_retry(lambda: "ok", stats=stats, sleep=sleep) == "ok"
    assert slept == []
    assert (stats.calls, stats.retries, stats.recovered) == (1, 0, 0)


def test_recovers_after_transient_failures():
    slept, sleep = collect_sleeps()
    stats = Stats()
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TransientError("502")
        return "recovered"

    assert call_with_retry(flaky, stats=stats, sleep=sleep) == "recovered"
    assert attempts["n"] == 3
    assert stats.retries == 2
    assert stats.recovered == 1
    assert len(slept) == 2


def test_permanent_error_is_not_retried():
    """A bad token retried five times is five times the log noise and zero help."""
    slept, sleep = collect_sleeps()
    stats = Stats()
    calls = {"n": 0}

    def bad_token():
        calls["n"] += 1
        raise PermanentError("unauthorized")

    with pytest.raises(PermanentError):
        call_with_retry(bad_token, stats=stats, sleep=sleep)
    assert calls["n"] == 1
    assert slept == []
    assert stats.retries == 0


def test_gives_up_after_configured_attempts():
    slept, sleep = collect_sleeps()
    stats = Stats()
    policy = RetryPolicy(attempts=4, jitter=False)

    with pytest.raises(TransientError):
        call_with_retry(lambda: (_ for _ in ()).throw(TransientError("down")),
                        policy=policy, stats=stats, sleep=sleep)
    assert len(slept) == 3          # 4 attempts => 3 waits
    assert stats.failures == 1


def test_backoff_is_exponential_and_capped():
    p = RetryPolicy(base_delay=1, multiplier=2, max_delay=10, jitter=False)
    assert [p.delay_for(i) for i in range(1, 6)] == [1, 2, 4, 8, 10]


def test_jitter_stays_within_bounds_and_varies():
    """Full jitter: never longer than the capped delay, and not a constant —
    synchronised retries are how one outage becomes a stampede."""
    p = RetryPolicy(base_delay=1, multiplier=2, max_delay=10, jitter=True)
    samples = [p.delay_for(3) for _ in range(60)]
    assert all(0 <= s <= 4 for s in samples)
    assert len(set(samples)) > 1
