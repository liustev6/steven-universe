"""Every tool, registered once (tools.md TL13): its location (grants and profiles), its MCP name, its
arguments and the function both interfaces call. `call()` is the one path a call takes, from the
REST API or from MCP: the access check, the arguments, the run, the audit line."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel
from service_auth import Principal

from app.access import may_call

audit = logging.getLogger("tools_service.audit")
LOCATION_RE = re.compile(r"^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$")


class ToolError(Exception):
    """The tool ran and failed in a way the caller should hear (a place not found, the weather
    service down). MCP returns it as a tool result with is_error (the model can correct itself);
    the API as a problem with `status`."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message, self.status = message, status


class Denied(Exception):
    """Not a tool this token may call (or no such tool: the two look the same to the caller)."""


@dataclass(frozen=True)
class Tool:
    location: str                                        # "weather/forecast": in grants and profiles
    title: str
    description: str                                     # what the model reads
    arguments: type[BaseModel]
    run: Callable[[BaseModel], Awaitable[dict]]
    read_only: bool = True
    destructive: bool = False
    api_path: str | None = None                          # its REST route, when it has one

    @property
    def mcp_name(self) -> str:
        """weather/forecast -> weather_forecast: the model's function names allow only [A-Za-z0-9_-]."""
        return self.location.replace("/", "_")


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        if not LOCATION_RE.match(tool.location):
            raise ValueError(f"bad tool location {tool.location!r}: <namespace>/<tool>, lowercase")
        if tool.location in self._tools or any(t.mcp_name == tool.mcp_name for t in self._tools.values()):
            raise ValueError(f"tool {tool.location!r} registered twice")
        self._tools[tool.location] = tool

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def get(self, location: str) -> Tool | None:
        return self._tools.get(location)

    def by_mcp_name(self, name: str) -> Tool | None:
        return next((t for t in self._tools.values() if t.mcp_name == name), None)

    def visible_to(self, principal: Principal) -> list[Tool]:
        return [t for t in self._tools.values() if may_call(principal, t.location)]


def _audit(interface: str, tool: str, principal: Principal, outcome: str, started: float, **extra) -> None:
    """One line per call: who (sub, the actor if relayed, the token), which tool, how it went. Never
    the arguments or the result: they can be personal (TL12)."""
    audit.info(json.dumps({
        "interface": interface, "tool": tool, "sub": principal.subject,
        "act": principal.actor_chain[0] if principal.actor_chain else None,
        "client": principal.client_id, "jti": principal.token_id, "outcome": outcome,
        "ms": round((time.monotonic() - started) * 1000), **extra,
    }, separators=(",", ":")))


async def call(registry: Registry, location: str, principal: Principal, raw: dict, *, interface: str) -> dict:
    """Run a tool for a caller. Raises Denied, pydantic's ValidationError, or ToolError."""
    started = time.monotonic()
    tool = registry.get(location)
    if tool is None or not may_call(principal, location):
        # A security event, not routine: the caller asked for a tool it was never shown.
        _audit(interface, location, principal, "denied", started, known=tool is not None)
        raise Denied(location)
    try:
        args = tool.arguments.model_validate(raw)
    except Exception:
        _audit(interface, location, principal, "bad_arguments", started)
        raise
    try:
        result = await tool.run(args)
    except ToolError as e:
        _audit(interface, location, principal, "error", started, status=e.status)
        raise
    except Exception:
        _audit(interface, location, principal, "failed", started)
        raise
    _audit(interface, location, principal, "ok", started)
    return result
