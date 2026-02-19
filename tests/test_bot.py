from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import date, time

import pytest

from dutyscdp_bot.bot import DutyBot
from dutyscdp_bot.config import BotConfig, Contact, LoopSettings, NotificationSettings, OnCallSettings, Schedule


class StubLoopClient:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.thread_events: list[list[dict]] = []
        self.user_profiles: dict[str, dict] = {
            "alice.ldap": {"id": "u-alice"},
            "scdp-platform-bot": {"id": "u-bot"},
        }
        self.group_member_ids: set[str] = set()
        self.added_members: list[list[str]] = []
        self.removed_members: list[list[str]] = []

    async def send_message(self, channel_id: str, message: str, *, root_id: str | None = None) -> dict:
        self.messages.append({"channel_id": channel_id, "message": message, "root_id": root_id})
        response = {"id": f"msg-{len(self.messages)}"}
        if root_id:
            response["root_id"] = root_id
        return response

    async def fetch_thread_events(self, thread_id: str) -> list[dict]:
        if self.thread_events:
            return self.thread_events.pop(0)
        return []

    async def get_user_by_username(self, username: str) -> dict:
        return self.user_profiles.get(username, {})

    async def get_group_member_ids(self, group_id: str) -> set[str]:
        return set(self.group_member_ids)

    async def add_group_members(self, group_id: str, user_ids: list[str]) -> None:
        self.added_members.append(user_ids)
        self.group_member_ids.update(user_ids)

    async def remove_group_members(self, group_id: str, user_ids: list[str]) -> None:
        self.removed_members.append(user_ids)
        self.group_member_ids.difference_update(user_ids)


@pytest.fixture()
def bot_config() -> BotConfig:
    contact = Contact(key="alice", ldap="alice.ldap", full_name="Alice")
    contacts = {contact.key: contact}
    return BotConfig(
        loop=LoopSettings(
            token="t",
            channel_id="main",
            admin_group_id="channel",
            server_url="https://loop",
            team="team",
        ),
        notification=NotificationSettings(
            daily_time=time(hour=8, minute=50),
            weekly_schedule_weekday=4,
            weekly_schedule_time=time(hour=14, minute=0),
            timezone="UTC",
            reminder_interval_minutes=1,
            weekends_alerts=True,
        ),
        contacts=contacts,
        schedule=Schedule(weekday_to_contact={0: contact}),
        oncall=None,
    )


@pytest.fixture()
def dual_contacts_config() -> BotConfig:
    alice = Contact(key="alice", ldap="alice.ldap", full_name="Alice")
    bob = Contact(key="bob", ldap="bob.ldap", full_name="Bob")
    contacts = {alice.key: alice, bob.key: bob}
    return BotConfig(
        loop=LoopSettings(
            token="t",
            channel_id="main",
            admin_group_id="channel",
            server_url="https://loop",
            team="team",
        ),
        notification=NotificationSettings(
            daily_time=time(hour=8, minute=50),
            weekly_schedule_weekday=4,
            weekly_schedule_time=time(hour=14, minute=0),
            timezone="UTC",
            reminder_interval_minutes=1,
            weekends_alerts=True,
        ),
        contacts=contacts,
        schedule=Schedule(weekday_to_contact={0: alice}),
        oncall=None,
    )


def test_trigger_contact_unknown(bot_config: BotConfig) -> None:
    async def run() -> bool:
        bot = DutyBot(bot_config, client=StubLoopClient())
        return await bot.trigger_contact("bob")

    assert not asyncio.run(run())


def test_trigger_contact_starts_session(bot_config: BotConfig) -> None:
    async def run() -> int:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        assert bot._session is not None  # noqa: SLF001 - inspecting internal state for test
        event = {
            "type": "message",
            "root_id": bot._session.thread_id,
            "user": {"ldap": bot._session.contacts[0].ldap},
            "text": "@take",
        }
        await bot.handle_event(event)
        if bot._session_task:
            await bot._session_task
        assert bot._session is None
        return len(client.messages)

    assert asyncio.run(run()) >= 1


