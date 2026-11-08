"""Signing-key rotation, startup checks, and policy validation/reload."""

import os

import jwt
import pytest
import yaml

from app import keys as keymod
from app.fileutil import InsecureFile
from app.keys import KeyProblem
from app.main import create_app
from app.policy import Policy, PolicyStore
from tests.conftest import REPO_POLICY, ok, policy_data


def _kids(client) -> list[str]:
    return sorted(k["kid"] for k in client.get("/.well-known/jwks.json").json()["keys"])


def _token(mint) -> str:
    return ok(mint("hey-pi", grant_type="client_credentials", resource="file-management"))["access_token"]


def test_key_rotation(client, mint, claims_of, config_dir):
    keys_dir = config_dir / "keys"
    (old_kid,) = _kids(client)
    old_token = _token(mint)

    # 1. publish a new key: in the JWKS, but tokens are still signed with the old one
    new_kid = keymod.new_key(keys_dir)
    assert _kids(client) == sorted([old_kid, new_kid])
    assert claims_of(_token(mint))  # still verifies
    assert jwt.get_unverified_header(_token(mint))["kid"] == old_kid

    # 2. promote: new tokens use the new key; old tokens still verify
    keymod.promote_key(keys_dir, new_kid)
    assert jwt.get_unverified_header(_token(mint))["kid"] == new_kid
    assert claims_of(old_token)

    # 3. retire the old key: no longer published
    keymod.retire_key(keys_dir, old_kid)
    assert _kids(client) == [new_kid]


def test_the_active_key_cannot_be_retired(config_dir):
    active = (config_dir / "keys" / "active").read_text().strip()
    with pytest.raises(KeyProblem):
        keymod.retire_key(config_dir / "keys", active)


def test_refuses_to_start_with_a_readable_key(settings, secrets, config_dir):
    pem = next((config_dir / "keys").glob("*.pem"))
    os.chmod(pem, 0o644)
    with pytest.raises(InsecureFile):
        create_app(settings)


def test_refuses_to_start_with_a_readable_client_file(settings, secrets, config_dir):
    os.chmod(config_dir / "clients" / "hey-pi.sha256", 0o640)
    with pytest.raises(InsecureFile):
        create_app(settings)


def test_refuses_to_start_without_an_active_key(settings, secrets, config_dir):
    (config_dir / "keys" / "active").unlink()
    with pytest.raises(KeyProblem):
        create_app(settings)


# ---- policy ------------------------------------------------------------------------


def test_repo_policy_is_valid():
    Policy.model_validate(yaml.safe_load(REPO_POLICY.read_text()))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p.update(issuer="http://auth.lan.liustev6.ca"), "issuer"),
        (lambda p: p["clients"]["hey-pi"]["grants"].update({"nowhere": {"scopes": []}}), "unknown audience"),
        (lambda p: p["clients"]["hey-pi"]["grants"]["file-management"].update(scopes=["files:nuke"]), "unknown scopes"),
        (lambda p: p["clients"]["hey-pi"]["grants"]["file-management"]["authorization_details"][0]
            .update(locations=["services/${org}/"]), "unknown variable"),
        (lambda p: p["clients"]["hey-pi"]["grants"]["file-management"]["authorization_details"][0]
            .update(locations=["services/../models/"]), "segment"),
        (lambda p: p["clients"]["hey-pi"]["grants"]["file-management"]["authorization_details"][0]
            .update(locations=["secrets/"]), "outside location_roots"),
        (lambda p: p["clients"]["hey-pi"]["grants"]["file-management"]["authorization_details"][0]
            .update(actions=["admin"]), "bad actions"),
        (lambda p: p["clients"].update({"Bad_Name": {}}), "invalid client id"),
    ],
)
def test_invalid_policies_are_rejected(change, message):
    data = policy_data()
    change(data)
    with pytest.raises(ValueError, match=message):
        Policy.model_validate(data)


def test_an_invalid_new_policy_keeps_the_old_one(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy_data()))
    store = PolicyStore(path)
    version = store.version

    bad = policy_data()
    bad["issuer"] = "http://insecure"
    path.write_text(yaml.safe_dump(bad))
    os.utime(path, (1, 1))  # make sure the change is noticed
    assert store.current().issuer == "https://auth.lan.liustev6.ca"
    assert store.version == version

    good = policy_data()
    good["token_ttl_seconds"] = 300
    path.write_text(yaml.safe_dump(good))
    assert store.current().token_ttl_seconds == 300



def test_repo_policy_relays_pi_server_with_all_its_file_permissions():
    """The deployed policy: api-service relaying pi-server to file-management."""
    from app.permissions import resolve

    policy = Policy.model_validate(yaml.safe_load(REPO_POLICY.read_text()))
    relayed = resolve(policy, "pi-server", "file-management", {"client": "pi-server"})
    assert set(relayed.scopes) == {"files:read", "files:write", "files:delete", "files:list"}
    assert [d["locations"] for d in relayed.details] == [["private/pi-server/"]]
