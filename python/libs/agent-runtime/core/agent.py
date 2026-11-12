"""The Codex SDK, configured from a session's profile and pointed at the gateway (agent-runtime.md).

The SDK (`openai-codex`) drives a local `codex app-server` over JSON-RPC. We give it:
- the gateway as its model provider: a custom provider whose base URL is the gateway and whose key
  is the session capability; Codex never logs in and never holds a real token (model-access MA1);
- the profile: model, effort, instructions, which built-in tools are on;
- a fresh CODEX_HOME on /tmp (read-only root, R4), no AGENTS.md from the workspace, no update
  checks or analytics (only the gateway is reachable anyway);
- its own sandbox off and no approval prompts: our container is the boundary.

`Translator` turns Codex notifications into protocol-A event payloads (core/types). It needs no
Codex process, so it's tested directly.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any

from core.types import OUTPUT_CHARS, EventType, SessionFile

Payload = dict[str, Any]
CAPABILITY_ENV = "AGENT_CAPABILITY"        # the provider's env_key: Codex sends it as `Authorization: Bearer`


def _toml(value: Any) -> str:
    """A `--config key=value` value: Codex parses it as TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)                  # a TOML basic string


def codex_overrides(session: SessionFile) -> dict[str, Any]:
    tools = set(session.profile.tools)
    return {
        "model_provider": "gateway",
        "model_providers.gateway.name": "agent gateway",
        "model_providers.gateway.base_url": f"{session.gateway_url.rstrip('/')}/model",
        "model_providers.gateway.env_key": CAPABILITY_ENV,
        "model_providers.gateway.wire_api": "responses",
        "model_providers.gateway.requires_openai_auth": False,
        "features.shell_tool": "shell" in tools,
        "features.view_image": "view_image" in tools,
        "web_search": "live" if "web_search" in tools else "disabled",
        "project_doc_max_bytes": 0,            # never read AGENTS.md from the (untrusted) workspace
        "check_for_update_on_startup": False,
        "analytics.enabled": False,
        "feedback.enabled": False,
    }


def codex_config(session: SessionFile, *, codex_home: str = "/tmp/codex-home", codex_bin: str | None = None):
    from openai_codex import CodexConfig

    os.makedirs(codex_home, mode=0o700, exist_ok=True)
    return CodexConfig(
        codex_bin=codex_bin,
        config_overrides=tuple(f"{k}={_toml(v)}" for k, v in codex_overrides(session).items()),
        env={CAPABILITY_ENV: session.capability, "CODEX_HOME": codex_home},
    )


# ---- Codex notifications -> protocol-A payloads ---------------------------------------------------

