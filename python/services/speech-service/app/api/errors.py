"""Errors as RFC 9457 problem details (`application/problem+json`): `type`, `title`, `status`,
`detail`, plus our `code`, as agent-service's. A WebSocket refused at its handshake gets the same body."""

from __future__ import annotations

from http import HTTPStatus

from fastapi.responses import JSONResponse


def problem(status: int, code: str, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status,
                         "detail": detail, "code": code},
                        status_code=status, media_type="application/problem+json", headers=headers)
