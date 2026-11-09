"""/deploy/*: GitHub Actions -> the deployer (api-service spec "Deploys"; deployer spec D3, D15).

Mode `proxy`: GitHub's OIDC token is addressed to the deployer, not to api-service. api-service
never consumes or verifies it (the deployer does, against GitHub's keys); it only stops what can't
possibly be a deploy before it reaches the LAN, then forwards unchanged and streams the answer.

    POST /deploy/v1/deployments                        start a deploy (NDJSON stream)
    GET  /deploy/v1/deployments/<id>/events?after=<n>  reconnect after a dropped stream

    precheck.py   the cheap token filter (unverified on purpose)
    routes.py     the two routes, rate limits, body limit, streaming forward
"""

from app.deploy.routes import build_router

__all__ = ["build_router"]
