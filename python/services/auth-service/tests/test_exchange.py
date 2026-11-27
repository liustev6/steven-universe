"""Token exchange: grant 2 (user assertion), 3 (delegation chain, relay), 4 (downscope)."""

import json

import yaml

from service_auth.constants import TOKEN_TYPE_ACCESS_TOKEN, TOKEN_TYPE_USER_ID
from tests.conftest import error_of, ok, policy_data

EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"


def exchange(mint, client_id, subject_token, resource, **extra):
    return mint(
        client_id, grant_type=EXCHANGE, subject_token=subject_token,
        subject_token_type=TOKEN_TYPE_ACCESS_TOKEN, resource=resource, **extra,
    )


def assert_user(mint, client_id, user, resource, **extra):
    return mint(
        client_id, grant_type=EXCHANGE, subject_token=f"user:{user}",
        subject_token_type=TOKEN_TYPE_USER_ID, resource=resource, **extra,
    )


def own_token(mint, client_id, resource, **extra) -> str:
    return ok(mint(client_id, grant_type="client_credentials", resource=resource, **extra))["access_token"]


# ---- grant 2 ---------------------------------------------------------------------


def test_api_service_starts_a_delegation_for_a_user(mint, claims_of):
    body = ok(assert_user(mint, "api-service", "42", "file-management"))
    assert body["issued_token_type"] == TOKEN_TYPE_ACCESS_TOKEN
    claims = claims_of(body["access_token"], audience="file-management")
    assert claims["sub"] == "user:42"
    assert claims["act"] == {"sub": "service:api-service"}
    locations = [loc for d in claims["authorization_details"] for loc in d["locations"]]
    assert locations == ["public/", "users/42/"]


def test_only_clients_allowed_to_assert_users_can(mint):
    assert error_of(assert_user(mint, "hey-pi", "42", "file-management")) == "unauthorized_client"


def test_user_ids_cannot_change_the_path(mint):
    for bad in ["../43", "42/..", "", "a" * 65]:
        assert error_of(assert_user(mint, "api-service", bad, "file-management")) == "invalid_request"


# ---- grant 3: user delegation chain -------------------------------------------------


def test_user_delegation_chain(mint, claims_of):
    """api-service -> stevenai-service -> gpu-server -> file-management, for user 42."""
    t1 = ok(assert_user(mint, "api-service", "42", "stevenai-service"))["access_token"]
    t2 = ok(exchange(mint, "stevenai-service", t1, "gpu-server"))["access_token"]
    t3 = ok(exchange(mint, "gpu-server", t2, "file-management"))["access_token"]

    c1, c3 = claims_of(t1), claims_of(t3, audience="file-management")
    assert c3["sub"] == "user:42"
    assert c3["act"] == {
        "sub": "service:gpu-server",
        "act": {"sub": "service:stevenai-service", "act": {"sub": "service:api-service"}},
    }
    # gpu-server's own templates, with ${user}=42: its fixed grant (models/) plus the
    # user's outputs folder, and nothing else of the user's.
    assert c3["authorization_details"] == [
        {"type": "file_access", "actions": ["read"], "locations": ["models/"]},
        {"type": "file_access", "actions": ["write"], "locations": ["users/42/outputs/"]},
    ]
    assert c3["exp"] <= c1["exp"]  # a chain never outlives the original token


def test_a_service_can_only_pass_along_tokens_issued_to_it(mint):
    t1 = ok(assert_user(mint, "api-service", "42", "stevenai-service"))["access_token"]
    assert error_of(exchange(mint, "gpu-server", t1, "file-management")) == "invalid_grant"


def test_delegation_depth_limit(mint, config_dir):
    data = policy_data()
    data["max_delegation_depth"] = 2
    (config_dir / "policy.yaml").write_text(yaml.safe_dump(data))  # picked up without a restart
    t1 = ok(assert_user(mint, "api-service", "42", "stevenai-service"))["access_token"]
    t2 = ok(exchange(mint, "stevenai-service", t1, "gpu-server"))["access_token"]
    response = exchange(mint, "gpu-server", t2, "file-management")
    assert error_of(response) == "invalid_grant"
    assert "too long" in response.json()["error_description"]


def test_tampered_subject_token_is_rejected(mint):
    t1 = ok(assert_user(mint, "api-service", "42", "stevenai-service"))["access_token"]
    header, payload, signature = t1.split(".")
    forged = f"{header}.{payload[:-2]}AA.{signature}"
    assert error_of(exchange(mint, "stevenai-service", forged, "gpu-server")) == "invalid_grant"


# ---- grant 3: relay (outside machine client through api-service) ----------------------


