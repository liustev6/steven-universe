"""Protocol constants shared by auth-service and every service that uses its tokens."""

# OAuth 2.0 grant types (RFC 6749 §4.4, RFC 8693 §2.1).
GRANT_CLIENT_CREDENTIALS = "client_credentials"
GRANT_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"

# Token type identifiers for token exchange (RFC 8693 §3).
TOKEN_TYPE_ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
# Non-standard: a user id asserted by a trusted client (auth-service grant 2, phase 2 only).
TOKEN_TYPE_USER_ID = "urn:steven-universe:token-type:user-id"

# JWT access token profile (RFC 9068).
ACCESS_TOKEN_TYP = "at+jwt"
SIGNING_ALG = "ES256"

# Claims every access token must carry (RFC 9068 §2.2).
REQUIRED_CLAIMS = ("iss", "sub", "aud", "exp", "iat", "jti", "client_id")

# Authorization Server Metadata (RFC 8414).
METADATA_PATH = "/.well-known/oauth-authorization-server"
JWKS_PATH = "/.well-known/jwks.json"
TOKEN_PATH = "/oauth2/token"

# Prefixes of the `sub` claim.
SERVICE_PREFIX = "service:"
USER_PREFIX = "user:"
