from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Set

from .config import BotConfig, Contact
from .loop_client import LoopClient
from .utils import seconds_until

LOGGER = logging.getLogger(__name__)


@dataclass
class ReminderSession:
    contact: Contact
    thread_id: str
    message_id: str
    started_at: datetime
    acknowledged: bool = False
    processed_post_ids: Set[str] = field(default_factory=set)


class DutyBot:
    _ACK_MESSAGE = "Команда принята. Хорошего рабочего дня!"
    _BOT_USERNAME = "scdp-platform-bot"
    _THREAD_POLL_INTERVAL_SECONDS = 5

    def __init__(self, config: BotConfig, client: LoopClient) -> None:
        self._config = config
        self._client = client
        self._session: Optional[ReminderSession] = None
        self._session_task: Optional[asyncio.Task[None]] = None
        self._thread_poll_task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._ack_event = asyncio.Event()

    async def start(self) -> None:
        LOGGER.info("Duty bot is starting. Notifications scheduled at %s %s", self._config.notification.daily_time, self._config.notification.timezone)
        while not self._stop_event.is_set():
            wait_seconds = seconds_until(self._config.notification.daily_time, self._config.notification.timezone)
            LOGGER.info("Next notification in %.0f seconds", wait_seconds)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                break
            except asyncio.TimeoutError:
                pass
            await self._notify_today()

    def stop(self) -> None:
        self._stop_event.set()

    async def _notify_today(self) -> None:
        today = date.today()
        contact = self._config.contact_for(today)
        if not contact:
            LOGGER.warning("No duty contact configured for %s", today)
            return
        LOGGER.info("Notifying duty contact %s (%s)", contact.full_name, contact.ldap)
        await self._run_session(contact)

    async def trigger_contact(self, contact_key: str) -> bool:
        contact = self._config.contacts.get(contact_key)
        if not contact:
            LOGGER.warning("Unknown contact key %s", contact_key)
            return False
        if self._session_task and not self._session_task.done():
            LOGGER.warning("Cannot trigger %s because a reminder session is already in progress", contact_key)
            return False
        task = asyncio.create_task(self._run_session(contact))
        self._session_task = task
        return True

    async def ping_contact(self, contact_key: str) -> bool:
        contact = self._config.contacts.get(contact_key)
        if not contact:
            LOGGER.warning("Unknown contact key %s", contact_key)
            return False
        LOGGER.info("Sending ping message to %s (%s)", contact.full_name, contact.ldap)
        await self._client.send_message(self._config.loop.channel_id, self._build_initial_message(contact))
        LOGGER.info("Ping message for %s sent", contact_key)
        return True

    async def _run_session(self, contact: Contact) -> None:
        current_task = asyncio.current_task()
        if current_task:
            self._session_task = current_task
        try:
            self._session = await self._send_initial_message(contact)
            self._ack_event.clear()
            self._thread_poll_task = asyncio.create_task(self._poll_session_thread())
            await self._reminder_loop()
        finally:
            self._session = None
            if self._thread_poll_task:
                try:
                    await asyncio.wait_for(self._thread_poll_task, timeout=1)
                except asyncio.TimeoutError:
                    self._thread_poll_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._thread_poll_task
                self._thread_poll_task = None
            if self._session_task is current_task:
                self._session_task = None

    async def _send_initial_message(self, contact: Contact) -> ReminderSession:
        response = await self._client.send_message(
            self._config.loop.channel_id, self._build_initial_message(contact)
        )
        message_id = response["id"]
        thread_id = response.get("root_id") or message_id
        LOGGER.debug("Initial message sent with id %s", message_id)
        session = ReminderSession(
            contact=contact,
            thread_id=thread_id,
            message_id=message_id,
            started_at=datetime.utcnow(),
        )
        session.processed_post_ids.add(message_id)
        return session

    def _build_initial_message(self, contact: Contact) -> str:
        return (
            f"@{contact.ldap} Доброе утро. Ты сегодня дежурный, напиши @take в чат, чтобы я понял что ты увидел это сообщение"
        )

    async def _reminder_loop(self) -> None:
        interval = self._config.notification.reminder_interval_minutes * 60
        while self._session and not self._session.acknowledged:
            try:
                await asyncio.wait_for(self._ack_event.wait(), timeout=interval)
                LOGGER.info("%s acknowledged the duty notification", self._session.contact.ldap)
            except asyncio.TimeoutError:
                LOGGER.info("No acknowledgement yet from %s, sending reminder", self._session.contact.ldap)
                await self._send_reminder()

    async def _send_reminder(self) -> None:
        if not self._session:
            return
        reminder_message = f"@{self._session.contact.ldap} напомню, что сегодня твоя дежурная смена. Напиши @take в ответном треде"
        await self._client.send_message(self._config.loop.channel_id, reminder_message, root_id=self._session.thread_id)

    async def _poll_session_thread(self) -> None:
        while not self._stop_event.is_set():
            session = self._session
            if not session or session.acknowledged:
                break
            try:
                events = await self._client.fetch_thread_events(session.thread_id)
            except Exception:  # pragma: no cover - logged for observability
                LOGGER.exception("Failed to fetch thread %s", session.thread_id)
                events = []
            for event in events:
                if not self._session or self._session is not session:
                    break
                event_id = event.get("id")
                if event_id and event_id in session.processed_post_ids:
                    continue
                if event_id:
                    session.processed_post_ids.add(event_id)
                await self.handle_event(event)
                if not self._session or self._session.acknowledged:
                    break
            if not self._session or self._session.acknowledged:
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._THREAD_POLL_INTERVAL_SECONDS)
                break
            except asyncio.TimeoutError:
                continue

    async def handle_event(self, event: dict) -> None:
        if not self._session or self._session.acknowledged:
            return
        if event.get("type") != "message":
            return
        root_id = event.get("root_id")
        event_id = event.get("id")
        if root_id and root_id != self._session.thread_id:
            # Some Loop events set ``root_id`` to the message id itself when the
            # post is not part of a thread. In that case we still want to
            # accept the acknowledgement.
            if not event_id or root_id != event_id:
                return
        text: str = event.get("text", "")
        normalized_text = text.lower()
        has_take_command = bool(re.search(r"\btake\b", normalized_text))
        user = event.get("user") or {}
        if not isinstance(user, dict):
            user = {}
        if self._is_bot_author(user):
            LOGGER.debug("Ignoring bot-authored message %s", event_id or "<unknown>")
            return
        bot_is_mentioned = self._is_bot_mentioned(event, normalized_text)
        if has_take_command and (user.get("ldap") == self._session.contact.ldap or bot_is_mentioned):
            LOGGER.info("Received take confirmation from %s", user.get("ldap"))
            self._session.acknowledged = True
            self._ack_event.set()
            await self._client.send_message(
                self._config.loop.channel_id,
                self._ACK_MESSAGE,
                root_id=self._session.thread_id,
            )

    def _is_bot_mentioned(self, event: dict, normalized_text: str) -> bool:
        if self._BOT_USERNAME in normalized_text:
            return True
        mentions = event.get("mentions")
        if mentions and self._is_bot_listed_in_mentions(mentions):
            return True
        props = event.get("props") or {}
        mention_keys = props.get("mention_keys")
        if mention_keys and self._is_bot_listed_in_mentions(mention_keys):
            return True
        return False

    def _is_bot_author(self, user: dict) -> bool:
        username = str(user.get("username", "")).lower()
        ldap = str(user.get("ldap", "")).lower()
        return self._BOT_USERNAME in {username, ldap}

    def _is_bot_listed_in_mentions(self, mentions: object) -> bool:
        if isinstance(mentions, (list, tuple)):
            for mention in mentions:
                name = ""
                if isinstance(mention, str):
                    name = mention
                elif isinstance(mention, dict):
                    name = (
                        mention.get("name")
                        or mention.get("username")
                        or mention.get("key")
                        or mention.get("text")
                        or ""
                    )
                if self._BOT_USERNAME in name.lower():
                    return True
        return False
