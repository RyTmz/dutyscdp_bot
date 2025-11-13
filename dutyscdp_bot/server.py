from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from threading import Thread
from typing import Callable, Tuple

from .bot import DutyBot


LOGGER = logging.getLogger(__name__)


class _WebhookHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, callback: Callable[[str, dict], Tuple[int, dict]], **kwargs):
        self._callback = callback
        super().__init__(*args, **kwargs)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        status, response = self._callback(self.path, payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return  # silence default stdout logging


class WebhookServer:
    def __init__(self, bot: DutyBot, loop: asyncio.AbstractEventLoop, host: str = "0.0.0.0", port: int = 8080) -> None:
        self._bot = bot
        self._loop = loop
        self._host = host
        self._port = port
        self._thread: Thread | None = None
        self._httpd: ThreadingHTTPServer | None = None

    def _handler_factory(self):
        def factory(*args, **kwargs):
            return _WebhookHandler(*args, callback=self._handle_payload, **kwargs)

        return factory

    def _handle_payload(self, path: str, payload: dict) -> Tuple[int, dict]:
        normalized_path = path.rstrip("/") or "/"
        LOGGER.info("Received webhook request path=%s payload=%s", normalized_path, payload)
        if normalized_path == "/trigger":
            contact_key = payload.get("contact")
            if not contact_key:
                return 400, {"status": "error", "error": "Missing contact"}
            future = asyncio.run_coroutine_threadsafe(self._bot.trigger_contact(str(contact_key)), self._loop)
            try:
                scheduled = future.result(timeout=5)
            except Exception as exc:  # pragma: no cover - propagated as server error
                LOGGER.exception("Trigger request for %s failed", contact_key)
                return 500, {"status": "error", "error": str(exc)}
            if not scheduled:
                LOGGER.warning("Trigger for %s rejected", contact_key)
                return 409, {"status": "error", "error": "Session already in progress or contact unknown"}
            LOGGER.info("Trigger for %s accepted", contact_key)
            return 200, {"status": "ok", "message": "Reminder started"}

        if normalized_path == "/ping":
            contact_key = payload.get("contact")
            if not contact_key:
                return 400, {"status": "error", "error": "Missing contact"}
            future = asyncio.run_coroutine_threadsafe(self._bot.ping_contact(str(contact_key)), self._loop)
            try:
                sent = future.result(timeout=5)
            except Exception as exc:  # pragma: no cover - propagated as server error
                LOGGER.exception("Ping request for %s failed", contact_key)
                return 500, {"status": "error", "error": str(exc)}
            if not sent:
                LOGGER.warning("Ping request for %s failed because contact is unknown", contact_key)
                return 404, {"status": "error", "error": "Contact unknown"}
            LOGGER.info("Ping request for %s completed", contact_key)
            return 200, {"status": "ok", "message": "Ping sent"}

        if normalized_path in {"/", ""}:
            asyncio.run_coroutine_threadsafe(self._bot.handle_event(payload), self._loop)
            return 200, {"status": "ok"}

        LOGGER.warning("Unknown webhook path %s", normalized_path)
        return 404, {"status": "error", "error": "Not found"}

    def start(self) -> None:
        handler = self._handler_factory()
        self._httpd = ThreadingHTTPServer((self._host, self._port), handler)
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
        if self._thread:
            self._thread.join(timeout=2)
