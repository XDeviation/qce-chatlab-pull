from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.qce import QceClient

TOKEN = "adapter-token-for-tests"


def qce_response(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": data})


@pytest.fixture
def settings() -> Settings:
    return Settings(
        qce_base_url="http://qce.test",
        qce_token="qce-token-for-tests",
        adapter_token=TOKEN,
        timeout_seconds=1,
        cache_seconds=30,
        allowlist=None,
    )


@pytest.fixture
def transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer qce-token-for-tests"
        if request.url.path == "/api/system/info":
            return qce_response({"napcat": {"online": True}})
        if request.url.path == "/api/groups":
            return qce_response(
                {
                    "groups": [{"groupCode": "42", "groupName": "Group", "memberCount": 3}],
                    "hasNext": False,
                }
            )
        if request.url.path == "/api/friends":
            return qce_response(
                {"friends": [{"uid": "u_7", "uin": 7, "nick": "Friend"}], "hasNext": False}
            )
        if request.url.path == "/api/groups/42/members":
            return qce_response([{"uin": "1", "nick": "Alice", "role": "owner"}])
        if request.url.path == "/api/messages/fetch":
            return qce_response(
                {
                    "messages": [
                        {
                            "msgId": "m1",
                            "msgTime": 100,
                            "senderUin": "1",
                            "sendNickName": "Alice",
                            "elements": [{"textElement": {"content": "hello"}}],
                        }
                    ],
                    "currentPage": 1,
                    "totalPages": 1,
                    "hasNext": False,
                }
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
async def client(
    settings: Settings, transport: httpx.MockTransport
) -> AsyncGenerator[httpx.AsyncClient]:
    qce = QceClient(settings, transport=transport)
    app = create_app(settings, qce)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as test_client:
        yield test_client
    await qce.close()


@pytest.mark.asyncio
async def test_health_and_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).status_code == 200
    unauthorized = await client.get("/sessions")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_discovery_and_pull(client: httpx.AsyncClient) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    discovery = await client.get("/sessions", headers=headers)
    assert discovery.status_code == 200
    assert discovery.json()["sessions"] == [
        {"id": "group:42", "name": "Group", "platform": "qq", "type": "group", "memberCount": 3},
        {"id": "private:u_7", "name": "Friend", "platform": "qq", "type": "private"},
    ]

    pull = await client.get(
        "/sessions/group:42/messages?format=chatlab&since=0&limit=1000", headers=headers
    )
    assert pull.status_code == 200
    body = pull.json()
    assert body["chatlab"]["version"] == "0.0.2"
    assert body["members"][0]["roles"] == [{"id": "owner"}]
    assert body["messages"][0]["content"] == "hello"
    assert body["sync"] == {"hasMore": False, "nextSince": 100}


@pytest.mark.asyncio
async def test_invalid_format_and_unknown_session(client: httpx.AsyncClient) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    invalid = await client.get("/sessions/group:42/messages?format=qce", headers=headers)
    assert invalid.status_code == 400
    missing = await client.get("/sessions/group:404/messages?format=chatlab", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_qce_newest_first_pages_are_exposed_oldest_first(settings: Settings) -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requested_pages.append(body["page"])
        if body["page"] == 1:
            return qce_response(
                {
                    "messages": [{"msgId": "newest", "msgTime": 300}],
                    "currentPage": 1,
                    "totalPages": 3,
                    "hasNext": True,
                }
            )
        assert body["page"] == 3
        return qce_response(
            {
                "messages": [{"msgId": "oldest", "msgTime": 100}],
                "currentPage": 3,
                "totalPages": 3,
                "hasNext": False,
            }
        )

    qce = QceClient(settings, transport=httpx.MockTransport(handler))
    try:
        messages, has_more = await qce.messages(
            {"type": "group", "remote_id": "42"}, since=0, limit=1
        )
    finally:
        await qce.close()

    assert [message["msgId"] for message in messages] == ["oldest"]
    assert has_more is True
    assert requested_pages == [1, 3]
