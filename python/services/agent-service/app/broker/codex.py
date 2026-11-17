"""The Codex SDK calls the broker makes. Codex itself signs in and refreshes (MA2): this code never
calls OpenAI's token endpoint, so it follows whatever Codex does as that changes.

The login lives in CODEX_HOME/auth.json (a file, not the OS keyring, so the broker can read and
save it). Tokens are never printed or logged.
"""

from __future__ import annotations

from pathlib import Path

# Credentials in a file; no update checks, analytics or feedback from a server.
CODEX_OVERRIDES = ('cli_auth_credentials_store="file"', "check_for_update_on_startup=false",
                   "analytics.enabled=false", "feedback.enabled=false")


def codex_config(codex_home: Path):
    from openai_codex import CodexConfig

    return CodexConfig(config_overrides=CODEX_OVERRIDES, env={"CODEX_HOME": str(codex_home)})


async def codex_refresh(codex_home: Path) -> None:
    """A proactive refresh: Codex runs the refresh-token flow and rewrites auth.json."""
    from openai_codex import AsyncCodex

    async with AsyncCodex(codex_config(codex_home)) as codex:
        await codex.account(refresh_token=True)


async def device_login(codex_home: Path) -> str | None:
    """Sign in with a device code: prints a URL and a one-time code to approve on your phone.
    Returns the plan's name. (ChatGPT's security settings may need device-code sign-in allowed.)"""
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
        return getattr(plan, "value", plan)
