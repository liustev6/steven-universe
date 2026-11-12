"""agent-dev: talk to the runtime through the dev gateway, from your Mac.

    agent-dev ask "what can you do?"     stream the reply; Ctrl-C interrupts the turn
    agent-dev events [--after N]         every event so far (JSON)
    agent-dev end                        end the session (the runtime exits)

AGENT_DEV_URL (default http://localhost:8080) and AGENT_DEV_TOKEN, or AGENT_DEV_TOKEN_FILE
(default ~/.config/agent-dev/token).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx

DIM, RESET = ("\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "")


def _client() -> httpx.Client:
    token = os.environ.get("AGENT_DEV_TOKEN") or Path(
        os.environ.get("AGENT_DEV_TOKEN_FILE", Path.home() / ".config/agent-dev/token")).read_text().strip()
    return httpx.Client(base_url=os.environ.get("AGENT_DEV_URL", "http://localhost:8080"),
                        headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(10.0, read=None))


def ask(text: str) -> int:
    client = _client()
    state: dict = {"turn_id": None, "result": None, "error": None}
    t0 = time.monotonic()

    def read() -> None:
        try:
            with client.stream("POST", "/dev/turns", json={"text": text}) as r:
                if r.status_code != 200:
                    state["error"] = f"HTTP {r.status_code}: {r.read().decode()[:200]}"
                    return
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    e = json.loads(line)
                    kind = e.get("type")
                    if kind == "dev.accepted":
                        state["turn_id"] = e["turn_id"]
                    elif kind == "text.delta":
                        print(e["text"], end="", flush=True)
                    elif kind == "tool.started":
                        print(f"\n{DIM}[{e['name']}] {json.dumps(e.get('input', {}))[:120]}{RESET}", flush=True)
                    elif kind in ("task.started", "task.completed"):
                        print(f"\n{DIM}[{kind}] {e.get('description') or e.get('status', '')}{RESET}", flush=True)
                    elif kind == "runtime.error":
                        print(f"\n{DIM}error: {e.get('message')}{RESET}", flush=True)
                    elif kind == "turn.completed":
                        state["result"] = e
        except httpx.HTTPError as exc:
            state["error"] = str(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    interrupted = False
    while reader.is_alive():
        try:
            reader.join(0.1)
        except KeyboardInterrupt:
            if state["turn_id"] and not interrupted:
                interrupted = True
                client.post(f"/dev/turns/{state['turn_id']}/interrupt")
                print(f"\n{DIM}(interrupt sent){RESET}", flush=True)
            else:
                return 130
    print()
    if state["error"]:
        print(f"error: {state['error']}", file=sys.stderr)
        return 1
    res = state["result"] or {}
    print(f"{DIM}{res.get('status')} · first text {res.get('first_text_ms')} ms · "
          f"turn {res.get('duration_ms')} ms · round trip {round((time.monotonic() - t0) * 1000)} ms · "
          f"cost ${res.get('total_cost_usd')}{RESET}")
    return 0 if res.get("status") == "completed" else 2


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-dev", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ask").add_argument("text")
    sub.add_parser("events").add_argument("--after", type=int, default=-1)
    sub.add_parser("end")
    args = parser.parse_args()
    if args.cmd == "ask":
        sys.exit(ask(args.text))
    client = _client()
    if args.cmd == "events":
        print(json.dumps(client.get("/dev/events", params={"after": args.after}).json(), indent=2))
    else:
        client.post("/dev/end").raise_for_status()
        print("session ended")


if __name__ == "__main__":
    main()
