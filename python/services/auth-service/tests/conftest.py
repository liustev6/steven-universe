from pathlib import Path

import jwt
import pytest
import yaml
from fastapi.testclient import TestClient
from service_auth.verifier import decode_claims, read_kid

from app import keys as keymod
from app.clients import ClientStore
from app.core.config import Settings
from app.main import create_app

REPO_POLICY = Path(__file__).resolve().parents[4] / "infra/services/auth-service/policy.yaml"  # deployed policy
FIXTURE_POLICY = Path(__file__).resolve().parent / "fixtures" / "policy.yaml"  # full test policy
ISSUER = "https://auth.lan.liustev6.ca"
CLIENTS = ["api-service", "hey-pi", "gpu-server", "stevenai-v2", "hey-pi-agent", "admin-cli", "stevenai-service"]


def policy_data() -> dict:
    """The fixture policy plus a `stevenai-service` client, so the full
    api-service -> stevenai-service -> gpu-server -> file-management chain exists."""
    data = yaml.safe_load(FIXTURE_POLICY.read_text())
    data["clients"]["stevenai-service"] = {
        "grants": {"gpu-server": {"scopes": ["tasks:run"]}},
    }
    return data


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "policy.yaml").write_text(yaml.safe_dump(policy_data()))
    kid = keymod.new_key(tmp_path / "keys")
    keymod.promote_key(tmp_path / "keys", kid)
    return tmp_path


@pytest.fixture
def secrets(config_dir: Path) -> dict[str, str]:
    store = ClientStore(config_dir / "clients")
    return {client_id: store.add(client_id) for client_id in CLIENTS}


@pytest.fixture
def settings(config_dir: Path) -> Settings:
    return Settings(CONFIG_DIR=config_dir, RATE_LIMIT_PER_MINUTE=10_000)


@pytest.fixture
def app(settings: Settings, secrets: dict[str, str]):
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def mint(client: TestClient, secrets: dict[str, str]):
    """POST /oauth2/token as `client_id`; returns the response."""

    def _mint(client_id: str, **form):
        return client.post("/oauth2/token", data=form, auth=(client_id, secrets[client_id]))

    return _mint


@pytest.fixture
def claims_of(client: TestClient):
    """Verify a token with the published JWKS (as a resource service would)."""

    def _claims(token: str, audience: str | None = None) -> dict:
        jwks = client.get("/.well-known/jwks.json").json()
        keys = {k["kid"]: jwt.PyJWK(k).key for k in jwks["keys"]}
        return decode_claims(token, keys[read_kid(token)], issuer=ISSUER, audience=audience)

    return _claims


def ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


def error_of(response) -> str:
    return response.json()["error"]
