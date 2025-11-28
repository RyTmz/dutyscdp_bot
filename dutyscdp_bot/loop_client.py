from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from typing import Any, Dict, List, Optional

from urllib.error import HTTPError, URLError


LOGGER = logging.getLogger(__name__)


class LoopClient:
    def __init__(self, token: str, base_url: str, team: str, *, ssl_context: Optional[ssl.SSLContext] = None) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._team = team
        self._ssl_context = ssl_context
        self._user_cache: Dict[str, Dict[str, str]] = {}

    async def send_message(self, channel_id: str, message: str, *, root_id: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"channel_id": channel_id, "message": message}
        if root_id:
            payload["root_id"] = root_id
        return await asyncio.to_thread(self._post_json, "/api/v4/posts", payload)

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = self._build_headers()
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
        url = f"{self._base_url}{path}"
        LOGGER.info("POST %s payload=%s", url, payload)
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

    async def fetch_thread_events(self, thread_id: str) -> List[Dict[str, Any]]:
        thread = await asyncio.to_thread(self._get_json, f"/api/v4/posts/{thread_id}/thread")
        posts: Dict[str, Any] = thread.get("posts", {}) if isinstance(thread, dict) else {}
        order: List[str] = []
        raw_order = thread.get("order") if isinstance(thread, dict) else None
        if isinstance(raw_order, list):
            order = [str(item) for item in raw_order]
        else:
            order = sorted(posts.keys(), key=lambda key: (posts.get(key) or {}).get("create_at", 0))
        events: List[Dict[str, Any]] = []
        for post_id in order:
            post = posts.get(post_id)
            if not isinstance(post, dict):
                continue
            user_id = post.get("user_id")
            user = await self._get_user_profile(user_id) if user_id else {}
            event = {
                "type": "message",
                "id": post_id,
                "root_id": post.get("root_id") or post_id,
                "text": post.get("message", ""),
                "user": user,
                "props": post.get("props") or {},
            }
            events.append(event)
        return events

    async def _get_user_profile(self, user_id: str) -> Dict[str, str]:
        cached = self._user_cache.get(user_id)
        if cached:
            return cached
        response = await asyncio.to_thread(self._get_json, f"/api/v4/users/{user_id}")
        username = str(response.get("username", "")) if isinstance(response, dict) else ""
        ldap = ""
        if isinstance(response, dict):
            ldap = str(
                response.get("ldap_id")
                or response.get("auth_data")
                or response.get("username")
                or response.get("email", ""),
            )
        profile = {"id": user_id, "username": username, "ldap": ldap}
        self._user_cache[user_id] = profile
        return profile

    def _get_json(self, path: str) -> Dict[str, Any]:
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            headers=self._build_headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, context=self._ssl_context) as resp:  # type: ignore[arg-type]
                body = resp.read().decode("utf-8")
                LOGGER.debug("Response %s %s", getattr(resp, "status", "?"), body)
                return json.loads(body)
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            LOGGER.error("HTTP error while getting %s%s: %s %s", self._base_url, path, exc, error_body or "<empty body>")
            raise
        except URLError as exc:
            LOGGER.error("Failed to reach %s%s: %s", self._base_url, path, exc)
            raise

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Loop-Team": self._team,
        }
        return headers
