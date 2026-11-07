"""auth-service-admin: client credentials and their lifecycle."""

import json
import sys

import pytest

from app.admin import main
from app.clients import ClientStore


def run(config_dir, *args, capsys) -> tuple[str, str]:
    main(["--config-dir", str(config_dir), *args])
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_add_client_writes_only_a_hash(config_dir, capsys):
    out, err = run(config_dir, "add-client", "hey-pi", "--stdout", capsys=capsys)
    secret = out.strip()
    assert len(secret) >= 43 and "added client hey-pi" in err
    stored = (config_dir / "clients" / "hey-pi.sha256").read_text()
    assert secret not in stored
    assert oct((config_dir / "clients" / "hey-pi.sha256").stat().st_mode & 0o777) == "0o600"
    assert ClientStore(config_dir / "clients").verify("hey-pi", secret)


def test_add_client_requires_policy_entry_and_stdout(config_dir, capsys):
    with pytest.raises(SystemExit, match="not in"):
        run(config_dir, "add-client", "unknown-service", "--stdout", capsys=capsys)
    with pytest.raises(SystemExit, match="--stdout"):
        run(config_dir, "add-client", "hey-pi", capsys=capsys)


def test_secret_is_never_printed_to_a_terminal(config_dir, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    with pytest.raises(SystemExit, match="terminal"):
        run(config_dir, "add-client", "hey-pi", "--stdout", capsys=capsys)


def test_rotation_keeps_both_secrets_until_promoted(config_dir, capsys):
    store = ClientStore(config_dir / "clients")
    old = run(config_dir, "add-client", "hey-pi", "--stdout", capsys=capsys)[0].strip()
    new = run(config_dir, "rotate-secret", "hey-pi", "--stdout", capsys=capsys)[0].strip()
    assert store.verify("hey-pi", old) and store.verify("hey-pi", new)
    run(config_dir, "promote-secret", "hey-pi", capsys=capsys)
    assert not store.verify("hey-pi", old) and store.verify("hey-pi", new)


def test_disable_and_enable(config_dir, capsys):
    store = ClientStore(config_dir / "clients")
    secret = run(config_dir, "add-client", "hey-pi", "--stdout", capsys=capsys)[0].strip()
    run(config_dir, "disable-client", "hey-pi", capsys=capsys)
    assert not store.verify("hey-pi", secret)
    run(config_dir, "enable-client", "hey-pi", capsys=capsys)
    assert store.verify("hey-pi", secret)


def test_list_clients_shows_missing_credentials(config_dir, capsys):
    run(config_dir, "add-client", "hey-pi", "--stdout", capsys=capsys)
    out, _ = run(config_dir, "list-clients", capsys=capsys)
    assert "hey-pi" in out and "(no credential)" in out


def test_explain(config_dir, capsys):
    out, _ = run(config_dir, "explain", "gpu-server", "file-management", "--user", "42", capsys=capsys)
    resolved = json.loads(out)
    assert {"type": "file_access", "actions": ["write"], "locations": ["users/42/outputs/"]} in resolved[
        "authorization_details"
    ]
    out, _ = run(config_dir, "explain", "gpu-server", "file-management", capsys=capsys)
    assert "users/" not in out  # no ${user} without a user


def test_check_policy(config_dir, capsys):
    out, _ = run(config_dir, "check-policy", capsys=capsys)
    assert "ok" in out
