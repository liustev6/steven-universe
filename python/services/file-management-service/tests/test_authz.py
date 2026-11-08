"""authorize() and key hygiene, table-driven (spec: "The authorization check", "Key hygiene")."""

import pytest
from service_auth import Principal

from app.authz import BadKey, Forbidden, authorize, resource_name

BUCKETS = ["public", "private"]
ALL = frozenset({"files:read", "files:write", "files:delete", "files:list"})


PI = Principal(service="pi-server", scopes=ALL, authorization_details=(
    {"type": "file_access", "actions": ["read", "write", "delete", "list"], "locations": ["private/pi-server/"]},))
API = Principal(service="api-service", scopes=frozenset({"files:read", "files:list"}), authorization_details=(
    {"type": "file_access", "actions": ["read", "list"], "locations": ["public/"]},))
SANDBOX = Principal(service="pi-server", scopes=frozenset({"files:read", "files:write", "files:list"}),
                    authorization_details=({"type": "file_access", "actions": ["read", "write", "list"],
                                            "locations": ["private/pi-server/workspaces/run-7/"]},))
ONE_FILE = Principal(service="api-service", scopes=frozenset({"files:read", "files:list"}), authorization_details=(
    {"type": "file_access", "actions": ["read", "list"], "locations": ["public/brochure.pdf"]},))
SCOPE_ONLY = Principal(service="x", scopes=ALL)  # scopes but no grants
GRANT_ONLY = Principal(service="x", scopes=frozenset({"files:read"}), authorization_details=(
    {"type": "file_access", "actions": ["read", "write"], "locations": ["private/x/"]},))


@pytest.mark.parametrize(("who", "action", "bucket", "key", "allowed"), [
    # pi-server: its own folder, nothing else
    (PI, "write", "private", "pi-server/notes.txt", True),
    (PI, "read", "private", "pi-server/a/b/c.bin", True),
    (PI, "delete", "private", "pi-server/notes.txt", True),
    (PI, "read", "private", "api-service/secret.txt", False),
    (PI, "read", "private", "pi-server-evil/x", False),          # prefix boundary is the "/"
    (PI, "read", "public", "logo.png", False),
    # api-service's own token: read/list on public/ only
    (API, "read", "public", "logo.png", True),
    (API, "write", "public", "logo.png", False),
    (API, "delete", "public", "logo.png", False),
    (API, "read", "private", "pi-server/notes.txt", False),
    # a sandbox confined to one workspace
    (SANDBOX, "write", "private", "pi-server/workspaces/run-7/out.txt", True),
    (SANDBOX, "write", "private", "pi-server/workspaces/run-8/out.txt", False),
    (SANDBOX, "delete", "private", "pi-server/workspaces/run-7/out.txt", False),
    # an exact-object grant covers that object only
    (ONE_FILE, "read", "public", "brochure.pdf", True),
    (ONE_FILE, "read", "public", "brochure.pdf.bak", False),
    # both levels are required
    (SCOPE_ONLY, "read", "private", "x/a", False),
    (GRANT_ONLY, "write", "private", "x/a", False),             # detail allows write, scope doesn't
    (GRANT_ONLY, "read", "private", "x/a", True),
])
def test_object_access(who, action, bucket, key, allowed):
    resource = resource_name(bucket, key, BUCKETS)
    if allowed:
        authorize(who, action, resource)
    else:
        with pytest.raises(Forbidden):
            authorize(who, action, resource)


@pytest.mark.parametrize(("who", "bucket", "prefix", "allowed"), [
    (PI, "private", "pi-server/", True),
    (PI, "private", "pi-server/photos/", True),
    (PI, "private", "", False),                                 # the whole bucket
    (PI, "private", "pi", False),                               # would match pi-server/ AND pi-other/
    (API, "public", "", True),
    (API, "public", "images/", True),
    (SANDBOX, "private", "pi-server/workspaces/run-7/", True),
    (SANDBOX, "private", "pi-server/workspaces/", False),       # can't list its parent
    (ONE_FILE, "public", "", False),                            # exact-object grants never list
])
def test_listing(who, bucket, prefix, allowed):
    resource = resource_name(bucket, prefix, BUCKETS, prefix=True)
    if allowed:
        authorize(who, "list", resource)
    else:
        with pytest.raises(Forbidden):
            authorize(who, "list", resource)


@pytest.mark.parametrize("key", [
    "", "/pi-server/x", "pi-server//x", "pi-server/./x", "pi-server/../api-service/x", "..",
    "pi-server/x/", "pi-server\\x", "pi-server/x\x00", "pi-server/\x1bx", "a" * 1025,
])
def test_bad_keys_are_rejected(key):
    with pytest.raises(BadKey):
        resource_name("private", key, BUCKETS)


def test_unknown_bucket_is_rejected():
    with pytest.raises(BadKey):
        resource_name("users", "42/x", BUCKETS)


@pytest.mark.parametrize("prefix", ["pi-server//", "pi-server/../", "/pi-server/"])
def test_bad_prefixes_are_rejected(prefix):
    with pytest.raises(BadKey):
        resource_name("private", prefix, BUCKETS, prefix=True)


def test_wildcard_scope_still_needs_a_grant():
    """The local dev principal has scope "*": it passes the scope level, never the grant level."""
    dev = Principal(service="dev", scopes=frozenset({"*"}), authorization_details=(
        {"type": "file_access", "actions": ["read"], "locations": ["public/"]},))
    authorize(dev, "read", resource_name("public", "a.txt", BUCKETS))
    with pytest.raises(Forbidden):
        authorize(dev, "write", resource_name("public", "a.txt", BUCKETS))
    with pytest.raises(Forbidden):
        authorize(dev, "read", resource_name("private", "x/a.txt", BUCKETS))
