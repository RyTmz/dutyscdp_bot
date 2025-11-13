from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Callable

from .bot import DutyBot


class _WebhookHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, callback: Callable[[dict], None], **kwargs):
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
        self._callback(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{\"status\": \"ok\"}")

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

    def _handle_payload(self, payload: dict) -> None:
        asyncio.run_coroutine_threadsafe(self._bot.handle_event(payload), self._loop)

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
