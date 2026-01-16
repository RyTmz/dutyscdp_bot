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


class OnCallClient:
    def __init__(self, token: str, base_url: str, *, ssl_context: Optional[ssl.SSLContext] = None) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._ssl_context = ssl_context

    async def fetch_current_oncall(self, schedule_name: str, *, limit: int = 2) -> List[str]:
        schedule_id = await asyncio.to_thread(self._resolve_schedule_id, schedule_name)
        if not schedule_id:
            LOGGER.warning("Schedule %s not found in Grafana OnCall", schedule_name)
            return []
        users = await asyncio.to_thread(self._fetch_oncall_users, schedule_id)
        ldaps = self._extract_ldaps(users)
        if limit > 0:
            return ldaps[:limit]
        return ldaps

    def _resolve_schedule_id(self, schedule_name: str) -> str:
        payload = self._get_json("/api/v1/schedules")
        schedules = self._extract_items(payload)
        normalized = schedule_name.strip().lower()
        for schedule in schedules:
            if not isinstance(schedule, dict):
                continue
            name = str(schedule.get("name") or schedule.get("title") or schedule.get("display_name") or "").strip()
            if name.lower() == normalized:
                return str(schedule.get("id") or schedule.get("pk") or schedule.get("uid") or "")
        return ""

    def _fetch_oncall_users(self, schedule_id: str) -> List[Dict[str, Any]]:
        try:
            payload = self._get_json(f"/api/v1/schedules/{schedule_id}/on_call")
            items = self._extract_items(payload)
            if items:
                return items
        except HTTPError as exc:
            if exc.code != 404:
                raise
        query = urllib.parse.urlencode({"schedule": schedule_id})
        payload = self._get_json(f"/api/v1/on_call/?{query}")
        return self._extract_items(payload)

    def _extract_items(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("results", "data", "on_call", "oncall", "users"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _extract_ldaps(self, users: List[Dict[str, Any]]) -> List[str]:
        ldaps: List[str] = []
        seen: set[str] = set()
        for item in users:
            ldap = self._extract_ldap(item)
            if ldap and ldap not in seen:
                ldaps.append(ldap)
                seen.add(ldap)
        return ldaps

    def _extract_ldap(self, item: Dict[str, Any]) -> str:
        if "user" in item and isinstance(item["user"], dict):
            return self._extract_ldap_from_user(item["user"])
        return self._extract_ldap_from_user(item)

    def _extract_ldap_from_user(self, user: Dict[str, Any]) -> str:
        for key in ("username", "user_name", "login", "name", "email"):
            value = user.get(key)
            if value:
                return str(value)
        return ""

    def _get_json(self, path: str) -> Dict[str, Any]:
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(
            url,
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
            LOGGER.error("HTTP error while getting %s: %s %s", url, exc, error_body or "<empty body>")
            raise
        except URLError as exc:
            LOGGER.error("Failed to reach %s: %s", url, exc)
            raise

    def _build_headers(self) -> Dict[str, str]:
        return {"Authorization": self._token}
