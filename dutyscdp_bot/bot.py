from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Set

from .config import BotConfig, Contact
from .loop_client import LoopClient
from .oncall_client import OnCallClient
from .utils import seconds_until, seconds_until_weekly

LOGGER = logging.getLogger(__name__)


@dataclass
class ReminderSession:
    contacts: tuple[Contact, ...]
    thread_id: str
    message_id: str
    started_at: datetime
    acknowledged: bool = False
    acknowledged_ldaps: Set[str] = field(default_factory=set)
    processed_post_ids: Set[str] = field(default_factory=set)


class DutyBot:
    _ACK_MESSAGE = "Команда принята. Хорошего рабочего дня!"
    _BOT_USERNAME = "scdp-platform-bot"
    _THREAD_POLL_INTERVAL_SECONDS = 5
    _WEEKDAY_LABELS = {
        0: "Понедельник",
        1: "Вторник",
        2: "Среда",
        3: "Четверг",
        4: "Пятница",
        5: "Суббота",
        6: "Воскресенье",
    }

    def __init__(self, config: BotConfig, client: LoopClient, oncall_client: Optional[OnCallClient] = None) -> None:
        self._config = config
        self._client = client
        self._oncall_client = oncall_client
        self._session: Optional[ReminderSession] = None
        self._session_task: Optional[asyncio.Task[None]] = None
        self._thread_poll_task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._ack_event = asyncio.Event()

    async def start(self) -> None:
        LOGGER.info(
            "Duty bot is starting. Daily notifications at %s %s. Weekly schedule report at weekday=%s time=%s",
            self._config.notification.daily_time,
            self._config.notification.timezone,
            self._config.notification.weekly_schedule_weekday,
            self._config.notification.weekly_schedule_time,
        )
        daily_task = asyncio.create_task(self._daily_notification_loop())
        weekly_task = asyncio.create_task(self._weekly_schedule_loop())
        await self._stop_event.wait()
        for task in (daily_task, weekly_task):
            task.cancel()
        for task in (daily_task, weekly_task):
            with suppress(asyncio.CancelledError):
                await task

    def stop(self) -> None:
        self._stop_event.set()

    async def _daily_notification_loop(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = seconds_until(self._config.notification.daily_time, self._config.notification.timezone)
            LOGGER.info("Next daily notification in %.0f seconds", wait_seconds)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                break
            except asyncio.TimeoutError:
                pass
            await self._notify_today()

    async def _weekly_schedule_loop(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = seconds_until_weekly(
                self._config.notification.weekly_schedule_weekday,
                self._config.notification.weekly_schedule_time,
                self._config.notification.timezone,
            )
            LOGGER.info("Next weekly schedule notification in %.0f seconds", wait_seconds)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                break
            except asyncio.TimeoutError:
                pass
            await self._notify_next_week_schedule()

    async def _notify_today(self) -> None:
        today = date.today()
        if not self._config.notification.weekends_alerts and today.weekday() >= 5:
            LOGGER.info("Skipping duty notifications on weekend (%s)", today)
            return
        oncall_contacts = await self._load_oncall_contacts()
        if oncall_contacts:
            names = ", ".join(f"{contact.full_name} ({contact.ldap})" for contact in oncall_contacts)
            LOGGER.info("Notifying current on-call contacts: %s", names)
            await self._sync_duty_group(oncall_contacts)
            await self._run_session(oncall_contacts)
            return
        contact = self._config.contact_for(today)
        if not contact:
            LOGGER.warning("No duty contact configured for %s", today)
            return
        LOGGER.info("Notifying duty contact %s (%s)", contact.full_name, contact.ldap)
        duty_contacts = [contact]
        await self._sync_duty_group(duty_contacts)
        await self._run_session(duty_contacts)

    async def _notify_next_week_schedule(self) -> None:
        next_monday = self._next_week_monday(date.today())
        next_sunday = next_monday + timedelta(days=6)
        oncall_schedule: dict[date, list[Contact]] = {}
        if self._oncall_client and self._config.oncall:
            try:
                oncall_schedule = await self._load_oncall_schedule(next_monday, next_sunday)
            except Exception:  # pragma: no cover - logged for observability
                LOGGER.exception("Failed to load next week schedule from Grafana OnCall")
                oncall_schedule = {}
        message = self._build_next_week_schedule_message(next_monday, oncall_schedule)
        await self._client.send_message(self._config.loop.channel_id, message)
        LOGGER.info("Weekly schedule message sent")

    def _build_next_week_schedule_message(self, next_monday: date, oncall_schedule: dict[date, list[Contact]]) -> str:
        next_sunday = next_monday + timedelta(days=6)
        lines = [
            "Расписание дежурств на следующую неделю:",
            f"Период: {next_monday.strftime('%d.%m.%Y')} - {next_sunday.strftime('%d.%m.%Y')}",
            "",
            "| День | Дежурный |",
            "| --- | --- |",
        ]
        for weekday in range(7):
            current_day = next_monday + timedelta(days=weekday)
            if not self._config.notification.weekends_alerts and weekday >= 5:
                continue
            contacts = oncall_schedule.get(current_day, [])
            if contacts:
                duty = ", ".join(f"{contact.full_name} (@{contact.ldap})" for contact in contacts)
            else:
                contact = self._config.contact_for(current_day)
                duty = f"{contact.full_name} (@{contact.ldap})" if contact else "Не назначен"
            lines.append(f"| {self._WEEKDAY_LABELS[weekday]} ({current_day.strftime('%d.%m')}) | {duty} |")
        return "\n".join(lines)

    def _next_week_monday(self, today: date) -> date:
        return today + timedelta(days=(7 - today.weekday()))

    async def trigger_contact(self, contact_key: str) -> bool:
        contact = self._config.contacts.get(contact_key)
        if not contact:
            LOGGER.warning("Unknown contact key %s", contact_key)
            return False
        if self._session_task and not self._session_task.done():
            LOGGER.warning("Cannot trigger %s because a reminder session is already in progress", contact_key)
            return False
        task = asyncio.create_task(self._run_session([contact]))
        self._session_task = task
        return True

    async def ping_contact(self, contact_key: str) -> bool:
        contact = self._config.contacts.get(contact_key)
        if not contact:
            LOGGER.warning("Unknown contact key %s", contact_key)
            return False
        LOGGER.info("Sending ping message to %s (%s)", contact.full_name, contact.ldap)
        await self._client.send_message(self._config.loop.channel_id, self._build_initial_message([contact]))
        LOGGER.info("Ping message for %s sent", contact_key)
        return True

    async def _sync_duty_group(self, contacts: Iterable[Contact]) -> None:
        group_id = self._config.loop.admin_group_id.strip()
        if not group_id:
            LOGGER.info("Duty group synchronization is disabled: loop.admin_group_id is not configured")
            return
        usernames = {contact.ldap for contact in contacts}
        usernames.add(self._BOT_USERNAME)
        desired_user_ids: set[str] = set()
        for username in usernames:
            try:
                profile = await self._client.get_user_by_username(username)
            except Exception:  # pragma: no cover - logged for observability
                LOGGER.exception("Failed to resolve Loop user id for username %s", username)
                continue
            user_id = str(profile.get("id", "")).strip()
            if not user_id:
                LOGGER.warning("Loop user profile for %s does not contain id", username)
                continue
            desired_user_ids.add(user_id)

        if not desired_user_ids:
            LOGGER.warning("Skipping duty group sync because no Loop user ids were resolved")
            return

        try:
            current_user_ids = await self._client.get_group_member_ids(group_id)
        except Exception:  # pragma: no cover - logged for observability
            LOGGER.exception("Failed to load members of Loop group %s", group_id)
            return

        users_to_add = sorted(desired_user_ids - current_user_ids)
        users_to_remove = sorted(current_user_ids - desired_user_ids)

        if users_to_remove:
            await self._client.remove_group_members(group_id, users_to_remove)
            LOGGER.info("Removed %d user(s) from duty group %s", len(users_to_remove), group_id)
        if users_to_add:
            await self._client.add_group_members(group_id, users_to_add)
            LOGGER.info("Added %d user(s) to duty group %s", len(users_to_add), group_id)
        if not users_to_add and not users_to_remove:
            LOGGER.info("Duty group %s is already up to date", group_id)

    async def _run_session(self, contacts: Iterable[Contact]) -> None:
        current_task = asyncio.current_task()
        if current_task:
            self._session_task = current_task
        try:
            self._session = await self._send_initial_message(contacts)
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

    async def _send_initial_message(self, contacts: Iterable[Contact]) -> ReminderSession:
        contact_list = tuple(contacts)
        response = await self._client.send_message(
            self._config.loop.channel_id, self._build_initial_message(contact_list)
        )
        message_id = response["id"]
        thread_id = response.get("root_id") or message_id
        LOGGER.debug("Initial message sent with id %s", message_id)
        session = ReminderSession(
            contacts=contact_list,
            thread_id=thread_id,
            message_id=message_id,
            started_at=datetime.utcnow(),
        )
        session.processed_post_ids.add(message_id)
        return session

    def _build_initial_message(self, contacts: Iterable[Contact]) -> str:
        contact_list = list(contacts)
        mentions = " ".join(f"@{contact.ldap}" for contact in contact_list)
        noun = "Вы сегодня дежурные и вам необходимо отвечать на сообщения в группе [lmru-scdp-platform-engineers](https://lemanapro.loop.ru/lemanapro/channels/lmru-scdp-platform-engineers), ревьють PR и первично обрабатывать новые задачи на доске" if len(contact_list) > 1 else "Ты сегодня дежурный и тебе необходимо отвечать на сообщения в группе [lmru-scdp-platform-engineers](https://lemanapro.loop.ru/lemanapro/channels/lmru-scdp-platform-engineers)"
        return f"{mentions} Доброе утро. {noun}, напишите '@scdp-platform-bot take' в данный тред для подтверждения."

    async def _reminder_loop(self) -> None:
        interval = self._config.notification.reminder_interval_minutes * 60
        while self._session and not self._session.acknowledged:
            try:
                await asyncio.wait_for(self._ack_event.wait(), timeout=interval)
                LOGGER.info("Duty notification acknowledged")
            except asyncio.TimeoutError:
                LOGGER.info("No acknowledgement yet from on-call users, sending reminder")
                await self._send_reminder()

    async def _send_reminder(self) -> None:
        if not self._session:
            return
        contacts_to_remind = self._contacts_pending_ack(self._session)
        if not contacts_to_remind:
            return
        mentions = " ".join(f"@{contact.ldap}" for contact in contacts_to_remind)
        noun = "ваша дежурная смена" if len(contacts_to_remind) > 1 else "твоя дежурная смена"
        reminder_message = f"{mentions} напомню, что сегодня {noun}. Напиши '@scdp-platform-bot take' в данном треде"
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
        has_stop_command = bool(re.search(r"\bstop\b", normalized_text))
        user = event.get("user") or {}
        if not isinstance(user, dict):
            user = {}
        if self._is_bot_author(user):
            LOGGER.debug("Ignoring bot-authored message %s", event_id or "<unknown>")
            return
        bot_is_mentioned = self._is_bot_mentioned(event, normalized_text)
        user_ldap = user.get("ldap")
        known_ldaps = {contact.ldap for contact in self._session.contacts}
        if has_stop_command:
            LOGGER.info("Received stop confirmation from %s", user_ldap)
            self._session.acknowledged_ldaps.update(known_ldaps)
            self._session.acknowledged = True
            self._ack_event.set()
            await self._client.send_message(
                self._config.loop.channel_id,
                self._ACK_MESSAGE,
                root_id=self._session.thread_id,
            )
            return
        if has_take_command and (user_ldap in known_ldaps or bot_is_mentioned):
            LOGGER.info("Received take confirmation from %s", user_ldap)
            if user_ldap in known_ldaps:
                self._session.acknowledged_ldaps.add(user_ldap)
            if all(contact.ldap in self._session.acknowledged_ldaps for contact in self._session.contacts):
                self._session.acknowledged = True
                self._ack_event.set()
            await self._client.send_message(
                self._config.loop.channel_id,
                self._ACK_MESSAGE,
                root_id=self._session.thread_id,
            )

    def _contacts_pending_ack(self, session: ReminderSession) -> list[Contact]:
        return [contact for contact in session.contacts if contact.ldap not in session.acknowledged_ldaps]

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

    async def _load_oncall_contacts(self) -> list[Contact]:
        if not self._oncall_client or not self._config.oncall:
            return []
        try:
            ldaps = await self._oncall_client.fetch_current_oncall(self._config.oncall.schedule_name, limit=2)
        except Exception:  # pragma: no cover - logged for observability
            LOGGER.exception("Failed to load on-call users from Grafana OnCall")
            return []
        if not ldaps:
            LOGGER.warning("Grafana OnCall returned no on-call users for schedule %s", self._config.oncall.schedule_name)
            return []
        if len(ldaps) < 2:
            LOGGER.warning("Grafana OnCall returned only %d on-call users", len(ldaps))
        contacts = self._map_oncall_ldaps_to_contacts(ldaps)
        if not contacts:
            LOGGER.warning("No matching contacts found for on-call LDAPs: %s", ", ".join(ldaps))
        return contacts

    async def _load_oncall_schedule(self, start_date: date, end_date: date) -> dict[date, list[Contact]]:
        if not self._oncall_client or not self._config.oncall:
            return {}
        raw_schedule = await self._oncall_client.fetch_schedule_for_period(
            self._config.oncall.schedule_name,
            start_date,
            end_date,
        )
        schedule: dict[date, list[Contact]] = {}
        for shift_day, ldaps in raw_schedule.items():
            mapped_contacts = self._map_oncall_ldaps_to_contacts(ldaps)
            if mapped_contacts:
                schedule[shift_day] = mapped_contacts
        return schedule

    def _map_oncall_ldaps_to_contacts(self, ldaps: Iterable[str]) -> list[Contact]:
        contacts: list[Contact] = []
        seen_contacts: set[str] = set()
        for oncall_ldap in ldaps:
            normalized = str(oncall_ldap).strip().lower()
            if not normalized:
                continue
            matched = []
            for contact in self._config.contacts.values():
                contact_tokens = {contact.ldap.lower()}
                if "@" in contact.ldap:
                    contact_tokens.add(contact.ldap.split("@", maxsplit=1)[0].lower())
                if contact.ldap_oncall:
                    contact_tokens.add(contact.ldap_oncall.lower())
                if normalized in contact_tokens:
                    matched.append(contact)
            for contact in matched:
                if contact.key in seen_contacts:
                    continue
                contacts.append(contact)
                seen_contacts.add(contact.key)
        return contacts

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
