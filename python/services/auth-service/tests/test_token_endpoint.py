"""Grant 1 (client credentials), client authentication, and request validation."""

import json

import jwt
from fastapi.testclient import TestClient

from app.clients import ClientStore
from app.core.config import Settings
from app.main import create_app
from tests.conftest import ISSUER, error_of, ok


def test_metadata_points_at_token_endpoint_and_jwks(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"] == ISSUER
    assert meta["token_endpoint"] == f"{ISSUER}/oauth2/token"
    assert meta["jwks_uri"] == f"{ISSUER}/.well-known/jwks.json"
    assert "urn:ietf:params:oauth:grant-type:token-exchange" in meta["grant_types_supported"]
    assert meta["authorization_details_types_supported"] == ["file_access"]

    jwks = client.get("/.well-known/jwks.json").json()
    (key,) = jwks["keys"]
    assert key["kty"] == "EC" and key["crv"] == "P-256" and key["alg"] == "ES256"
    assert "d" not in key  # never the private part


def test_client_credentials_token(mint, claims_of):
    response = mint("hey-pi", grant_type="client_credentials", resource="file-management")
    body = ok(response)
    assert response.headers["cache-control"] == "no-store"
    assert body["token_type"] == "Bearer" and body["expires_in"] == 600

    header = jwt.get_unverified_header(body["access_token"])
    assert header["alg"] == "ES256" and header["typ"] == "at+jwt"

    claims = claims_of(body["access_token"], audience="file-management")
    assert claims["sub"] == "service:hey-pi"
    assert claims["client_id"] == "hey-pi"
    assert "act" not in claims
    assert claims["scope"] == "files:read files:list"
    assert claims["authorization_details"] == [
        {"type": "file_access", "actions": ["read", "list"], "locations": ["services/hey-pi/"]}
    ]
    assert claims["exp"] - claims["iat"] == 600


def test_user_templates_are_dropped_from_a_service_token(mint, claims_of):
    """api-service's own token = what anonymous visitors may do (A11)."""
    body = ok(mint("api-service", grant_type="client_credentials", resource="file-management"))
    details = claims_of(body["access_token"])["authorization_details"]
    assert details == [{"type": "file_access", "actions": ["read", "list"], "locations": ["public/"]}]


def test_audience_parameter_is_accepted_too(mint):
    ok(mint("hey-pi", grant_type="client_credentials", audience="file-management"))


def test_requesting_less_narrows_the_token(mint, claims_of):
    detail = [{"type": "file_access", "actions": ["read"], "locations": ["public/blog/"]}]
    body = ok(mint(
        "api-service", grant_type="client_credentials", resource="file-management",
        scope="files:read", authorization_details=json.dumps(detail),
    ))
    claims = claims_of(body["access_token"])
    assert claims["scope"] == "files:read"
    assert claims["authorization_details"] == detail


def test_requesting_more_than_allowed_fails(mint):
    def ask(**extra):
        return mint("api-service", grant_type="client_credentials", resource="file-management", **extra)

    assert error_of(ask(scope="files:admin")) == "invalid_scope"
    wider = [{"type": "file_access", "actions": ["write"], "locations": ["public/"]}]
    assert error_of(ask(authorization_details=json.dumps(wider))) == "invalid_authorization_details"
    other = [{"type": "file_access", "actions": ["read"], "locations": ["users/42/"]}]
    assert error_of(ask(authorization_details=json.dumps(other))) == "invalid_authorization_details"
    escape = [{"type": "file_access", "actions": ["read"], "locations": ["public/../users/42/"]}]
    assert error_of(ask(authorization_details=json.dumps(escape))) == "invalid_authorization_details"
    unknown = [{"type": "gpu_access", "actions": ["read"], "locations": ["public/"]}]
    assert error_of(ask(authorization_details=json.dumps(unknown))) == "invalid_authorization_details"
    assert error_of(ask(authorization_details="not json")) == "invalid_authorization_details"


def test_prefix_rule_needs_a_segment_boundary(mint):
    """users/42/ must not cover users/420/ (prefixes end in '/')."""
    detail = [{"type": "file_access", "actions": ["read"], "locations": ["services/hey-pi-other/"]}]
    response = mint("hey-pi", grant_type="client_credentials", resource="file-management",
                    authorization_details=json.dumps(detail))
    assert error_of(response) == "invalid_authorization_details"


def test_audience_without_a_grant_is_invalid_target(mint):
    response = mint("gpu-server", grant_type="client_credentials", resource="stevenai-service")
    assert error_of(response) == "invalid_target"
    response = mint("gpu-server", grant_type="client_credentials", resource="nowhere")
    assert error_of(response) == "invalid_target"


def test_client_authentication_failures_look_identical(client, mint, secrets, config_dir):
    form = {"grant_type": "client_credentials", "resource": "file-management"}
    responses = [
        client.post("/oauth2/token", data=form, auth=("hey-pi", "wrong")),
        client.post("/oauth2/token", data=form, auth=("nobody", "whatever")),
        client.post("/oauth2/token", data=form),  # no credentials
    ]
    ClientStore(config_dir / "clients").set_enabled("hey-pi", False)
    responses.append(client.post("/oauth2/token", data=form, auth=("hey-pi", secrets["hey-pi"])))
    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"error": "invalid_client", "error_description": "client authentication failed"}
        assert response.headers["www-authenticate"].startswith("Basic")


def test_malformed_requests(client, mint, secrets):
    auth = ("hey-pi", secrets["hey-pi"])
    repeated = client.post(
        "/oauth2/token",
        content="grant_type=client_credentials&resource=file-management&resource=api-service",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=auth,
    )
    assert error_of(repeated) == "invalid_request"
    as_json = client.post("/oauth2/token", json={"grant_type": "client_credentials"}, auth=auth)
    assert error_of(as_json) == "invalid_request"
    assert error_of(mint("hey-pi", grant_type="password", resource="file-management")) == "unsupported_grant_type"
    assert error_of(mint("hey-pi", grant_type="client_credentials")) == "invalid_request"  # no target
    in_body = client.post(
        "/oauth2/token",
        data={"grant_type": "client_credentials", "resource": "file-management",
              "client_id": "hey-pi", "client_secret": secrets["hey-pi"]},
    )
    assert error_of(in_body) == "invalid_request"
    conflicting = mint("hey-pi", grant_type="client_credentials", resource="file-management", audience="api-service")
    assert error_of(conflicting) == "invalid_target"


def test_rate_limit(settings: Settings, secrets):
    client = TestClient(create_app(settings.model_copy(update={"RATE_LIMIT_PER_MINUTE": 2})))
    form = {"grant_type": "client_credentials", "resource": "file-management"}
    auth = ("hey-pi", secrets["hey-pi"])
    assert client.post("/oauth2/token", data=form, auth=auth).status_code == 200
    assert client.post("/oauth2/token", data=form, auth=auth).status_code == 200
    limited = client.post("/oauth2/token", data=form, auth=auth)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["published_keys"] == 1
