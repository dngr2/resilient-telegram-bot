"""Handler routing and loop behaviour."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from bot import handlers  # noqa: E402
from bot.client import DemoClient  # noqa: E402
from bot.main import Bot  # noqa: E402
from bot.resilience import PermanentError, RetryPolicy, TransientError  # noqa: E402


def ctx(text):
    return handlers.Context(chat_id=1, text=text, args="", started_at=0.0,
                            stats={"calls": 3, "retries": 1, "recovered": 1,
                                   "failures": 0, "last_error": ""})


def test_known_commands_route():
    assert "Running" in handlers.dispatch(ctx("/start"))
    assert "/help" in handlers.dispatch(ctx("/help"))
    assert "uptime" in handlers.dispatch(ctx("/status"))


def test_echo_returns_arguments():
    assert handlers.dispatch(ctx("/echo hello world")) == "hello world"


def test_unknown_command_replies_rather_than_ignoring():
    """Silence is indistinguishable from being down."""
    assert "unknown command" in handlers.dispatch(ctx("/nope"))
    assert "not a command" in handlers.dispatch(ctx("just chatting"))


def test_handler_exception_does_not_propagate():
    @handlers.command("/boom", "raises on purpose")
    def boom(_):
        raise RuntimeError("kaboom")

    out = handlers.dispatch(ctx("/boom"))
    assert "kaboom" in out          # reported to the user...
    assert isinstance(out, str)     # ...not raised into the loop


def test_status_reports_counters():
    assert "retries  1" in handlers.dispatch(ctx("/status"))


def test_bot_survives_a_very_flaky_api():
    """The whole point: heavy transient failure, no crash.

    Note what is NOT asserted: that failures == 0. At a 50% failure rate with
    6 attempts, every attempt failing has a ~1.6% chance per call, so across
    50 calls the occasional exhausted retry is expected. The guarantee is that
    the loop absorbs it and keeps going — not that it never happens.
    """
    bot = Bot(DemoClient(failure_rate=0.5, seed=3),
              policy=RetryPolicy(attempts=6, base_delay=0, max_delay=0, jitter=False))
    stats = bot.run(max_iterations=25)
    assert bot.running is True          # completed all 25 cycles, still alive
    assert stats.retries > 0            # the retry path was genuinely exercised
    assert stats.recovered > stats.failures


def test_offset_advances_so_updates_are_not_reprocessed():
    bot = Bot(DemoClient(failure_rate=0.0, seed=1),
              policy=RetryPolicy(attempts=2, base_delay=0, jitter=False))
    bot.run(max_iterations=5)
    assert bot.offset >= 5


def test_permanent_error_stops_the_loop():
    class DeadClient:
        def get_updates(self, offset, timeout=25):
            raise PermanentError("unauthorized")

        def send_message(self, chat_id, text):
            raise PermanentError("unauthorized")

    bot = Bot(DeadClient(), policy=RetryPolicy(attempts=2, base_delay=0, jitter=False))
    with pytest.raises(PermanentError):
        bot.run(max_iterations=3)


def test_transient_exhaustion_keeps_the_bot_alive():
    """An outage longer than the backoff must not kill the process."""
    class DownClient:
        def get_updates(self, offset, timeout=25):
            raise TransientError("502")

        def send_message(self, chat_id, text):
            raise TransientError("502")

    bot = Bot(DownClient(), policy=RetryPolicy(attempts=2, base_delay=0, jitter=False))
    stats = bot.run(max_iterations=4)
    assert stats.failures == 4
    assert bot.running is True      # still standing
