"""LAN break-glass (deployer spec D18): run a deploy without a GitHub token.

    docker exec -it deployer-deployer-1 python -m app.cli deploy <service> [<ref>]

<ref> is a branch or (short) sha on a pushed branch; default main. Everything else is the same as
an API deploy: manifest at that commit, host allowlist, secrets from OpenBao with the deployer's
identity, deploy-target over the restricted key, rollback on failure, audit log. Differences: no
GitHub token, `pipeline: false` services are allowed (their hosts must still be in the allowlist),
and any pushed branch is allowed (e.g. a rollback drill). Only root on the deployer's host can run
it, and root there already holds everything the deployer holds.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from app.config import Settings
from app.deploys import DeployError
from app.main import build_deployer


async def deploy(service: str, ref: str) -> int:
    settings = Settings()
    deployer, closers = build_deployer(settings)
    try:
        try:
            await deployer.repo.fetch()
            sha = await deployer.repo.resolve(ref)
            plan = await deployer.prepare(service, sha, require_pipeline=False, require_main=False)
        except DeployError as exc:
            print(f"refused: {exc.message}", file=sys.stderr)
            return 2
        except Exception as exc:  # RepoError from resolve
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
        queue = deployer.start(plan, {"cli": user})
        status = "failed"
        while (event := await queue.get()) is not None:
            kind = event["event"]
            if kind == "log":
                print(event["line"], flush=True)
            elif kind == "accepted":
                print(f"==> deploying {service} {sha[:12]} to {event['host']}", flush=True)
            elif kind == "rollback":
                print(f"==> ROLLING BACK to {event['to'][:12]} ({event['reason']})", flush=True)
            elif kind == "result":
                status = event["status"]
                print(f"==> {status} in {event.get('seconds')} s" + (f": {event['error']}" if event.get("error") else ""))
        return 0 if status == "succeeded" else 1
    finally:
        await deployer.wait()
        for c in closers:
            await c.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("deploy", help="deploy <service> at <ref> (default: main)")
    d.add_argument("service")
    d.add_argument("ref", nargs="?", default="main")
    args = parser.parse_args()
    sys.exit(asyncio.run(deploy(args.service, args.ref)))


if __name__ == "__main__":
    main()
