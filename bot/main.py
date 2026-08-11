"""The polling loop, and the shutdown that makes restarts clean.

    python -m bot.main --demo              # no token needed, fakes a flaky API
    python -m bot.main --token $BOT_TOKEN  # the real thing
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from . import handlers, logging_setup
from .client import Client, DemoClient, TelegramClient
from .resilience import PermanentError, RetryPolicy, Stats, TransientError, call_with_retry

log = logging.getLogger("bot.main")


class Bot:
    def __init__(self, client: Client, policy: RetryPolicy | None = None):
        self.client = client
        self.policy = policy or RetryPolicy()
        self.stats = Stats()
        self.offset = 0
        self.started_at = time.time()
        self.running = False

    # ---- lifecycle ----

    def install_signals(self) -> None:
        """SIGTERM is what systemd sends on restart. Catching it means the
        current message finishes instead of being cut in half."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._stop)

    def _stop(self, signum, _frame) -> None:
        log.info("shutdown requested", extra={"event": "shutdown",
                                              "signal": signal.Signals(signum).name})
        self.running = False

    # ---- work ----

    def poll_once(self) -> int:
        updates = call_with_retry(
            lambda: self.client.get_updates(self.offset, timeout=25),
            policy=self.policy, stats=self.stats, label="getUpdates",
        )
        for u in updates:
            self.offset = max(self.offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            text = msg.get("text")
            chat = (msg.get("chat") or {}).get("id")
            if not text or chat is None:
                continue
            ctx = handlers.Context(chat_id=chat, text=text, args="",
                                   started_at=self.started_at,
                                   stats=self.stats.as_dict())
            reply = handlers.dispatch(ctx)
            call_with_retry(
                lambda: self.client.send_message(chat, reply),
                policy=self.policy, stats=self.stats, label="sendMessage",
            )
        return len(updates)

    def run(self, max_iterations: int | None = None, pause: float = 0.0) -> Stats:
        self.running = True
        log.info("bot started", extra={"event": "startup",
                                       "commands": ",".join(handlers.registered())})
        n = 0
        while self.running:
            if max_iterations is not None and n >= max_iterations:
                break
            n += 1
            try:
                self.poll_once()
            except PermanentError:
                # Nothing a retry fixes. Exit non-zero so the supervisor sees it
                # rather than restart-looping forever against a bad token.
                log.critical("permanent failure, exiting",
                             extra={"event": "fatal", **self.stats.as_dict()})
                raise
            except TransientError:
                # Retries already exhausted inside call_with_retry. Stay alive:
                # an outage that outlasts the backoff should not kill the bot.
                log.error("cycle failed, continuing",
                          extra={"event": "cycle_failed", **self.stats.as_dict()})
            if pause:
                time.sleep(pause)
        log.info("bot stopped", extra={"event": "stopped", **self.stats.as_dict()})
        return self.stats


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bot", description="Resilient Telegram bot")
    p.add_argument("--token", default=os.environ.get("BOT_TOKEN", ""),
                   help="bot token (or set BOT_TOKEN)")
    p.add_argument("--demo", action="store_true",
                   help="run against a simulated flaky API — no token required")
    p.add_argument("--failure-rate", type=float, default=0.35,
                   help="demo only: chance each call fails (default 0.35)")
    p.add_argument("--iterations", type=int, default=None,
                   help="stop after N poll cycles (demo/testing)")
    p.add_argument("--pause", type=float, default=0.4, help="seconds between cycles")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--json-logs", action="store_true", help="emit JSON lines")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging_setup.configure(args.log_level, args.json_logs)

    if args.demo:
        client: Client = DemoClient(failure_rate=args.failure_rate)
        log.info("demo mode — simulated flaky API",
                 extra={"event": "demo", "failure_rate": args.failure_rate})
    else:
        if not args.token:
            print("no token: pass --token, set BOT_TOKEN, or use --demo",
                  file=sys.stderr)
            return 2
        client = TelegramClient(args.token)

    bot = Bot(client)
    bot.install_signals()
    try:
        stats = bot.run(max_iterations=args.iterations, pause=args.pause)
    except PermanentError:
        return 1
    print(f"\ncalls={stats.calls} retries={stats.retries} "
          f"recovered={stats.recovered} failures={stats.failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
