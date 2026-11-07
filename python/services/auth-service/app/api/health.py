"""GET /health: liveness for systemd and curl."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    state = request.app.state
    policy = state.policies.current()
    return {
        "status": "healthy",
        "version": state.settings.APP_VERSION,
        "policy_version": state.policies.version,
        "clients_in_policy": len(policy.clients),
        "published_keys": len(state.keys.jwks()["keys"]),
        "active_kid": state.keys.signing_key().kid,
    }
