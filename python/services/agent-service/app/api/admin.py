"""The kill switch (C14) and the lab's status page.

    POST /v1/admin/stop     chat:admin    end every session; refuse new ones (across restarts)
    POST /v1/admin/start    chat:admin    accept sessions again
    GET  /v1/status         status:read   for api-service's public status page: counts, never ids
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from service_auth import Principal
from sqlalchemy import text

from app.sessions import Sessions

logger = logging.getLogger(__name__)


def build_router(sessions: Sessions, auth) -> APIRouter:
    router = APIRouter()
    admin = auth.require("chat:admin")

    @router.post("/v1/admin/stop")
    async def stop(p: Principal = Depends(admin)) -> dict:
        return {"stopped": True, "sessions_ended": await sessions.stop(p.subject)}

    @router.post("/v1/admin/start")
    async def start(p: Principal = Depends(admin)) -> dict:
        await sessions.resume(p.subject)
        return {"stopped": False}

    @router.get("/v1/status", dependencies=[Depends(auth.require("status:read"))])
    async def status() -> dict:
        try:
            async with sessions.db() as db:
                await db.execute(text("SELECT 1"))
            database = "ok"
            counts = await sessions.counts()
        except Exception as exc:                        # the page shows it; the details stay in our log
            logger.warning("status: database check failed (%r)", exc)
            database, counts = "down", None
        return {"ok": database == "ok" and not sessions.stopped, "stopped": sessions.stopped,
                "database": database, "sessions": counts}

    return router
