"""auth-service-admin: the host-only admin CLI (spec: "auth-service-admin commands").

Runs on the auth-service host, against the files in CONFIG_DIR. There is no
admin API on the network: whoever has a shell on this host is the admin.

    auth-service-admin add-client hey-pi --stdout | ssh panel 'sudo install -D -m 600 ... /dev/stdin <path>'
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

from app.clients import ClientProblem, ClientStore
from app.core.config import Settings
from app.errors import OAuthError
from app.keys import KeyProblem, list_keys, new_key, promote_key, retire_key
from app.permissions import resolve
from app.policy import Policy, load_policy
from app.validation import USER_ID_RE


def _fmt_time(ts: float | None) -> str:
    if ts is None:
        return "-"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _emit_secret(secret: str, force_tty: bool) -> None:
    """Write a new secret to stdout, which must be a pipe (or --force-tty)."""
    if sys.stdout.isatty() and not force_tty:
        raise SystemExit(
            "refusing to print a secret to a terminal; pipe it to the service host "
            "(see scripts/add-client.sh) or pass --force-tty"
        )
    sys.stdout.write(secret + "\n")
    sys.stdout.flush()


def _policy(settings: Settings) -> Policy:
    return load_policy(settings.policy_path)


def _require_in_policy(settings: Settings, client_id: str) -> None:
    if client_id not in _policy(settings).clients:
        raise SystemExit(
            f"{client_id!r} is not in {settings.policy_path}; add it (with its grants) in git first"
        )


# ---- commands ---------------------------------------------------------------------


def cmd_add_client(args, settings: Settings) -> None:
    _require_in_policy(settings, args.client_id)
    if not args.stdout:
        raise SystemExit("pass --stdout and pipe the secret to the service host")
    secret = ClientStore(settings.clients_dir).add(args.client_id)
    _emit_secret(secret, args.force_tty)
    print(f"added client {args.client_id}", file=sys.stderr)


def cmd_rotate_secret(args, settings: Settings) -> None:
    if not args.stdout:
        raise SystemExit("pass --stdout and pipe the secret to the service host")
    secret = ClientStore(settings.clients_dir).rotate(args.client_id)
    _emit_secret(secret, args.force_tty)
    print(
        f"rotation started for {args.client_id}; both secrets work until promote-secret",
        file=sys.stderr,
    )


def cmd_promote_secret(args, settings: Settings) -> None:
    ClientStore(settings.clients_dir).promote(args.client_id)
    print(f"{args.client_id}: new secret is now current; old secret no longer works")


def cmd_disable_client(args, settings: Settings) -> None:
    ClientStore(settings.clients_dir).set_enabled(args.client_id, False)
    print(f"{args.client_id} disabled; its existing tokens expire on their own")


def cmd_enable_client(args, settings: Settings) -> None:
    ClientStore(settings.clients_dir).set_enabled(args.client_id, True)
    print(f"{args.client_id} enabled")


def cmd_list_clients(args, settings: Settings) -> None:
    in_policy = set(_policy(settings).clients)
    records = {r.client_id: r for r in ClientStore(settings.clients_dir).list()}
    print(f"{'CLIENT':<20} {'ENABLED':<8} {'IN POLICY':<10} {'CREATED':<17} {'ROTATED':<17} {'ROTATING':<9} LAST MINT")
    for client_id in sorted(in_policy | set(records)):
        r = records.get(client_id)
        if r is None:
            print(f"{client_id:<20} {'-':<8} {'yes':<10} {'(no credential)':<17}")
            continue
        print(
            f"{client_id:<20} {'yes' if r.enabled else 'NO':<8} "
            f"{'yes' if client_id in in_policy else 'NO':<10} {_fmt_time(r.created_at):<17} "
            f"{_fmt_time(r.rotated_at):<17} {'yes' if r.rotating else '-':<9} {_fmt_time(r.last_mint)}"
        )


def cmd_check_policy(args, settings: Settings) -> None:
    path = Path(args.file) if args.file else settings.policy_path
    try:
        policy = Policy.model_validate(yaml.safe_load(path.read_text()))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(f"{path}: {exc}")
    print(f"{path}: ok ({len(policy.audiences)} audiences, {len(policy.clients)} clients)")


def cmd_explain(args, settings: Settings) -> None:
    policy = _policy(settings)
    if args.client_id not in policy.clients:
        raise SystemExit(f"{args.client_id!r} is not in policy")
    variables = {"client": args.client_id}
    if args.user is not None:
        if not USER_ID_RE.match(args.user):
            raise SystemExit("invalid user id")
        variables["user"] = args.user
    perms = resolve(policy, args.client_id, args.audience, variables)
    print(json.dumps(
        {"scope": " ".join(perms.scopes), "authorization_details": list(perms.details)}, indent=2
    ))


def cmd_new_key(args, settings: Settings) -> None:
    kid = new_key(settings.keys_dir)
    print(f"published {kid} (not signing yet); wait for verifiers' JWKS refresh, then: promote-key {kid}")


def cmd_promote_key(args, settings: Settings) -> None:
    promote_key(settings.keys_dir, args.kid)
    print(f"now signing with {args.kid}")


def cmd_retire_key(args, settings: Settings) -> None:
    retire_key(settings.keys_dir, args.kid)
    print(f"retired {args.kid}; tokens it signed no longer verify")


def cmd_list_keys(args, settings: Settings) -> None:
    for kid, active, created in list_keys(settings.keys_dir):
        print(f"{kid:<16} {'ACTIVE' if active else 'published':<10} created {_fmt_time(created)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auth-service-admin", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config-dir", type=Path, help="default: AUTH_SERVICE_CONFIG_DIR or /etc/auth-service")
    sub = parser.add_subparsers(dest="command", required=True)

    def client_cmd(name, func, help_, secret=False):
        p = sub.add_parser(name, help=help_)
        p.add_argument("client_id")
        if secret:
            p.add_argument("--stdout", action="store_true", help="write the new secret to stdout (a pipe)")
            p.add_argument("--force-tty", action="store_true", help="allow printing it to a terminal")
        p.set_defaults(func=func)

    client_cmd("add-client", cmd_add_client, "create a client's credential", secret=True)
    client_cmd("rotate-secret", cmd_rotate_secret, "start rotating a client's secret", secret=True)
    client_cmd("promote-secret", cmd_promote_secret, "finish rotation; the old secret stops working")
    client_cmd("disable-client", cmd_disable_client, "stop a client from minting tokens")
    client_cmd("enable-client", cmd_enable_client, "allow a disabled client again")

    p = sub.add_parser("list-clients", help="clients, credential state, and last mint")
    p.set_defaults(func=cmd_list_clients)

    p = sub.add_parser("check-policy", help="validate policy.yaml")
    p.add_argument("--file", help="check this file instead of the deployed policy")
    p.set_defaults(func=cmd_check_policy)

    p = sub.add_parser("explain", help="show what a client would get at an audience")
    p.add_argument("client_id")
    p.add_argument("audience")
    p.add_argument("--user", help="resolve as a delegated token for this user id")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("new-key", help="generate and publish a signing key (not active yet)")
    p.set_defaults(func=cmd_new_key)
    p = sub.add_parser("promote-key", help="start signing with a published key")
    p.add_argument("kid")
    p.set_defaults(func=cmd_promote_key)
    p = sub.add_parser("retire-key", help="unpublish a key that no longer signs")
    p.add_argument("kid")
    p.set_defaults(func=cmd_retire_key)
    p = sub.add_parser("list-keys", help="published keys and which one signs")
    p.set_defaults(func=cmd_list_keys)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = Settings(CONFIG_DIR=args.config_dir) if args.config_dir else Settings()
    try:
        args.func(args, settings)
    except (ClientProblem, KeyProblem) as exc:
        raise SystemExit(f"error: {exc}")
    except OAuthError as exc:
        raise SystemExit(f"error: {exc.description}")


if __name__ == "__main__":
    main()
