from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from urllib.error import HTTPError, URLError


LOGGER = logging.getLogger(__name__)


class OnCallClient:
    def __init__(self, token: str, base_url: str, *, ssl_context: Optional[ssl.SSLContext] = None) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._ssl_context = ssl_context


    async def fetch_schedule_for_period(self, schedule_name: str, start_date: date, end_date: date) -> Dict[date, List[str]]:
        schedule_id = await asyncio.to_thread(self._resolve_schedule_id, schedule_name)
        if not schedule_id:
            LOGGER.warning("Schedule %s not found in Grafana OnCall", schedule_name)
            return {}
        return await asyncio.to_thread(self._fetch_schedule_for_period, schedule_id, start_date, end_date)

    def _fetch_schedule_for_period(self, schedule_id: str, start_date: date, end_date: date) -> Dict[date, List[str]]:
        payload = self._get_json(
            f"/api/v1/schedules/{schedule_id}/shifts?since={start_date.isoformat()}&until={end_date.isoformat()}"
        )
        items = self._extract_items(payload)
        shifts_by_day: Dict[date, List[str]] = {}
        for item in items:
            raw_start = item.get("start") or item.get("start_at") or item.get("date") or item.get("since")
            if not raw_start:
                continue
            shift_day = self._parse_date(raw_start)
            if not shift_day or shift_day < start_date or shift_day > end_date:
                continue
            usernames: List[str] = []
            for key in ("username", "user_name", "login", "name"):
                value = item.get(key)
                if value:
                    usernames.append(str(value))
                    break
            user = item.get("user")
            if isinstance(user, dict):
                username = self._extract_ldap_from_user(user)
                if username:
                    usernames.append(username)
            for key in ("user_id", "id", "pk"):
                value = item.get(key)
                if value and isinstance(value, (int, str)) and str(value).isdigit():
                    fetched = self._fetch_usernames([str(value)])
                    usernames.extend(fetched)
                    break
            unique_usernames: List[str] = []
            seen: set[str] = set()
            for username in usernames:
                normalized = username.strip()
                if normalized and normalized not in seen:
                    unique_usernames.append(normalized)
                    seen.add(normalized)
            if not unique_usernames:
                continue
            existing = shifts_by_day.setdefault(shift_day, [])
            for username in unique_usernames:
                if username not in existing:
                    existing.append(username)
        return shifts_by_day

    def _parse_date(self, value: Any) -> Optional[date]:
        raw = str(value).strip()
        if not raw:
            return None
        normalized = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            pass
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None

    async def fetch_current_oncall(self, schedule_name: str, *, limit: int = 2) -> List[str]:
        schedule_id = await asyncio.to_thread(self._resolve_schedule_id, schedule_name)
        if not schedule_id:
            LOGGER.warning("Schedule %s not found in Grafana OnCall", schedule_name)
            return []
        ldaps = await asyncio.to_thread(self._fetch_oncall_usernames, schedule_id)
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

    def _fetch_oncall_usernames(self, schedule_id: str) -> List[str]:
        payload = self._get_json(f"/api/v1/schedules/{schedule_id}")
        user_ids = self._extract_oncall_now_user_ids(payload)
        return self._fetch_usernames(user_ids)

    def _extract_oncall_now_user_ids(self, payload: Any) -> List[str]:
        if not isinstance(payload, dict):
            return []
        on_call_now = payload.get("on_call_now")
        if not on_call_now:
            return []
        items = on_call_now if isinstance(on_call_now, list) else [on_call_now]
        user_ids: List[str] = []
        seen: set[str] = set()
        for item in items:
            user_id = ""
            if isinstance(item, dict):
                user_id = str(item.get("user_id") or item.get("id") or item.get("pk") or "")
            elif isinstance(item, (int, str)):
                user_id = str(item)
            if user_id and user_id not in seen:
                user_ids.append(user_id)
                seen.add(user_id)
        return user_ids

    def _extract_items(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("results", "data", "schedules"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _fetch_usernames(self, user_ids: List[str]) -> List[str]:
        usernames: List[str] = []
        seen: set[str] = set()
        for user_id in user_ids:
            payload = self._get_json(f"/api/v1/users/{user_id}")
            if not isinstance(payload, dict):
                continue
            username = self._extract_ldap_from_user(payload)
            if username and username not in seen:
                usernames.append(username)
                seen.add(username)
        return usernames

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
