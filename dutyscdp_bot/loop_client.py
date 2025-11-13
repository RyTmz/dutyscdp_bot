from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from typing import Any, Dict, Optional

from urllib.error import HTTPError, URLError


LOGGER = logging.getLogger(__name__)


class LoopClient:
    def __init__(self, token: str, base_url: str, team: str, *, ssl_context: Optional[ssl.SSLContext] = None) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._team = team
        self._ssl_context = ssl_context

    async def send_message(self, channel_id: str, message: str, *, root_id: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"channel_id": channel_id, "message": message}
        if root_id:
            payload["root_id"] = root_id
        return await asyncio.to_thread(self._post_json, "/api/v4/posts", payload)

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Loop-Team": self._team,
            "Content-Type": "application/json",
        }
        headers_for_log = headers.copy()
        if "Authorization" in headers_for_log:
            headers_for_log["Authorization"] = "<redacted>"
        LOGGER.info("POST %s headers=%s payload=%s", url, headers_for_log, payload)
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=self._ssl_context) as resp:  # type: ignore[arg-type]
                body = resp.read().decode("utf-8")
                LOGGER.debug("Response %s %s", getattr(resp, "status", "?"), body)
                return json.loads(body)
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            LOGGER.error(
                "HTTP error while posting to %s: %s %s", url, exc, error_body or "<empty body>"
            )
            raise
        except URLError as exc:
            LOGGER.error("Failed to reach %s: %s", url, exc)
            raise
