"""Sign the dev gateway in with your ChatGPT account, once (model-access.md MA4).

    docker compose run --rm dev-gateway python -m agent_dev.login

Prints a URL and a one-time code: open the URL on your phone or laptop, sign in, enter the code.
Codex writes the login to CODEX_HOME/auth.json (dev/secrets/codex-home/, gitignored). Tokens are
never printed. You may need to allow device-code sign-in in ChatGPT's security settings first.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agent_dev.broker import codex_config


async def login(codex_home: Path) -> None:
    from openai_codex import AsyncCodex

    codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    async with AsyncCodex(codex_config(codex_home)) as codex:
        handle = await codex.login_chatgpt_device_code()
        print(f"\n  1. Open      {handle.verification_url}\n  2. Enter     {handle.user_code}\n", flush=True)
        print("Waiting for you to approve…", flush=True)
        done = await handle.wait()
        if not getattr(done, "success", True):
            raise SystemExit(f"sign-in failed: {getattr(done, 'error', None)}")
        account = (await codex.account()).account
        plan = getattr(getattr(account, "root", account), "plan_type", None)
        print(f"Signed in (plan: {getattr(plan, 'value', plan)}). The login is in {codex_home}/auth.json.")


def main() -> None:
    asyncio.run(login(Path(os.environ.get("DEV_CODEX_HOME", "/secrets/codex-home"))))


if __name__ == "__main__":
    main()
