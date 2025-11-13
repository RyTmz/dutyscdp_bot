from __future__ import annotations

import asyncio
import json
import ssl
import urllib.request
from typing import Any, Dict, Optional


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
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-Loop-Team": self._team,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, context=self._ssl_context) as resp:  # type: ignore[arg-type]
            return json.loads(resp.read().decode("utf-8"))