def test_api_service_relays_a_machine_clients_own_permissions(mint, claims_of):
    t1 = own_token(mint, "hey-pi", "api-service")
    body = ok(exchange(mint, "api-service", t1, "file-management"))
    claims = claims_of(body["access_token"], audience="file-management")
    assert claims["sub"] == "service:hey-pi"
    assert claims["act"] == {"sub": "service:api-service"}
    assert claims["client_id"] == "api-service"
    assert claims["scope"] == "files:read files:list"
    # hey-pi's own grant, NOT api-service's (no public/):
    assert claims["authorization_details"] == [
        {"type": "file_access", "actions": ["read", "list"], "locations": ["services/hey-pi/"]}
    ]


def test_only_relay_clients_may_relay(mint):
    t1 = own_token(mint, "stevenai-v2", "gpu-server")
    assert error_of(exchange(mint, "gpu-server", t1, "file-management")) == "unauthorized_client"


# ---- grant 4: downscope -------------------------------------------------------------------


def test_orchestrator_downscopes_for_a_sandbox(mint, claims_of):
    own = own_token(mint, "stevenai-v2", "file-management")
    sandbox = [{"type": "file_access", "identifier": "ws-7f3", "actions": ["read", "write", "list"],
                "locations": ["workspaces/stevenai-v2/ws-7f3/"]}]
    body = ok(exchange(
        mint, "stevenai-v2", own, "file-management",
        scope="files:read files:write files:list",
        authorization_details=json.dumps(sandbox), expires_in="7200",
    ))
    assert body["expires_in"] == 3600  # capped
    claims = claims_of(body["access_token"], audience="file-management")
    assert claims["sub"] == "service:stevenai-v2"
    assert claims["authorization_details"] == sandbox

    # It can be narrowed again, but never widened:
    narrower = [{"type": "file_access", "identifier": "ws-7f3", "actions": ["read"],
                 "locations": ["workspaces/stevenai-v2/ws-7f3/data/"]}]
    ok(exchange(mint, "stevenai-v2", body["access_token"], "file-management",
                scope="files:read", authorization_details=json.dumps(narrower)))
    wider = [{"type": "file_access", "identifier": "ws-7f3", "actions": ["read"],
              "locations": ["workspaces/stevenai-v2/"]}]
    response = exchange(mint, "stevenai-v2", body["access_token"], "file-management",
                        authorization_details=json.dumps(wider))
    assert error_of(response) == "invalid_authorization_details"
    renamed = [{**narrower[0], "identifier": "ws-other"}]
    response = exchange(mint, "stevenai-v2", body["access_token"], "file-management",
                        authorization_details=json.dumps(renamed))
    assert error_of(response) == "invalid_authorization_details"


def test_downscope_cannot_reach_another_clients_workspace(mint):
    own = own_token(mint, "stevenai-v2", "file-management")
    other = [{"type": "file_access", "actions": ["read"], "locations": ["workspaces/hey-pi-agent/"]}]
    response = exchange(mint, "stevenai-v2", own, "file-management", authorization_details=json.dumps(other))
    assert error_of(response) == "invalid_authorization_details"


def test_downscope_needs_permission_and_own_token(mint):
    hey_pi_token = own_token(mint, "hey-pi", "file-management")
    assert error_of(exchange(mint, "hey-pi", hey_pi_token, "file-management")) == "unauthorized_client"
    others_token = own_token(mint, "stevenai-v2", "file-management")
    assert error_of(exchange(mint, "hey-pi-agent", others_token, "file-management")) == "invalid_grant"


def test_expires_in_only_applies_to_downscoping(mint):
    response = mint("hey-pi", grant_type="client_credentials", resource="file-management", expires_in="60")
    assert error_of(response) == "invalid_request"


# ---- grant 3: agent-service relays a session's owner to tools-service (grant_overlap) -------

TOOLS = "tools-service"


def tools_for(mint, owner, wanted, scope="tools:call"):
    """agent-service exchanges the owner's agent-service token for a tools-service token."""
    subject = own_token(mint, owner, "agent-service")
    details = [{"type": "tool_access", "actions": ["call"], "locations": wanted}]
    return exchange(mint, "agent-service", subject, TOOLS, scope=scope,
                    authorization_details=json.dumps(details))


def test_a_profiles_tools_meet_the_owners_grants(mint, claims_of):
    body = ok(tools_for(mint, "hey-pi", ["weather/", "home/"]))
    granted = [{"type": "tool_access", "actions": ["call"], "locations": ["weather/"]}]
    assert body["authorization_details"] == granted          # the response says what it got
    assert body["scope"] == "tools:call"
    claims = claims_of(body["access_token"], audience=TOOLS)
    assert claims["sub"] == "service:hey-pi"
    assert claims["act"] == {"sub": "service:agent-service"}
    assert claims["authorization_details"] == granted


def test_the_overlap_is_the_narrower_of_the_two(mint):
    # asked for one tool of a granted namespace: that tool
    body = ok(tools_for(mint, "hey-pi", ["weather/forecast"]))
    assert body["authorization_details"][0]["locations"] == ["weather/forecast"]
    # asked for whole namespaces, granted one tool of one and all of another: those
    body = ok(tools_for(mint, "admin-cli", ["weather/", "home/"]))
    assert body["authorization_details"][0]["locations"] == ["weather/forecast", "home/lights/"]


