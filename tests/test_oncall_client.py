from datetime import date

from dutyscdp_bot.oncall_client import OnCallClient


def test_fetch_schedule_for_period_uses_final_shifts_and_extracts_identifiers(monkeypatch):
    client = OnCallClient(token="t", base_url="https://example.com")

    def fake_resolve(schedule_name: str) -> str:
        assert schedule_name == "Support"
        return "SCHEDULE-ID"

    def fake_get_json(path: str):
        assert path == "/api/v1/schedules/SCHEDULE-ID/final_shifts?start_date=2026-02-23&end_date=2026-03-01"
        return {
            "results": [
                {
                    "user_pk": "UTVMFXZZ2EWIB",
                    "user_email": "max.ryshkevich@lemanapro.ru",
                    "user_username": "Максим Рышкевич (60116703)",
                    "shift_start": "2026-02-23T06:00:00Z",
                }
            ]
        }

    monkeypatch.setattr(client, "_resolve_schedule_id", fake_resolve)
    monkeypatch.setattr(client, "_get_json", fake_get_json)

    schedule = __import__("asyncio").run(
        client.fetch_schedule_for_period("Support", date(2026, 2, 23), date(2026, 3, 1))
    )

    assert date(2026, 2, 23) in schedule
    day_identifiers = schedule[date(2026, 2, 23)]
    assert "UTVMFXZZ2EWIB" in day_identifiers
    assert "max.ryshkevich@lemanapro.ru" in day_identifiers
    assert "max.ryshkevich" in day_identifiers
    assert "60116703" in day_identifiers
