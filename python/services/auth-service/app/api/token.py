"""POST /oauth2/token (RFC 6749 §3.2, RFC 8693, RFC 8707, RFC 9396)."""

from __future__ import annotations

import base64
import binascii
import json
import logging
from urllib.parse import parse_qsl, unquote_plus

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_auth.constants import GRANT_CLIENT_CREDENTIALS, GRANT_TOKEN_EXCHANGE, TOKEN_TYPE_ACCESS_TOKEN

from app.errors import NO_STORE, OAuthError, invalid_client, invalid_details, invalid_request, invalid_target
from app.issuer import IssuedToken, TokenRequest

router = APIRouter()
logger = logging.getLogger(__name__)
audit = logging.getLogger("auth_service.audit")

FORM_TYPE = "application/x-www-form-urlencoded"


async def _read_form(request: Request, max_bytes: int) -> dict[str, str]:
    """Parse the urlencoded body by hand instead of FastAPI's Form().

    Form() silently keeps the last value of a repeated parameter, so a request
    like `scope=files:read&scope=files:delete` would be ambiguous. OAuth says
    repeats are an error, and parsing manually is the only way to see them.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != FORM_TYPE:
        raise invalid_request(f"content type must be {FORM_TYPE}")
    body = await request.body()
    if len(body) > max_bytes:
        raise invalid_request("request too large")
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
    except UnicodeDecodeError:
        raise invalid_request("request body is not UTF-8") from None
    form: dict[str, str] = {}
    for name, value in pairs:
        if name in form:
            # RFC 6749 §3.2: parameters must not be repeated.
            raise invalid_request(f"parameter {name!r} is repeated")
        form[name] = value
    return form


def _client_from_basic(request: Request) -> tuple[str, str] | None:
    """client_secret_basic (RFC 6749 §2.3.1): id and secret are form-urlencoded.

    The header is `Authorization: Basic base64(urlencode(id) ":" urlencode(secret))`.
    The extra urlencoding (which most people forget) lets an id or secret
    contain ":" safely. Returns None for anything malformed; the caller turns
    that into the same 401 as a wrong secret, so a caller learns nothing.
    """
    header = request.headers.get("authorization", "")
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    client_id, sep, secret = decoded.partition(":")
    if not sep:
        return None
    return unquote_plus(client_id), unquote_plus(secret)


def _audience(form: dict[str, str]) -> str:
    # Which service the token is for (it becomes the `aud` claim). Two RFCs
    # name this parameter differently: RFC 8707 `resource` and RFC 8693
    # `audience`. Accept either, but a token is only ever for one target.
    resource, audience = form.get("resource"), form.get("audience")
    if resource and audience and resource != audience:
        raise invalid_target("resource and audience name different targets")
    target = resource or audience
    if not target:
        raise invalid_request("resource (or audience) is required")
    return target


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/oauth2/token")
async def token(request: Request) -> JSONResponse:
    """The one endpoint that issues tokens. Every grant comes through here.

    The order of the steps matters:
      1. rate limit        (cheap, before any secret is checked)
      2. read the form     (strict: content type, size, no repeats)
      3. authenticate      (who is asking: HTTP Basic client id + secret)
      4. validate params   (what kind of token, for which service)
      5. run the grant     (issuer.py decides what this client may get)
      6. audit + respond   (one log line, token returned with no-store)

    Every failure raises OAuthError, which app/errors.py turns into a standard
    OAuth JSON error ({"error": ..., "error_description": ...}).
    """
    # app.state holds everything create_app() loaded at startup: policy,
    # client secret hashes, signing keys, the issuer and the rate limiter.
    state = request.app.state
    ip = _client_ip(request)
    credentials = _client_from_basic(request)  # (client_id, secret) or None

    # 1. Rate limit, keyed on client id + IP, so one noisy or misbehaving
    #    caller can't lock out others. It runs before the secret check, so
    #    guessing secrets is capped at RATE_LIMIT_PER_MINUTE tries.
    limit_key = f"{credentials[0] if credentials else '-'}|{ip}"
    if not state.rate_limiter.allow(limit_key):
        return JSONResponse(
            {"error": "rate_limited", "error_description": "too many token requests"},
            status_code=429,
            headers={**NO_STORE, "Retry-After": str(state.rate_limiter.retry_after(limit_key))},
        )

    # 2. Read the body. Secrets in the body are rejected outright, even
    #    correct ones: request bodies get logged by proxies and frameworks far
    #    more often than headers, and allowing one method keeps it simple.
    form = await _read_form(request, state.settings.MAX_FORM_BYTES)
    if any(k in form for k in ("client_secret", "client_assertion")):
        raise invalid_request("only client_secret_basic (HTTP Basic) is supported")

    # 3. Authenticate the caller. The client must be in policy.yaml (it has
    #    permissions) AND have a matching secret hash in clients/ (it has a
    #    credential). Unknown client, wrong secret and disabled client all get
    #    the identical 401, so the response never reveals which client ids
    #    exist. clients.verify() compares hashes in constant time.
    if credentials is None:
        raise invalid_client()
    client_id, secret = credentials
    policy = state.policies.current()  # re-reads policy.yaml if it changed
    if client_id not in policy.clients or not state.clients.verify(client_id, secret):
        logger.warning("invalid_client: client=%r ip=%s", client_id, ip)
        raise invalid_client()

    # 4. Validate the request parameters. From here on we know WHO is asking;
    #    these checks are about WHAT they ask for.
    grant_type = form.get("grant_type")
    if not grant_type:
        raise invalid_request("grant_type is required")
    # RFC 8693 lets a caller send a second token naming an "actor". We don't:
    # the actor is always the authenticated client itself, so it can't be
    # spoofed by passing someone else's token.
    if form.get("actor_token"):
        raise invalid_request("actor_token is not supported")
    # This issuer only produces access tokens (no refresh or ID tokens).
    requested_type = form.get("requested_token_type")
    if requested_type and requested_type != TOKEN_TYPE_ACCESS_TOKEN:
        raise invalid_request("only access tokens can be issued")

    # Optional narrowing. Each of these can only ask for LESS than the policy
    # allows; issuer.py/permissions.py reject anything broader.
    #   scope                  -> which actions, e.g. "files:read files:list"
    #   authorization_details  -> which resources (RFC 9396, a JSON list)
    #   expires_in             -> only honoured for downscoping (grant 4)
    scopes = form["scope"].split() if form.get("scope") else None
    details_raw = None
    if form.get("authorization_details"):
        try:
            details_raw = json.loads(form["authorization_details"])
        except ValueError:
            raise invalid_details("authorization_details is not valid JSON") from None
    expires_in = None
    if form.get("expires_in"):
        try:
            expires_in = int(form["expires_in"])
        except ValueError:
            raise invalid_request("expires_in must be an integer") from None

    # Everything the issuer needs, already authenticated and parsed. The
    # issuer never sees HTTP; this keeps the grant logic testable on its own.
    req = TokenRequest(
        client_id=client_id,
        audience=_audience(form),
        scopes=scopes,
        details_raw=details_raw,
        # For token exchange only: the token (or user id) being exchanged,
        # and what kind of thing it is.
        subject_token=form.get("subject_token"),
        subject_token_type=form.get("subject_token_type"),
        expires_in=expires_in,
    )

    # 5. Run the grant. Only two OAuth grant types exist on the wire;
    #    token_exchange() then picks grant 2, 3 or 4 by looking at the
    #    subject token (a user id, a token issued TO this client, or this
    #    client's OWN token). See issuer.py.
    try:
        if grant_type == GRANT_CLIENT_CREDENTIALS:
            issued = state.issuer.client_credentials(req)  # grant 1: as itself
        elif grant_type == GRANT_TOKEN_EXCHANGE:
            issued = state.issuer.token_exchange(req)      # grants 2, 3, 4
        else:
            raise OAuthError("unsupported_grant_type", f"unsupported grant_type {grant_type!r}")
    except OAuthError as exc:
        # Denials are audited too: a client repeatedly asking for more than
        # it is allowed is worth noticing.
        audit.info(json.dumps({
            "event": "denied", "client_id": client_id, "grant_type": grant_type,
            "aud": req.audience, "error": exc.error, "reason": exc.description, "ip": ip,
        }))
        raise

    # 6. Audit and respond. NO_STORE adds `Cache-Control: no-store`
    #    (RFC 6749 §5.1) so no proxy or browser keeps a copy of the token.
    _audit_mint(issued, ip)
    return JSONResponse(_response_body(issued), headers=NO_STORE)


def _response_body(issued: IssuedToken) -> dict:
    # Standard OAuth token response (RFC 6749 §5.1). `scope` and
    # `authorization_details` repeat what was granted, which may be less than
    # requested, so the client doesn't have to decode its own token.
    body: dict = {
        "access_token": issued.access_token,
        "token_type": "Bearer",
        "expires_in": issued.expires_in,
        "scope": " ".join(issued.scopes),
    }
    if issued.details:
        body["authorization_details"] = list(issued.details)
    if issued.issued_token_type:
        body["issued_token_type"] = issued.issued_token_type
    return body


def _audit_mint(issued: IssuedToken, ip: str) -> None:
    # One JSON line per issued token: who got what, for whom, until when.
    # Never the token itself; `jti` (its unique id) is enough to trace it.
    claims = issued.claims
    audit.info(json.dumps({
        "event": "mint",
        "grant": issued.grant,
        "client_id": claims["client_id"],
        "aud": claims["aud"],
        "sub": claims["sub"],
        "act": claims.get("act"),
        "scope": claims["scope"],
        "authorization_details": claims.get("authorization_details"),
        "jti": claims["jti"],
        "exp": claims["exp"],
        "ip": ip,
    }, separators=(",", ":")))
