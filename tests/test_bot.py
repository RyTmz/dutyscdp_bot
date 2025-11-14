from __future__ import annotations

import asyncio
from datetime import time

import pytest

from dutyscdp_bot.bot import DutyBot
from dutyscdp_bot.config import BotConfig, Contact, LoopSettings, NotificationSettings, Schedule


class StubLoopClient:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, channel_id: str, message: str, *, root_id: str | None = None) -> dict:
        self.messages.append({"channel_id": channel_id, "message": message, "root_id": root_id})
        response = {"id": f"msg-{len(self.messages)}"}
        if root_id:
            response["root_id"] = root_id
        return response


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
        notification=NotificationSettings(daily_time=time(hour=8, minute=50), timezone="UTC", reminder_interval_minutes=1),
        contacts=contacts,
        schedule=Schedule(weekday_to_contact={0: contact}),
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
            "user": {"ldap": bot._session.contact.ldap},
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
            "user": {"ldap": bot._session.contact.ldap},
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
        if bot._session_task:
            await bot._session_task
        return client.messages

    messages = asyncio.run(run())
    assert any(message["message"] == "Команда принята. Хорошего рабочего дня!" for message in messages)


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
        if bot._session_task:
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
        if bot._session_task:
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
            "user": {"ldap": bot._session.contact.ldap},
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
                    "user": {"ldap": bot._session.contact.ldap},
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
