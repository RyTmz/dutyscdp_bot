from __future__ import annotations

import asyncio

from dutyscdp_bot.server import WebhookServer


class StubBot:
    def __init__(self, oncall_result: bool = True) -> None:
        self.oncall_result = oncall_result
        self.oncall_calls = 0

    async def trigger_oncall_duty(self) -> bool:
        self.oncall_calls += 1
        return self.oncall_result

    async def trigger_contact(self, contact_key: str) -> bool:
        return True

    async def ping_contact(self, contact_key: str) -> bool:
        return True

    async def handle_event(self, event: dict) -> None:
        return None


class _DoneFuture:
    def __init__(self, result: bool) -> None:
        self._result = result

    def result(self, timeout: int | None = None) -> bool:
        return self._result


def test_oncall_duty_endpoint_success(monkeypatch) -> None:
    bot = StubBot(oncall_result=True)
    server = WebhookServer(bot, asyncio.new_event_loop())

    def fake_run_coroutine_threadsafe(coro, loop):
        return _DoneFuture(asyncio.run(coro))

    monkeypatch.setattr("dutyscdp_bot.server.asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    status, payload = server._handle_payload("/oncall_duty", {})  # noqa: SLF001

    assert status == 200
    assert payload["status"] == "ok"
    assert bot.oncall_calls == 1


def test_oncall_duty_endpoint_conflict(monkeypatch) -> None:
    bot = StubBot(oncall_result=False)
    server = WebhookServer(bot, asyncio.new_event_loop())

    def fake_run_coroutine_threadsafe(coro, loop):
        return _DoneFuture(asyncio.run(coro))

    monkeypatch.setattr("dutyscdp_bot.server.asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    status, payload = server._handle_payload("/oncall_duty", {})  # noqa: SLF001

    assert status == 409
    assert payload["status"] == "error"
    assert bot.oncall_calls == 1