def test_take_acknowledgement_sends_confirmation(bot_config: BotConfig) -> None:
    async def run() -> tuple[list[dict], str]:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001 - accessing test internals
        thread_id = bot._session.thread_id
        event = {
            "type": "message",
            "root_id": thread_id,
            "user": {"ldap": bot._session.contacts[0].ldap},
            "text": "@scdp-platform-bot take",
        }
        await bot.handle_event(event)
        if bot._session_task:
            await bot._session_task
        return client.messages, thread_id

    messages, thread_id = asyncio.run(run())
    assert messages[-1]["message"] == "Команда принята. Хорошего рабочего дня!"
    assert messages[-1]["root_id"] == thread_id


def test_any_user_acknowledgement_with_bot_mention(bot_config: BotConfig) -> None:
    async def run() -> list[dict]:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001 - accessing test internals
        event = {
            "type": "message",
            "root_id": bot._session.thread_id,
            "user": {"ldap": "random.user"},
            "text": "@scdp-platform-bot take",
        }
        await bot.handle_event(event)
        await asyncio.sleep(0)
        bot.stop()
        if bot._session_task:
            bot._session_task.cancel()
            with suppress(asyncio.CancelledError):
                await bot._session_task
        return client.messages

    messages = asyncio.run(run())
    assert any(message["message"] == "Команда принята. Хорошего рабочего дня!" for message in messages)


def test_bot_reminder_does_not_acknowledge_itself(bot_config: BotConfig) -> None:
    async def run() -> list[dict]:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001 - accessing test internals
        event = {
            "type": "message",
            "root_id": bot._session.thread_id,
            "user": {"username": "scdp-platform-bot", "ldap": "scdp-platform-bot"},
            "text": "@scdp-platform-bot take",
        }
        await bot.handle_event(event)
        await asyncio.sleep(0)
        bot.stop()
        if bot._session_task:
            bot._session_task.cancel()
            with suppress(asyncio.CancelledError):
                await bot._session_task
        return client.messages

    messages = asyncio.run(run())
    assert all(message["message"] != "Команда принята. Хорошего рабочего дня!" for message in messages)


def test_any_user_acknowledgement_with_structured_mentions(bot_config: BotConfig) -> None:
    async def run() -> list[dict]:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001 - accessing test internals
        event = {
            "type": "message",
            "root_id": bot._session.thread_id,
            "user": {"ldap": "random.user"},
            "text": "<@U123> take",
            "mentions": [{"username": "scdp-platform-bot"}],
        }
        await bot.handle_event(event)
        await asyncio.sleep(0)
        bot.stop()
        if bot._session_task:
            bot._session_task.cancel()
            with suppress(asyncio.CancelledError):
                await bot._session_task
        return client.messages

    messages = asyncio.run(run())
    assert any(message["message"] == "Команда принята. Хорошего рабочего дня!" for message in messages)


def test_any_user_acknowledgement_with_props_mentions(bot_config: BotConfig) -> None:
    async def run() -> list[dict]:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001 - accessing test internals
        event = {
            "type": "message",
            "root_id": bot._session.thread_id,
            "user": {"ldap": "random.user"},
            "text": "take",
            "props": {"mention_keys": ["@alice", "@scdp-platform-bot"]},
        }
        await bot.handle_event(event)
        await asyncio.sleep(0)
        bot.stop()
        if bot._session_task:
            bot._session_task.cancel()
            with suppress(asyncio.CancelledError):
                await bot._session_task
        return client.messages

    messages = asyncio.run(run())
    assert any(message["message"] == "Команда принята. Хорошего рабочего дня!" for message in messages)


