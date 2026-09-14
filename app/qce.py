from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import ConfigurationError, Settings

QCE_QUERY_END_MS = 4_102_444_800_000  # 2100-01-01; keeps QCE's page cache key stable.


class QceError(RuntimeError):
    """A sanitized QCE dependency error."""


class QceClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.qce_base_url,
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._sessions: list[dict[str, Any]] = []
        self._sessions_at = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            token = self._settings.read_qce_token()
            response = await self._client.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Access-Token": token,
                    "Accept": "application/json",
                },
                **kwargs,
            )
            response.raise_for_status()
            body = response.json()
        except (ConfigurationError, httpx.HTTPError, ValueError) as error:
            raise QceError("QCE request failed") from error
        if not isinstance(body, dict) or body.get("success") is not True:
            raise QceError("QCE returned an unsuccessful response")
        return body.get("data")

    async def ready(self) -> bool:
        try:
            await self._request("GET", "/api/system/info")
        except QceError:
            return False
        return True

    async def _paged(self, path: str, key: str) -> list[dict[str, Any]]:
        page = 1
        values: list[dict[str, Any]] = []
        while page <= 100:
            data = await self._request("GET", path, params={"page": page, "limit": 999})
            if not isinstance(data, dict) or not isinstance(data.get(key), list):
                raise QceError("QCE returned an invalid session list")
            values.extend(item for item in data[key] if isinstance(item, dict))
            if not data.get("hasNext"):
                return values
            page += 1
        raise QceError("QCE session pagination exceeded the safety limit")

    async def sessions(self, *, force: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not force and now - self._sessions_at < self._settings.cache_seconds:
            return list(self._sessions)
        async with self._lock:
            now = time.monotonic()
            if not force and now - self._sessions_at < self._settings.cache_seconds:
                return list(self._sessions)
            groups, friends = await asyncio.gather(
                self._paged("/api/groups", "groups"),
                self._paged("/api/friends", "friends"),
            )
            sessions: list[dict[str, Any]] = []
            for group in groups:
                remote_id = str(group.get("groupCode", "")).strip()
                if not remote_id or (
                    self._settings.allowlist
                    and not self._settings.allowlist.permits("group", remote_id)
                ):
                    continue
                sessions.append(
                    {
                        "id": f"group:{remote_id}",
                        "remote_id": remote_id,
                        "name": str(group.get("groupName") or remote_id),
                        "platform": "qq",
                        "type": "group",
                        "memberCount": int(group.get("memberCount") or 0),
                    }
                )
            for friend in friends:
                remote_id = str(friend.get("uid") or friend.get("uin") or "").strip()
                if not remote_id or (
                    self._settings.allowlist
                    and not self._settings.allowlist.permits("friend", remote_id)
                ):
                    continue
                sessions.append(
                    {
                        "id": f"private:{remote_id}",
                        "remote_id": remote_id,
                        "name": str(friend.get("remark") or friend.get("nick") or remote_id),
                        "platform": "qq",
                        "type": "private",
                        "chat_type": int(friend.get("chatType") or 1),
                    }
                )
            sessions.sort(key=lambda item: (item["type"], item["name"].casefold(), item["id"]))
            self._sessions = sessions
            self._sessions_at = time.monotonic()
            return list(sessions)

    async def session(self, session_id: str) -> dict[str, Any] | None:
        return next((item for item in await self.sessions() if item["id"] == session_id), None)

    async def group_members(self, group_id: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/api/groups/{group_id}/members")
        if not isinstance(data, list):
            raise QceError("QCE returned an invalid member list")
        members: list[dict[str, Any]] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            platform_id = str(raw.get("uin") or raw.get("uid") or "").strip()
            if not platform_id or platform_id == "0":
                continue
            member: dict[str, Any] = {
                "platformId": platform_id,
                "accountName": str(raw.get("nick") or raw.get("nickname") or platform_id),
            }
            nickname = str(raw.get("cardName") or raw.get("card") or "").strip()
            if nickname:
                member["groupNickname"] = nickname
            role = raw.get("role")
            if role in {"owner", 4}:
                member["roles"] = [{"id": "owner"}]
            elif role in {"admin", 3}:
                member["roles"] = [{"id": "admin"}]
            members.append(member)
        return members

    async def _message_page(
        self,
        session: dict[str, Any],
        *,
        since: int,
        page: int,
        limit: int,
        force_refresh: bool,
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/api/messages/fetch",
            json={
                "peer": {
                    "chatType": 2 if session["type"] == "group" else session.get("chat_type", 1),
                    "peerUid": session["remote_id"],
                },
                "page": page,
                "limit": limit,
                "filter": {
                    "startTime": max(0, since) * 1000,
                    "endTime": QCE_QUERY_END_MS,
                },
                "forceRefresh": force_refresh,
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise QceError("QCE returned an invalid message page")
        return data

    async def messages(
        self, session: dict[str, Any], *, since: int, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        first_page = await self._message_page(
            session,
            since=since,
            page=1,
            limit=limit,
            force_refresh=since > 0,
        )
        total_pages = max(1, int(first_page.get("totalPages") or 1))
        data = first_page
        if total_pages > 1:
            # QCE orders pages newest-first. ChatLab paginates forward with
            # `since`, so expose the oldest remaining page first.
            data = await self._message_page(
                session,
                since=since,
                page=total_pages,
                limit=limit,
                force_refresh=False,
            )
        messages = [item for item in data["messages"] if isinstance(item, dict)]
        return messages, total_pages > 1
