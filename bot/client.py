"""Transport. Two implementations behind one interface.

`TelegramClient` talks to the real Bot API. `DemoClient` fakes an API that
fails the way real ones do, so the resilience behaviour can be demonstrated
without a token — run the demo and watch it recover.
"""
from __future__ import annotations

import itertools
import logging
import random
from typing import Protocol

from .resilience import PermanentError, TransientError

log = logging.getLogger("bot.client")

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram is explicit about which failures are worth retrying.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class Client(Protocol):
    def get_updates(self, offset: int, timeout: int) -> list[dict]: ...
    def send_message(self, chat_id: int, text: str) -> dict: ...


class TelegramClient:
    """Real Bot API client. Translates HTTP outcomes into the two error kinds."""

    def __init__(self, token: str, session=None, timeout: float = 35.0):
        if not token:
            raise PermanentError("no bot token supplied")
        self.token = token
        self.timeout = timeout
        import requests  # imported lazily so the demo runs without it
        self.session = session or requests.Session()
        self._requests = requests

    def _call(self, method: str, **params) -> dict:
        url = API.format(token=self.token, method=method)
        try:
            r = self.session.post(url, json=params, timeout=self.timeout)
        except self._requests.exceptions.Timeout as exc:
            raise TransientError(f"timeout calling {method}") from exc
        except self._requests.exceptions.ConnectionError as exc:
            raise TransientError(f"connection error calling {method}") from exc

        if r.status_code in RETRYABLE_STATUS:
            # 429 carries retry_after; honouring it is the difference between
            # backing off and getting throttled harder.
            raise TransientError(f"{method} -> HTTP {r.status_code}")
        if r.status_code == 401:
            raise PermanentError("unauthorized — check the bot token")
        if r.status_code >= 400:
            raise PermanentError(f"{method} -> HTTP {r.status_code}: {r.text[:160]}")

        body = r.json()
        if not body.get("ok"):
            raise PermanentError(f"{method} -> {body.get('description', 'not ok')}")
        return body["result"]

    def get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        return self._call("getUpdates", offset=offset, timeout=timeout)

    def send_message(self, chat_id: int, text: str) -> dict:
        return self._call("sendMessage", chat_id=chat_id, text=text)


class DemoClient:
    """A deliberately unreliable API, so the retry path is observable.

    `failure_rate` is the chance any single call raises TransientError. The
    default makes recovery visible within a few seconds of running the demo.
    """

    MESSAGES = [
        "/start", "/help", "/status", "hello there",
        "/echo resilience is just error handling you bothered to write",
        "/status", "/help",
    ]

    def __init__(self, failure_rate: float = 0.35, seed: int | None = 11):
        self.rng = random.Random(seed)
        self.failure_rate = failure_rate
        self._ids = itertools.count(1)
        self._script = itertools.cycle(self.MESSAGES)
        self.sent: list[tuple[int, str]] = []

    def _maybe_fail(self, what: str) -> None:
        if self.rng.random() < self.failure_rate:
            kind = self.rng.choice([
                "timeout", "HTTP 502 Bad Gateway",
                "HTTP 429 Too Many Requests", "connection reset by peer",
            ])
            raise TransientError(f"{what}: {kind}")

    def get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        self._maybe_fail("getUpdates")
        uid = next(self._ids)
        return [{
            "update_id": uid,
            "message": {
                "message_id": uid,
                "chat": {"id": 4242, "type": "private"},
                "from": {"id": 4242, "first_name": "Demo"},
                "text": next(self._script),
            },
        }]

    def send_message(self, chat_id: int, text: str) -> dict:
        self._maybe_fail("sendMessage")
        self.sent.append((chat_id, text))
        log.info("sent", extra={"event": "sent", "chat": chat_id, "text": text[:60]})
        return {"message_id": len(self.sent), "chat": {"id": chat_id}}