def test_acknowledgement_outside_thread(bot_config: BotConfig) -> None:
    async def run() -> list[dict]:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001 - accessing test internals
        event = {
            "type": "message",
            "id": "msg-root",
            "root_id": "msg-root",
            "user": {"ldap": bot._session.contacts[0].ldap},
            "text": "@take",
        }
        await bot.handle_event(event)
        if bot._session_task:
            await bot._session_task
        return client.messages

    messages = asyncio.run(run())
    assert any(message["message"] == "Команда принята. Хорошего рабочего дня!" for message in messages)


def test_trigger_contact_rejects_when_session_active(bot_config: BotConfig) -> None:
    async def run() -> bool:
        bot = DutyBot(bot_config, client=StubLoopClient())
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        second = await bot.trigger_contact("alice")
        if bot._session:
            await bot.handle_event(
                {
                    "type": "message",
                    "root_id": bot._session.thread_id,
                    "user": {"ldap": bot._session.contacts[0].ldap},
                    "text": "@take",
                }
            )
            if bot._session_task:
                await bot._session_task
        return second

    assert not asyncio.run(run())


def test_ping_contact_unknown(bot_config: BotConfig) -> None:
    async def run() -> bool:
        bot = DutyBot(bot_config, client=StubLoopClient())
        return await bot.ping_contact("bob")

    assert not asyncio.run(run())


def test_ping_contact_sends_message(bot_config: BotConfig) -> None:
    async def run() -> list[dict]:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        assert await bot.ping_contact("alice")
        return client.messages

    messages = asyncio.run(run())
    assert len(messages) == 1
    assert "@alice.ldap" in messages[0]["message"]