def test_nothing_in_common_is_still_refused(mint):
    assert error_of(tools_for(mint, "hey-pi", ["home/"])) == "invalid_authorization_details"
    assert error_of(tools_for(mint, "hey-pi", ["weather/"], scope="tools:admin")) == "invalid_scope"


def test_relay_only_to_its_relay_audiences(mint):
    subject = own_token(mint, "hey-pi", "agent-service")
    response = exchange(mint, "agent-service", subject, "file-management")
    assert error_of(response) == "unauthorized_client"


def test_other_audiences_still_refuse_asking_for_more(mint):
    """grant_overlap is per audience: file-management keeps refusing (above), tools-service overlaps."""
    wider = [{"type": "file_access", "actions": ["write"], "locations": ["public/"]}]
    response = mint("api-service", grant_type="client_credentials", resource="file-management",
                    authorization_details=json.dumps(wider))
    assert error_of(response) == "invalid_authorization_details"


def test_relay_audiences_need_relaying_and_known_audiences():
    import pytest
    from app.policy import Policy

    data = policy_data()
    data["clients"]["hey-pi"]["relay_audiences"] = ["tools-service"]
    with pytest.raises(ValueError, match="relay_audiences without can_relay_clients"):
        Policy.model_validate(data)
    data = policy_data()
    data["clients"]["agent-service"]["relay_audiences"] = ["nowhere"]
    with pytest.raises(ValueError, match="unknown audience 'nowhere'"):
        Policy.model_validate(data)


def test_a_grant_without_tool_access_is_refused_by_the_policy_check():
    """requires_details: at tools-service, no authorization_details means no grant, never all tools."""
    import pytest
    from app.policy import Policy

    data = policy_data()
    data["clients"]["hey-pi"]["grants"]["tools-service"] = {"scopes": ["tools:call"]}
    with pytest.raises(ValueError, match="hey-pi.grants.tools-service: this audience requires authorization_details"):
        Policy.model_validate(data)


def test_no_token_without_details_where_they_are_required(config_dir, secrets):
    """A grant whose only details need ${user} yields none for a service caller: refused, never a
    token with no details (which would read as no limit)."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    data = policy_data()
    data["clients"]["hey-pi"]["grants"]["tools-service"]["authorization_details"] = [
        {"type": "tool_access", "actions": ["call"], "locations": ["users/${user}/"]}]
    (config_dir / "policy.yaml").write_text(yaml.safe_dump(data))
    client = TestClient(create_app(Settings(CONFIG_DIR=config_dir, RATE_LIMIT_PER_MINUTE=10_000)))
    response = client.post("/oauth2/token", data={"grant_type": "client_credentials", "resource": "tools-service"},
                           auth=("hey-pi", secrets["hey-pi"]))
    assert error_of(response) == "invalid_authorization_details"


# ---- grant 3: agent-service relays a session's owner to file-management (grant_overlap, files F6) ----

def files_policy(config_dir):
    """The fixture policy with what CP6 adds: file-management overlaps, agent-service relays to it."""
    data = policy_data()
    data["audiences"]["file-management"]["grant_overlap"] = True
    data["clients"]["agent-service"]["relay_audiences"] = ["tools-service", "file-management"]
    (config_dir / "policy.yaml").write_text(yaml.safe_dump(data))   # picked up without a restart


def files_for(mint, owner, details, scope="files:read files:list files:write"):
    subject = own_token(mint, owner, "agent-service")
    return exchange(mint, "agent-service", subject, "file-management", scope=scope,
                    authorization_details=json.dumps(details))


def test_a_profiles_files_meet_the_owners_grant(mint, config_dir, claims_of):
    """A folder inside the grant narrows it; a location or action outside it is left out."""
    files_policy(config_dir)
    asked = [{"type": "file_access", "actions": ["read", "list", "write"],
              "locations": ["services/hey-pi/notes/", "public/"]}]
    body = ok(files_for(mint, "hey-pi", asked))
    got = [{"type": "file_access", "actions": ["read", "list"], "locations": ["services/hey-pi/notes/"]}]
    assert body["authorization_details"] == got
    claims = claims_of(body["access_token"], audience="file-management")
    assert claims["sub"] == "service:hey-pi" and claims["act"] == {"sub": "service:agent-service"}
    assert claims["authorization_details"] == got and set(claims["scope"].split()) == {"files:read", "files:list"}


def test_files_with_nothing_in_common_are_refused(mint, config_dir):
    files_policy(config_dir)
    other = [{"type": "file_access", "actions": ["read"], "locations": ["services/other/"]}]
    assert error_of(files_for(mint, "hey-pi", other)) == "invalid_authorization_details"
