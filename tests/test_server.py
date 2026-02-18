from __future__ import annotations

from concurrent.futures import Future

from dutyscdp_bot.server import WebhookServer


class StubBot:
    async def trigger_contact(self, contact_key: str) -> bool:  # pragma: no cover - not used in this test file
        return True

    async def ping_contact(self, contact_key: str) -> bool:  # pragma: no cover - not used in this test file
        return True

    async def trigger_oncall_duty(self) -> bool:
        return True

    async def handle_event(self, payload: dict) -> None:  # pragma: no cover - not used in this test file
        return None


def test_duty_oncall_path_returns_ok(monkeypatch) -> None:
    def fake_run_coroutine_threadsafe(coro, loop):
        coro.close()
        future: Future[bool] = Future()
        future.set_result(True)
        return future

    monkeypatch.setattr("dutyscdp_bot.server.asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    server = WebhookServer(bot=StubBot(), loop=None)  # type: ignore[arg-type]
    status, payload = server._handle_payload("/duty_oncall", {})  # noqa: SLF001

    assert status == 200
    assert payload == {"status": "ok", "message": "On-call duty reminder started"}


def test_duty_oncall_path_returns_conflict_when_rejected(monkeypatch) -> None:
    def fake_run_coroutine_threadsafe(coro, loop):
        coro.close()
        future: Future[bool] = Future()
        future.set_result(False)
        return future

    monkeypatch.setattr("dutyscdp_bot.server.asyncio.run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    server = WebhookServer(bot=StubBot(), loop=None)  # type: ignore[arg-type]
    status, payload = server._handle_payload("/duty_oncall", {})  # noqa: SLF001

    assert status == 409
    assert payload == {
        "status": "error",
        "error": "Session already in progress or no on-call contacts available",
    }
