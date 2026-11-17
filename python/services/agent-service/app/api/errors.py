"""Errors as RFC 9457 problem details (`application/problem+json`): `type`, `title`, `status`,
`detail`, plus our `code` (a stable name a client can branch on)."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.sessions import (
    Busy, BudgetExceeded, Ended, NotFound, ProfileMismatch, SessionError, Stopped, UnknownProfile,
)
from app.sessions.runner import NoSandbox, RunnerError

# error class -> (HTTP status, code)
MAPPING: dict[type[Exception], tuple[int, str]] = {
    NotFound: (404, "not_found"),
    UnknownProfile: (400, "unknown_profile"),
    ProfileMismatch: (409, "profile_mismatch"),
    Busy: (409, "busy"),
    Ended: (409, "ended"),
    BudgetExceeded: (429, "budget"),
    Stopped: (503, "stopped"),
    NoSandbox: (503, "no_sandbox"),
    RunnerError: (502, "runner"),
}


def problem(status: int, code: str, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status,
                         "detail": detail, "code": code},
                        status_code=status, media_type="application/problem+json", headers=headers)


def install(app: FastAPI) -> None:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        status, code = MAPPING[type(exc)]
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, NoSandbox) else None
        detail = str(exc) if isinstance(exc, SessionError) else {
            NoSandbox: "no sandbox is free right now; try again shortly",
            RunnerError: "the sandbox runner couldn't be reached",
        }[type(exc)]
        return problem(status, code, detail, headers)

    for cls in MAPPING:
        app.add_exception_handler(cls, handle)