def test_notify_today_skips_weekend(bot_config: BotConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return cls(2024, 6, 1)

    async def run() -> list[dict]:
        client = StubLoopClient()
        notification = NotificationSettings(
            daily_time=bot_config.notification.daily_time,
            weekly_schedule_weekday=bot_config.notification.weekly_schedule_weekday,
            weekly_schedule_time=bot_config.notification.weekly_schedule_time,
            timezone=bot_config.notification.timezone,
            reminder_interval_minutes=bot_config.notification.reminder_interval_minutes,
            weekends_alerts=False,
        )
        config = BotConfig(
            loop=bot_config.loop,
            notification=notification,
            contacts=bot_config.contacts,
            schedule=bot_config.schedule,
            oncall=bot_config.oncall,
        )
        bot = DutyBot(config, client=client)
        monkeypatch.setattr("dutyscdp_bot.bot.date", FakeDate)
        await bot._notify_today()
        return client.messages

    messages = asyncio.run(run())
    assert messages == []


def test_polling_detects_acknowledgement(bot_config: BotConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> list[dict]:
        client = StubLoopClient()
        bot = DutyBot(bot_config, client=client)
        monkeypatch.setattr(DutyBot, "_THREAD_POLL_INTERVAL_SECONDS", 0.01)
        assert await bot.trigger_contact("alice")
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001
        thread_id = bot._session.thread_id
        client.thread_events.append(
            [
                {
                    "type": "message",
                    "id": "msg-poll",
                    "root_id": thread_id,
                    "user": {"ldap": bot._session.contacts[0].ldap},
                    "text": "@take",
                }
            ]
        )
        await asyncio.sleep(0.05)
        if bot._session_task:
            await bot._session_task
        return client.messages

    messages = asyncio.run(run())
    assert any(message["message"] == "Команда принята. Хорошего рабочего дня!" for message in messages)


def test_notify_today_syncs_duty_group(bot_config: BotConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return cls(2024, 3, 4)

    async def run() -> tuple[list[list[str]], list[list[str]]]:
        client = StubLoopClient()
        client.group_member_ids = {"u-old", "u-bot"}
        config = BotConfig(
            loop=LoopSettings(
                token=bot_config.loop.token,
                channel_id=bot_config.loop.channel_id,
                admin_group_id=bot_config.loop.admin_group_id,
                server_url=bot_config.loop.server_url,
                team=bot_config.loop.team,
            ),
            notification=bot_config.notification,
            contacts=bot_config.contacts,
            schedule=bot_config.schedule,
            oncall=bot_config.oncall,
        )
        bot = DutyBot(config, client=client)
        monkeypatch.setattr("dutyscdp_bot.bot.date", FakeDate)

        async def noop_run_session(contacts):
            return None

        monkeypatch.setattr(bot, "_run_session", noop_run_session)
        await bot._notify_today()
        return client.added_members, client.removed_members

    added_members, removed_members = asyncio.run(run())
    assert added_members == [["u-alice"]]
    assert removed_members == [["u-old"]]


def test_multi_contact_waits_for_both_acknowledgements(dual_contacts_config: BotConfig) -> None:
    async def run() -> tuple[bool, bool, int]:
        client = StubLoopClient()
        bot = DutyBot(dual_contacts_config, client=client)
        contacts = [dual_contacts_config.contacts["alice"], dual_contacts_config.contacts["bob"]]
        session_task = asyncio.create_task(bot._run_session(contacts))  # noqa: SLF001
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001

        await bot.handle_event(
            {
                "type": "message",
                "root_id": bot._session.thread_id,
                "user": {"ldap": contacts[0].ldap},
                "text": "@scdp-platform-bot take",
            }
        )
        first_ack = bool(bot._session and bot._session.acknowledged)  # noqa: SLF001

        await bot.handle_event(
            {
                "type": "message",
                "root_id": bot._session.thread_id,
                "user": {"ldap": contacts[1].ldap},
                "text": "@scdp-platform-bot take",
            }
        )
        second_ack = bool(bot._session and bot._session.acknowledged)  # noqa: SLF001
        await session_task
        ack_messages = [message for message in client.messages if message["message"] == "Команда принята. Хорошего рабочего дня!"]
        return first_ack, second_ack, len(ack_messages)

    first_ack, second_ack, ack_count = asyncio.run(run())
    assert not first_ack
    assert second_ack
    assert ack_count == 2




def test_multi_contact_stop_acknowledges_all(dual_contacts_config: BotConfig) -> None:
    async def run() -> tuple[bool, int]:
        client = StubLoopClient()
        bot = DutyBot(dual_contacts_config, client=client)
        contacts = [dual_contacts_config.contacts["alice"], dual_contacts_config.contacts["bob"]]
        session_task = asyncio.create_task(bot._run_session(contacts))  # noqa: SLF001
        await asyncio.sleep(0)
        assert bot._session  # noqa: SLF001

        await bot.handle_event(
            {
                "type": "message",
                "root_id": bot._session.thread_id,
                "user": {"ldap": "random.user"},
                "text": "stop",
            }
        )
        acknowledged_after_stop = bool(bot._session and bot._session.acknowledged)  # noqa: SLF001
        await session_task
        ack_messages = [message for message in client.messages if message["message"] == "Команда принята. Хорошего рабочего дня!"]
        return acknowledged_after_stop, len(ack_messages)

    acknowledged_after_stop, ack_count = asyncio.run(run())
    assert acknowledged_after_stop
    assert ack_count == 1

def test_multi_contact_reminder_mentions_only_unacknowledged(dual_contacts_config: BotConfig) -> None:
    async def run() -> str:
        client = StubLoopClient()
        bot = DutyBot(dual_contacts_config, client=client)
        contacts = [dual_contacts_config.contacts["alice"], dual_contacts_config.contacts["bob"]]
        bot._session = await bot._send_initial_message(contacts)  # noqa: SLF001
        bot._session.acknowledged_ldaps.add("alice.ldap")  # noqa: SLF001
        await bot._send_reminder()  # noqa: SLF001
        return client.messages[-1]["message"]

    reminder_message = asyncio.run(run())
    assert "@bob.ldap" in reminder_message
    assert "@alice.ldap" not in reminder_message


def test_build_next_week_schedule_message(dual_contacts_config: BotConfig) -> None:
    bot = DutyBot(dual_contacts_config, client=StubLoopClient())
    next_monday = date(2024, 3, 11)

    message = bot._build_next_week_schedule_message(next_monday, {})  # noqa: SLF001

    assert "Расписание дежурств на следующую неделю" in message
    assert "Период: 11.03.2024 - 17.03.2024" in message
    assert "| Понедельник (11.03) | Alice (@alice.ldap) |" in message


def test_build_next_week_schedule_message_skips_weekends_when_disabled(dual_contacts_config: BotConfig) -> None:
    client = StubLoopClient()
    notification = NotificationSettings(
        daily_time=dual_contacts_config.notification.daily_time,
        weekly_schedule_weekday=dual_contacts_config.notification.weekly_schedule_weekday,
        weekly_schedule_time=dual_contacts_config.notification.weekly_schedule_time,
        timezone=dual_contacts_config.notification.timezone,
        reminder_interval_minutes=dual_contacts_config.notification.reminder_interval_minutes,
        weekends_alerts=False,
    )
    config = BotConfig(
        loop=dual_contacts_config.loop,
        notification=notification,
        contacts=dual_contacts_config.contacts,
        schedule=dual_contacts_config.schedule,
        oncall=dual_contacts_config.oncall,
    )
    bot = DutyBot(config, client=client)
    message = bot._build_next_week_schedule_message(date(2024, 3, 11), {})  # noqa: SLF001

    assert "Суббота" not in message
    assert "Воскресенье" not in message


def test_notify_next_week_schedule_uses_oncall_schedule(dual_contacts_config: BotConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOnCallClient:
        async def fetch_schedule_for_period(self, schedule_name: str, start_date: date, end_date: date) -> dict[date, list[str]]:
            assert schedule_name == "Support"
            assert start_date == date(2024, 3, 11)
            assert end_date == date(2024, 3, 17)
            return {
                date(2024, 3, 11): ["alice-oncall"],
                date(2024, 3, 12): ["bob-oncall"],
            }

    class FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return cls(2024, 3, 8)

    async def run() -> list[dict]:
        client = StubLoopClient()
        alice = Contact(key="alice", ldap="alice.ldap", full_name="Alice", ldap_oncall="alice-oncall")
        bob = Contact(key="bob", ldap="bob.ldap", full_name="Bob", ldap_oncall="bob-oncall")
        contacts = {"alice": alice, "bob": bob}
        config = BotConfig(
            loop=dual_contacts_config.loop,
            notification=dual_contacts_config.notification,
            contacts=contacts,
            schedule=Schedule(weekday_to_contact={0: alice, 1: alice}),
            oncall=OnCallSettings(token="t", base_url="u", schedule_name="Support"),
        )
        bot = DutyBot(config, client=client, oncall_client=FakeOnCallClient())
        monkeypatch.setattr("dutyscdp_bot.bot.date", FakeDate)
        await bot._notify_next_week_schedule()  # noqa: SLF001
        return client.messages

    messages = asyncio.run(run())
    assert len(messages) == 1
    assert "| Понедельник (11.03) | Alice (@alice.ldap) |" in messages[0]["message"]
    assert "| Вторник (12.03) | Bob (@bob.ldap) |" in messages[0]["message"]


def test_map_oncall_ldaps_to_contacts_does_not_warn_for_extra_identifiers(bot_config: BotConfig, caplog: pytest.LogCaptureFixture) -> None:
    bot = DutyBot(bot_config, client=StubLoopClient())

    with caplog.at_level("WARNING"):
        contacts = bot._map_oncall_ldaps_to_contacts(["alice.ldap", "alice@example.com", "Unknown User (123)"])  # noqa: SLF001

    assert [contact.ldap for contact in contacts] == ["alice.ldap"]
    assert "No contact mapping for on-call ldap" not in caplog.text
