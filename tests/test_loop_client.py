from __future__ import annotations

import asyncio

from dutyscdp_bot.loop_client import LoopClient


def test_get_group_member_ids_accepts_wrapped_members_payload(monkeypatch) -> None:
    client = LoopClient(token="t", base_url="https://loop", team="team")

    def fake_request_json(path: str, payload=None, method: str = "GET"):
        assert method == "GET"
        assert path == "/api/v4/groups/group-id/members"
        return {
            "members": [
                {"id": "user-1", "username": "alice"},
                {"id": "user-2", "username": "bob"},
            ],
            "total_member_count": 2,
        }

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    member_ids = asyncio.run(client.get_group_member_ids("group-id"))

    assert member_ids == {"user-1", "user-2"}


def test_get_group_member_ids_accepts_plain_list_payload(monkeypatch) -> None:
    client = LoopClient(token="t", base_url="https://loop", team="team")

    def fake_request_json(path: str, payload=None, method: str = "GET"):
        return [{"user_id": "user-3"}, {"user_id": "user-4"}]

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    member_ids = asyncio.run(client.get_group_member_ids("group-id"))

    assert member_ids == {"user-3", "user-4"}