def _plain(value: Any) -> Any:
    """Enums and pydantic models (the SDK's generated types) as plain JSON values."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _capped(text: str) -> tuple[str, bool]:
    return (text, False) if len(text) <= OUTPUT_CHARS else (text[:OUTPUT_CHARS], True)


def _as_text(value: Any) -> str:
    value = _plain(value)
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _tool_call(item: Any) -> tuple[str, dict[str, Any], bool] | None:
    """(name, input, server) for an item that is a tool call; None for anything else."""
    kind = item.type
    if kind == "commandExecution":
        return "shell", {"command": _plain(item.command), "cwd": _plain(item.cwd)}, False
    if kind == "fileChange":
        return "apply_patch", {"changes": _plain(item.changes)}, False
    if kind == "mcpToolCall":
        args = _plain(item.arguments)
        return f"{item.server}.{item.tool}", args if isinstance(args, dict) else {"value": args}, False
    if kind == "dynamicToolCall":
        args = _plain(item.arguments)
        return item.tool, args if isinstance(args, dict) else {"value": args}, False
    if kind == "webSearch":
        return "web_search", {"query": _plain(item.query)}, True
    if kind == "imageView":
        return "view_image", {"path": _plain(item.path)}, False
    return None


def _tool_result(item: Any) -> tuple[str, bool]:
    """(output text, is_error) of a completed tool-call item."""
    kind, status = item.type, _plain(getattr(item, "status", None))
    failed = status in ("failed", "declined")
    if kind == "commandExecution":
        return item.aggregated_output or "", failed or item.exit_code not in (0, None)
    if kind == "mcpToolCall":
        return _as_text(item.error) if item.error else _as_text(item.result), failed or bool(item.error)
    if kind == "dynamicToolCall":
        return _as_text(item.content_items), failed or item.success is False
    if kind == "webSearch":
        return _as_text(getattr(item, "results", None) or item.query), False
    return str(status or ""), failed


class Translator:
    """Codex notifications -> protocol-A event payloads (no envelope; EventSink adds it).

    `result` holds the turn's final figures, read by the main loop for turn.completed.
    """

    def __init__(self) -> None:
        self.result: Payload | None = None
        self._usage: dict[str, Any] | None = None

    def __call__(self, notification: Any) -> list[Payload]:
        method, p = notification.method, notification.payload
        if method == "item/agentMessage/delta":
            return [{"type": EventType.TEXT_DELTA, "text": p.delta}] if p.delta else []
        if method in ("item/started", "item/completed"):
            item = getattr(p.item, "root", p.item)
            if item.type == "agentMessage":
                return [{"type": EventType.TEXT_COMPLETED, "text": item.text}] if method == "item/completed" and item.text else []
            call = _tool_call(item)
            if call is None:
                return []
            if method == "item/started":
                name, args, server = call
                return [{"type": EventType.TOOL_STARTED, "tool_use_id": item.id, "name": name, "input": args,
                         "server": server}]
            output, is_error = _tool_result(item)
            text, truncated = _capped(output)
            return [{"type": EventType.TOOL_COMPLETED, "tool_use_id": item.id, "is_error": is_error,
                     "output": text, "truncated": truncated}]
        if method == "thread/tokenUsage/updated":
            self._usage = _plain(p.token_usage)
            return [{"type": EventType.MODEL_USAGE_UPDATED, "info": {"tokens": self._usage}}]
        if method == "account/rateLimits/updated":
            snapshot = _plain(p.rate_limits)
            out = [{"type": EventType.MODEL_USAGE_UPDATED, "info": {"plan": snapshot}}]
            if snapshot.get("rate_limit_reached_type"):                 # an actual limit, not just usage
                out.append({"type": EventType.MODEL_RATE_LIMITED, "info": snapshot})
            return out
        if method == "turn/completed":
            turn = p.turn
            status = _plain(turn.status)
            self.result = {"is_error": status == "failed",
                           "subtype": turn.error.message if turn.error else None,
                           "usage": self._usage}
        return []


class Agent:
    """One Codex thread for the whole session: follow-up turns keep the conversation.

    A turn's events come from two places: the turn's own notifications, and notifications Codex
    sends outside any turn (the plan's usage and rate limits). Both feed one queue per turn.
    """

    def __init__(self, session: SessionFile, *, cwd: str = "/workspace", codex_home: str | None = None,
                 codex_bin: str | None = None):
        self._session = session
        self._cwd, self._codex_bin = cwd, codex_bin
        self._codex_home = codex_home or os.environ.get("CODEX_HOME", "/tmp/codex-home")
        self._codex = None
        self._thread = None
        self._handle = None
        self._out: asyncio.Queue | None = None     # the running turn's payloads; None between turns
        self._watcher: asyncio.Task | None = None
        self.last_result: Payload | None = None    # the last turn's final figures

    async def start(self) -> None:
        from openai_codex import ApprovalMode, AsyncCodex, Sandbox

        p = self._session.profile
        own = p.instructions or None
        self._codex = AsyncCodex(codex_config(self._session, codex_home=self._codex_home, codex_bin=self._codex_bin))
        await self._codex.__aenter__()
        self._thread = await self._codex.thread_start(
            model=p.model,
            # Codex's own instructions (a coding agent, ~10k tokens) stay unless the profile replaces them.
            base_instructions=own if not p.codex_instructions else None,
            developer_instructions=own if p.codex_instructions else None,
            ephemeral=True,                          # nothing kept on disk: history belongs to the control plane
            sandbox=Sandbox.full_access,             # our container is the boundary, not Codex's sandbox
            approval_mode=ApprovalMode.deny_all,     # never pause for an approval nobody can give
            cwd=self._cwd if os.path.isdir(self._cwd) else None,
        )
        self._watcher = asyncio.create_task(self._watch_session_notifications())

    async def _watch_session_notifications(self) -> None:
        """Notifications outside any turn (account/rateLimits/updated): forwarded into the running turn."""
        translate = Translator()
        while True:
            try:
                # The SDK's global queue (pinned version); closing the client ends this with an error.
                notification = await self._codex._client.next_notification()
            except Exception:
                return
            for payload in translate(notification):
                if self._out is not None:
                    self._out.put_nowait(payload)

    async def run(self, text: str) -> AsyncIterator[Payload]:
        assert self._thread is not None, "start() first"
        translate = Translator()
        out: asyncio.Queue = asyncio.Queue()
        done = object()
        self._handle = await self._thread.turn(text, effort=self._session.profile.effort)

        async def pump() -> None:
            try:
                async for notification in self._handle.stream():
                    for payload in translate(notification):
                        out.put_nowait(payload)
            finally:
                out.put_nowait(done)

        self._out = out
        pumping = asyncio.create_task(pump())
        try:
            while (payload := await out.get()) is not done:
                yield payload
            await pumping                            # re-raises a stream failure
        finally:
            self._out, self._handle = None, None
            pumping.cancel()
        self.last_result = translate.result

    async def interrupt(self) -> None:
        if self._handle is not None:
            await self._handle.interrupt()

    async def stop(self) -> None:
        if self._codex is not None:
            await self._codex.close()
        if self._watcher is not None:
            self._watcher.cancel()
