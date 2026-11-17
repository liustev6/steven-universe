"""The broker's commands, run inside agent-service's container on CT 107:

    docker compose exec agent-service python -m app.broker login     sign in once (device code)
    docker compose exec agent-service python -m app.broker status    signed in? how old? saved?

`login` prints a URL and a one-time code to approve on your phone, then saves the login to OpenBao,
so a rebuilt host comes back signed in. The running service reads the new login on its next model
request. Tokens are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.config import Settings


async def _broker(settings: Settings):
    from service_auth import TokenClient

    from app.broker import AuthBroker, OpenBaoStore

    store = OpenBaoStore(settings.BAO_ADDR, TokenClient.from_env())
    return AuthBroker(settings.CODEX_HOME, store), store


async def login() -> None:
    from app.broker.codex import device_login

    settings = Settings()
    plan = await device_login(settings.CODEX_HOME)
    broker, store = await _broker(settings)
    try:
        await broker.save()
    finally:
        await store.aclose()
    status = broker.status()
    print(f"Signed in (plan: {plan}). Saved to OpenBao: {'yes' if status['saved_to_openbao'] else 'NO'}.")


async def status() -> None:
    broker, store = await _broker(Settings())
    try:
        await broker.restore()
    finally:
        await store.aclose()
    print(json.dumps(broker.status(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.broker", description="agent-service's ChatGPT login")
    parser.add_argument("command", choices=["login", "status"])
    asyncio.run({"login": login, "status": status}[parser.parse_args().command]())


if __name__ == "__main__":
    main()
