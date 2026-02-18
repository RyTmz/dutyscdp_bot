from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.parse
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

    async def get_user_by_username(self, username: str) -> Dict[str, Any]:
        encoded_username = urllib.parse.quote(username, safe="")
        response = await asyncio.to_thread(self._get_json, f"/api/v4/users/username/{encoded_username}")
        if not response:
            raise ValueError(f"Loop user {username} not found")
        return response

    async def get_group_member_ids(self, group_id: str) -> set[str]:
        path = f"/api/v4/groups/{group_id}/members"
        response = await asyncio.to_thread(self._request_json, path, None, "GET")
        members = self._extract_group_members(response, path)
        member_ids: set[str] = set()
        for member in members:
            member_id = str(member.get("user_id") or member.get("id") or "").strip()
            if member_id:
                member_ids.add(member_id)
        return member_ids

    async def add_group_members(self, group_id: str, user_ids: list[str]) -> None:
        if not user_ids:
            return
        await asyncio.to_thread(self._post_json, f"/api/v4/groups/{group_id}/members", {"user_ids": user_ids})

    async def remove_group_members(self, group_id: str, user_ids: list[str]) -> None:
        if not user_ids:
            return
        await asyncio.to_thread(self._delete_json, f"/api/v4/groups/{group_id}/members", {"user_ids": user_ids})

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


    def _extract_group_members(self, response: Any, path: str) -> List[Dict[str, Any]]:
        if isinstance(response, list):
            return [member for member in response if isinstance(member, dict)]
        if isinstance(response, dict):
            raw_members = response.get("members")
            if isinstance(raw_members, list):
                return [member for member in raw_members if isinstance(member, dict)]
        raise TypeError(f"Unexpected response type for {path}: {type(response)!r}")

    def _get_json(self, path: str) -> Dict[str, Any]:
        response = self._request_json(path, method="GET")
        if not isinstance(response, dict):
            raise TypeError(f"Unexpected response type for {path}: {type(response)!r}")
        return response

    def _get_json_list(self, path: str) -> List[Any]:
        response = self._request_json(path, method="GET")
        if not isinstance(response, list):
            raise TypeError(f"Unexpected response type for {path}: {type(response)!r}")
        return response

    def _delete_json(self, path: str, payload: Dict[str, Any]) -> Any:
        return self._request_json(path, payload=payload, method="DELETE")

    def _request_json(self, path: str, payload: Optional[Dict[str, Any]] = None, method: str = "GET") -> Any:
        data: Optional[bytes] = None
        headers = self._build_headers()
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, context=self._ssl_context) as resp:  # type: ignore[arg-type]
                body = resp.read().decode("utf-8")
                LOGGER.debug("Response %s %s", getattr(resp, "status", "?"), body)
                return json.loads(body)
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            LOGGER.error(
                "HTTP error while requesting %s%s with %s: %s %s",
                self._base_url,
                path,
                method,
                exc,
                error_body or "<empty body>",
            )
            raise
        except URLError as exc:
            LOGGER.error("Failed to reach %s%s with %s: %s", self._base_url, path, method, exc)
            raise

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Loop-Team": self._team,
        }
        return headers
