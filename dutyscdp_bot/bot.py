from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

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


class DutyBot:
    def __init__(self, config: BotConfig, client: LoopClient) -> None:
        self._config = config
        self._client = client
        self._session: Optional[ReminderSession] = None
        self._session_task: Optional[asyncio.Task[None]] = None
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
            await self._reminder_loop()
        finally:
            self._session = None
            if self._session_task is current_task:
                self._session_task = None

    async def _send_initial_message(self, contact: Contact) -> ReminderSession:
        response = await self._client.send_message(
            self._config.loop.channel_id, self._build_initial_message(contact)
        )
        message_id = response["id"]
        thread_id = response.get("root_id") or message_id
        LOGGER.debug("Initial message sent with id %s", message_id)
        return ReminderSession(contact=contact, thread_id=thread_id, message_id=message_id, started_at=datetime.utcnow())

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

    async def handle_event(self, event: dict) -> None:
        if not self._session or self._session.acknowledged:
            return
        if event.get("type") != "message":
            return
        if event.get("root_id") not in {self._session.thread_id, None}:
            return
        user = event.get("user", {})
        text: str = event.get("text", "")
        if user.get("ldap") == self._session.contact.ldap and "@take" in text.lower():
            LOGGER.info("Received take confirmation from %s", user.get("ldap"))
            self._session.acknowledged = True
            self._ack_event.set()
