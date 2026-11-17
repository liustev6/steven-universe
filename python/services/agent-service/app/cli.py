"""`agent`: agent-service's command line, on CT 107, as the `agent-cli` client (control-plane.md "Callers").

    agent ask [-p PROFILE] [-c CONVERSATION | --last] TEXT    one question; the reply streams as it's written
    agent chat [-p PROFILE] [-c CONVERSATION | --last]        a conversation; Ctrl-C interrupts a reply,
                                                              Ctrl-D (or "exit") ends the session
    agent conversations                                       yours, most recent first
    agent history CONVERSATION [--events]                     its turns, across its sessions
    agent stop | agent start                                  the kill switch (chat:admin)

Installed as /usr/local/bin/agent by agent-service's deploy: a wrapper that runs this module in the
running agent-service image, with the agent-cli secret (root-only on CT 107) mounted. `ask` leaves
its session to the idle timer, so a quick `agent ask --last …` continues it in the same sandbox.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from typing import Any

import httpx

URL = os.environ.get("AGENT_URL", "https://agent.lan.liustev6.ca")
ISSUER = os.environ.get("AGENT_CLI_ISSUER", "https://auth.lan.liustev6.ca")
SECRET_FILE = os.environ.get("AGENT_CLI_SECRET_FILE", "/t/secret")
DIM, RED, RESET = ("\033[2m", "\033[31m", "\033[0m") if sys.stderr.isatty() else ("", "", "")


class CLIError(Exception):
    pass


def note(text: str) -> None:
    print(f"{DIM}{text}{RESET}", file=sys.stderr, flush=True)


def problem(r: httpx.Response) -> CLIError:
    try:
        body = r.json()
        return CLIError(f"{body.get('detail') or body} ({r.status_code} {body.get('code', '')})".strip())
    except ValueError:
        return CLIError(f"HTTP {r.status_code}: {r.text[:200]}")


# ---- rendering a turn's events ---------------------------------------------------------------------
class Printer:
    """Turns protocol B events into terminal output: the reply on stdout as it streams, everything
    else (tools, errors, the summary) dimmed on stderr."""

    def __init__(self, out=None):
        self.out = out or sys.stdout                 # looked up now, not at import: stdout may be redirected
        self.started = time.monotonic()
        self.first_text: float | None = None
        self.at_line_start = True

    def event(self, e: dict[str, Any]) -> None:
        t = e["type"]
        if t == "message.delta":
            if self.first_text is None:
                self.first_text = time.monotonic() - self.started
            self.out.write(e["text"])
            self.out.flush()
            self.at_line_start = e["text"].endswith("\n")
        elif t == "message.completed":
            self._newline()
        elif t == "tool.started":
            self._newline()
            note(f"  ⚙ {e['name']} {json.dumps(e.get('input', {}))[:160]}")
        elif t == "tool.completed":
            note(f"  {'✗' if e.get('is_error') else '✓'} {e.get('output', '')[:160].strip()}")
        elif t == "task.started":
            note(f"  ↳ task: {e.get('description') or e['task_id']}")
        elif t == "error":
            self._newline()
            print(f"{RED}error ({e.get('code')}): {e.get('message')}{RESET}", file=sys.stderr, flush=True)
        elif t == "session.status" and e.get("status") == "ended":
            self._newline()
            note(f"(the session ended: {e.get('reason')})")
        elif t == "turn.completed":
            self._newline()
            note(self.summary(e))

    def summary(self, e: dict[str, Any]) -> str:
        parts = [e.get("status", "?")]
        if e.get("reason"):
            parts.append(e["reason"])
        parts.append(f"{time.monotonic() - self.started:.1f} s")
        if self.first_text is not None:
            parts.append(f"first text {self.first_text:.1f} s")
        usage = e.get("usage") or {}
        if usage.get("total_tokens") is not None:
            parts.append(f"{usage['total_tokens']:,} tokens")
        return "(" + " · ".join(parts) + ")"

    def _newline(self) -> None:
        if not self.at_line_start:
            self.out.write("\n")
            self.out.flush()
            self.at_line_start = True


async def read_events(response: httpx.Response):
    """Server-Sent Events → protocol B events (comments, the keep-alives, skipped)."""
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "" and data:
            yield json.loads("\n".join(data))
            data = []


# ---- the client ------------------------------------------------------------------------------------
class Agent:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def call(self, method: str, path: str, **kw) -> dict[str, Any]:
        r = await self.http.request(method, path, **kw)
        if r.status_code >= 400:
            raise problem(r)
        return r.json()

    async def session(self, profile: str, conversation: str | None, last: bool) -> dict[str, Any]:
        if last:
            mine = (await self.call("GET", "/v1/conversations", params={"limit": 50}))["conversations"]
            match = [c for c in mine if c["profile"] == profile]
            if not match:
                raise CLIError(f"no earlier conversation with the profile {profile!r}")
            conversation = match[0]["conversation_id"]
        body = {"profile": profile} | ({"conversation_id": conversation} if conversation else {})
        r = await self.http.post("/v1/sessions", json=body)
        if r.status_code >= 400:
            raise problem(r)
        s = r.json()
        note(f"({'new session' if r.status_code == 201 else 'continuing session'} {s['session_id'][:8]} · "
             f"conversation {s['conversation_id']})")
        return s

    async def turn(self, session_id: str, text: str, printer: Printer) -> dict[str, Any] | None:
        """One turn, streamed. Ctrl-C asks agent-service to interrupt it; the turn still ends
        properly (turn.completed, status interrupted). A second Ctrl-C stops waiting."""
        loop = asyncio.get_running_loop()
        interrupted = asyncio.Event()

        def on_sigint() -> None:
            if interrupted.is_set():
                raise KeyboardInterrupt
            interrupted.set()
            note("\n(interrupting… Ctrl-C again to stop waiting)")
            loop.create_task(self.http.post(f"/v1/sessions/{session_id}/cancel"))

        loop.add_signal_handler(signal.SIGINT, on_sigint)
        try:
            async with self.http.stream("POST", f"/v1/sessions/{session_id}/turns", json={"text": text}) as r:
                if r.status_code >= 400:
                    await r.aread()
                    raise problem(r)
                async for e in read_events(r):
                    printer.event(e)
                    if e["type"] == "turn.completed" or (e["type"] == "session.status" and e.get("status") == "ended"):
                        return e
        finally:
            loop.remove_signal_handler(signal.SIGINT)
        return None


# ---- commands --------------------------------------------------------------------------------------
async def ask(agent: Agent, a) -> int:
    s = await agent.session(a.profile, a.conversation, a.last)
    end = await agent.turn(s["session_id"], " ".join(a.text), Printer())
    return 0 if end and end.get("status") == "completed" else 1


async def chat(agent: Agent, a) -> int:
    s = await agent.session(a.profile, a.conversation, a.last)
    note("(Ctrl-C interrupts a reply; Ctrl-D or 'exit' ends the session)")
    try:
        while True:
            try:
                text = (await asyncio.to_thread(input, "\nyou> ")).strip()
            except EOFError:
                break
            if text in ("exit", "quit"):
                break
            if not text:
                continue
            end = await agent.turn(s["session_id"], text, Printer())
            if end is None or end["type"] == "session.status":
                break
    finally:
        await agent.http.delete(f"/v1/sessions/{s['session_id']}")
        note(f"(session ended; continue later with: agent chat -p {a.profile} -c {s['conversation_id']})")
    return 0


async def conversations(agent: Agent, a) -> int:
    for c in (await agent.call("GET", "/v1/conversations"))["conversations"]:
        live = f" · live session {c['live_session_id'][:8]}" if c["live_session_id"] else ""
        print(f"{c['conversation_id']}  {c['profile']:<10} last active {c['last_active_at'][:16]}{live}")
    return 0


async def history(agent: Agent, a) -> int:
    body = await agent.call("GET", f"/v1/conversations/{a.conversation}/turns",
                            params={"events": "true" if a.events else "false"})
    for t in body["turns"]:
        print(f"\n[{t['started_at'][:19]} · {t['status']} · session {t['session_id'][:8]}]")
        print(f"you> {t['user_text']}")
        print(t["reply_text"] or "")
        for e in t.get("events", []):
            note(f"  {e['seq']:>4} {e['type']}")
    return 0


async def kill_switch(agent: Agent, a) -> int:
    print(json.dumps(await agent.call("POST", f"/v1/admin/{a.command}")))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent", description="agent-service's command line (as agent-cli)")
    sub = p.add_subparsers(dest="command", required=True)

    def session_args(sp):
        sp.add_argument("-p", "--profile", default="steven-ai")
        g = sp.add_mutually_exclusive_group()
        g.add_argument("-c", "--conversation", help="continue this conversation")
        g.add_argument("--last", action="store_true", help="continue your latest conversation with this profile")

    sp = sub.add_parser("ask", help="one question")
    session_args(sp)
    sp.add_argument("text", nargs="+")
    sp.set_defaults(run=ask)
    sp = sub.add_parser("chat", help="a conversation in the terminal")
    session_args(sp)
    sp.set_defaults(run=chat)
    sub.add_parser("conversations", help="your conversations").set_defaults(run=conversations)
    sp = sub.add_parser("history", help="a conversation's turns")
    sp.add_argument("conversation")
    sp.add_argument("--events", action="store_true", help="with each turn's protocol B events")
    sp.set_defaults(run=history)
    for name in ("stop", "start"):
        sub.add_parser(name, help=f"the kill switch: {name}").set_defaults(run=kill_switch)
    return p


async def run(args) -> int:
    from service_auth import TokenClient

    try:
        secret = open(SECRET_FILE).read().strip()
    except OSError as exc:
        raise CLIError(f"can't read the agent-cli secret ({SECRET_FILE}): {exc.strerror}")
    tokens = TokenClient(ISSUER, "agent-cli", secret)
    try:
        async with httpx.AsyncClient(base_url=URL, timeout=httpx.Timeout(30.0, read=600.0),
                                     auth=tokens.auth("agent-service")) as http:
            return await args.run(Agent(http), args)
    finally:
        await tokens.aclose()


def main() -> None:
    args = parser().parse_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except CLIError as exc:
        print(f"{RED}agent: {exc}{RESET}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
