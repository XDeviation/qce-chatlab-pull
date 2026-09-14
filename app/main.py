from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse

from app.config import Settings
from app.qce import QceClient, QceError
from app.transform import chatlab_document


def create_app(settings: Settings | None = None, qce: QceClient | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    resolved_qce = qce or QceClient(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await resolved_qce.close()

    app = FastAPI(
        title="QCE ChatLab Pull Adapter",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.qce = resolved_qce

    async def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            supplied, resolved_settings.adapter_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.exception_handler(QceError)
    async def qce_error_handler(_: Request, __: QceError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": "QCE dependency failed"})

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ready = await resolved_qce.ready()
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "unavailable"},
        )

    @app.get("/sessions", dependencies=[Depends(authorize)])
    async def sessions(
        keyword: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int | None, Query(ge=1, le=5000)] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        discovered = await resolved_qce.sessions()
        if keyword:
            needle = keyword.casefold()
            discovered = [
                item
                for item in discovered
                if needle in item["name"].casefold() or needle in item["id"].casefold()
            ]
        if limit is not None:
            discovered = discovered[:limit]
        public_keys = {"id", "name", "platform", "type", "memberCount", "lastMessageAt"}
        return {
            "sessions": [
                {
                    key: value
                    for key, value in item.items()
                    if key in public_keys and value is not None
                }
                for item in discovered
            ]
        }

    @app.get("/sessions/{session_id}/messages", dependencies=[Depends(authorize)])
    async def messages(
        session_id: Annotated[str, Path(min_length=3, max_length=256)],
        format_: Annotated[str, Query(alias="format")],
        since: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
    ) -> dict[str, Any]:
        if format_ != "chatlab":
            raise HTTPException(status_code=400, detail="format must be chatlab")
        session = await resolved_qce.session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        raw_messages, has_more = await resolved_qce.messages(
            session, since=since, limit=limit
        )
        include_metadata = since == 0
        members = None
        if include_metadata and session["type"] == "group":
            members = await resolved_qce.group_members(session["remote_id"])
        return chatlab_document(
            session=session,
            raw_messages=raw_messages,
            members=members,
            include_metadata=include_metadata,
            has_more=has_more,
            since=since,
        )

    return app
