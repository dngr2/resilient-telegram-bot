"""Command routing.

A dict of command -> function, so adding a command is one decorator and no
edits to the loop. Unknown input gets a reply rather than silence: a bot that
ignores you is indistinguishable from a bot that is down.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("bot.handlers")

Handler = Callable[["Context"], str]
_REGISTRY: dict[str, Handler] = {}
_HELP: dict[str, str] = {}


@dataclass
class Context:
    chat_id: int
    text: str
    args: str
    started_at: float
    stats: dict


def command(name: str, help_text: str = "") -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _REGISTRY[name] = fn
        _HELP[name] = help_text or (fn.__doc__ or "").strip()
        return fn
    return deco


@command("/start", "say hello")
def start(ctx: Context) -> str:
    return ("Running. This is a demonstration bot showing retry, backoff and "
            "structured logging.\n\nTry /status or /help.")


@command("/help", "list commands")
def help_(ctx: Context) -> str:
    return "\n".join(f"{k}  —  {v}" for k, v in sorted(_HELP.items()))


@command("/status", "uptime and failure counters")
def status(ctx: Context) -> str:
    up = int(time.time() - ctx.started_at)
    h, rem = divmod(up, 3600)
    m, s = divmod(rem, 60)
    st = ctx.stats
    return (
        f"uptime   {h:02d}:{m:02d}:{s:02d}\n"
        f"calls    {st.get('calls', 0)}\n"
        f"retries  {st.get('retries', 0)}\n"
        f"recovered {st.get('recovered', 0)}\n"
        f"failures {st.get('failures', 0)}\n"
        f"last err {st.get('last_error') or 'none'}"
    )


@command("/echo", "echo the rest of the message back")
def echo(ctx: Context) -> str:
    return ctx.args or "give me something to echo"


def dispatch(ctx: Context) -> str:
    """Route a message to its handler. Never raises — a handler bug must not
    take the process down with it."""
    cmd, _, rest = ctx.text.strip().partition(" ")
    ctx.args = rest.strip()
    fn = _REGISTRY.get(cmd.lower())
    if fn is None:
        if cmd.startswith("/"):
            return f"unknown command {cmd}. try /help"
        return "not a command — try /help"
    try:
        return fn(ctx)
    except Exception as exc:                      # noqa: BLE001 — deliberate
        log.exception("handler raised", extra={"event": "handler_error", "cmd": cmd})
        return f"that command failed: {exc}"


def registered() -> list[str]:
    return sorted(_REGISTRY)
