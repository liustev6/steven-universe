"""Files (docs/specs/agent-service/files-and-mounts.md): a session's file grant, the overlap of its
profile's `files` with its caller's grant, fixed at its start (F6); the gateway's /dav, paired like
every gateway route (C7), showing only that grant, reading with the turn's caller's token.
file-management and auth-service are faked; the gateway is the real app, in process."""

import base64
import json
import uuid

import httpx
import pytest

from app.config import Settings
from app.db.models import ChatSession
from app.file_management import NoFiles, file_instructions, granted, locations, resolve
from app.gateway import create_gateway
from app.history.eventlog import EventLog
from app.profiles import AgentProfile, FileAccess
from app.sessions import Sessions
from tests.helpers import ME, PROFILES, FakeRunner, Runtime

PUBLIC = [{"type": "file_access", "actions": ["read", "list"], "locations": ["public/steven-universe/"]}]


class FakeFiles:
    """file-management as the sessions core and the gateway see it: what it was asked, with which token."""

    def __init__(self):
        self.grants: list[tuple] = []
        self.reads: list[tuple] = []
        self.answer = PUBLIC              # the overlap auth-service would give
        self.fail: Exception | None = None

    async def grant(self, caller_token, request):
        self.grants.append((caller_token, request))
        if self.fail:
            raise self.fail
        return self.answer

    async def dav(self, caller_token, request, method, path, headers):
        self.reads.append((caller_token, request, method, path, headers.get("depth"), headers.get("range")))
        return httpx.Response(207 if method == "PROPFIND" else 200, stream=httpx.ByteStream(b"<from file-management/>"),
                              headers={"content-type": "application/xml", "etag": '"e"', "x-other": "dropped"})


class FakeLogin:
    def headers(self):
        return {"authorization": "Bearer x"}

    async def refresh(self):
        pass


@pytest.fixture
async def env(db):
    runner, files = FakeRunner(), FakeFiles()
    sessions = Sessions(db, EventLog(db), runner, dict(PROFILES), Settings(), None, files)
    up = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    gateway = create_gateway(sessions, FakeLogin(), up)

    class Env:
        pass
    e = Env()
    e.sessions, e.runner, e.files, e.db = sessions, runner, files, db

    async def start(profile="filed", caller_token="caller-at-start"):
        row, _ = await sessions.start(ME, profile, caller_token=caller_token)
        claim = runner.claims[-1]
        await sessions.ingest(row.id, [Runtime(row.id).ready()])
        return row, {"authorization": f"Bearer {claim['capability']}", "x-sandbox-id": str(claim["sandbox_id"])}

    e.start = start
    e.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="https://gateway.test")
    yield e
    await e.http.aclose()
    await up.aclose()


def propfind(env, path, headers, depth="1"):
    return env.http.request("PROPFIND", path, headers={**headers, **({"Depth": depth} if depth else {})})


def hrefs(r):
    return [h.split("</D:href>")[0] for h in r.text.split("<D:href>")[1:]]


# ---- the profile's request, for its caller ----------------------------------------------------------
def test_files_locations_are_checked_when_profiles_load():
    for bad in (["public"], ["/public/"], ["public/../private/"], ["Public/x/"], ["public/${user}/"]):
        with pytest.raises(ValueError):
            FileAccess(actions=["read"], locations=bad)
    with pytest.raises(ValueError):
        FileAccess(actions=[], locations=["public/"])
    with pytest.raises(ValueError):
        AgentProfile(template="chat", files=[{"actions": ["admin"], "locations": ["public/"]}])
    assert FileAccess(actions=["read", "list"], locations=["public/", "private/${client}/notes/"])


def test_client_is_the_callers_client():
    files = PROFILES["filed"].files
    assert resolve(files, "service:pi-server") == [{"type": "file_access", "actions": ["read", "list"],
                                                    "locations": ["public/steven-universe/", "private/pi-server/notes/"]}]
    # another kind of caller (a person, later): the entries naming ${client} are left out
    assert resolve(files, "user:42")[0]["locations"] == ["public/steven-universe/"]
    assert resolve([], "service:pi-server") == []


def test_what_was_granted_is_read_from_the_token():
    def token(claims):
        seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        return f"h.{seg}.s"
    t = token({"authorization_details": PUBLIC + [{"type": "tool_access", "actions": ["call"], "locations": ["weather/"]}]})
    assert granted(t) == PUBLIC
    assert granted("not-a-jwt") == []
    assert locations(PUBLIC + [{"type": "file_access", "actions": ["write"], "locations": ["public/out/"]}], "read") \
        == ["public/steven-universe/"]
    assert file_instructions(PUBLIC) == ("Files you can read in this session (a read-only folder; use your shell: "
                                         "ls, cat, grep):\n- /workspace/inputs/public/steven-universe/")
    assert file_instructions([]) == ""


# ---- a session's grant, fixed when it starts (F6) -----------------------------------------------------
async def test_a_session_gets_the_overlap_when_it_starts(env):
    row, _ = await env.start()
    assert env.files.grants == [("caller-at-start", resolve(PROFILES["filed"].files, ME))]
    async with env.db() as db:
        assert (await db.get(ChatSession, row.id)).files == PUBLIC            # stored: it survives a restart
    instructions = env.runner.claims[-1]["profile"]["instructions"]
    assert "You answer from the files." in instructions and "/workspace/inputs/public/steven-universe/" in instructions


async def test_no_files_never_fails_a_start(env):
    for fail in (NoFiles("no grant"), RuntimeError("file-management down")):
        env.files.fail = fail
        row, _ = await env.start()
        async with env.db() as db:
            assert (await db.get(ChatSession, row.id)).files is None
    await env.start("chat")                                                    # no files asked: not called
    await env.start(caller_token=None)                                         # no caller token: not called
    assert len(env.files.grants) == 2


async def test_a_restart_keeps_each_sessions_files(env, db):
    row, _ = await env.start()
    again = Sessions(db, EventLog(db), env.runner, dict(PROFILES), Settings(), None, env.files)
    await again.load()
    assert again._live[row.id].files == PUBLIC


# ---- /dav: paired, its grant only, the turn's token ----------------------------------------------------
async def test_dav_needs_the_sessions_capability_from_its_own_pod(env):
    _, headers = await env.start()
    assert (await propfind(env, "/dav/", {})).status_code == 401
    assert (await propfind(env, "/dav/", {**headers, "x-sandbox-id": str(uuid.uuid4())})).status_code == 403


async def test_above_the_grant_only_the_way_down_is_shown(env):
    _, headers = await env.start()
    r = await propfind(env, "/dav/", headers)
    assert r.status_code == 207 and hrefs(r) == ["/dav/", "/dav/public/"]
    r = await propfind(env, "/dav/public/", headers)
    assert hrefs(r) == ["/dav/public/", "/dav/public/steven-universe/"]       # not the rest of public/
    assert hrefs(await propfind(env, "/dav/public", headers, "0")) == ["/dav/public/"]
    assert env.files.reads == []                                                # made here, not asked


async def test_outside_the_grant_is_404(env):
    row, headers = await env.start()
    await env.sessions.turn(row.id, ME, "hi", caller_token="t1")
    for path in ("/dav/private/", "/dav/public/other/", "/dav/public/steven-universex/", "/dav/nobucket/x"):
        assert (await propfind(env, path, headers)).status_code == 404, path
    assert (await env.http.get("/dav/public/x.md", headers=headers)).status_code == 404
    assert env.files.reads == []


async def test_inside_the_grant_is_read_with_the_turns_caller_token(env):
    row, headers = await env.start()
    await env.sessions.turn(row.id, ME, "summarize", caller_token="caller-of-this-turn")
    r = await propfind(env, "/dav/public/steven-universe/specs/", headers)
    assert r.status_code == 207 and r.text == "<from file-management/>"
    assert "x-other" not in r.headers and r.headers["etag"] == '"e"'
    r = await env.http.get("/dav/public/steven-universe/specs/c%20with%20space.md",
                           headers={**headers, "Range": "bytes=0-3"})
    assert r.status_code == 200
    assert env.files.reads == [
        ("caller-of-this-turn", PUBLIC, "PROPFIND", "public/steven-universe/specs/", "1", None),
        ("caller-of-this-turn", PUBLIC, "GET", "public/steven-universe/specs/c%20with%20space.md", None, "bytes=0-3"),
    ]   # the session's grant asked for again; the path exactly as it came (still encoded)


async def test_between_turns_nothing_is_read(env):
    row, headers = await env.start()
    turn, _ = await env.sessions.turn(row.id, ME, "hi", caller_token="t1")
    rt = Runtime(row.id)
    rt.seq = 1
    await env.sessions.ingest(row.id, rt.answer(turn.id))
    assert (await env.http.get("/dav/public/steven-universe/a.md", headers=headers)).status_code == 403
    assert (await propfind(env, "/dav/", headers)).status_code == 207           # the way down needs no token
    assert env.files.reads == []


async def test_it_is_read_only_and_finite(env):
    row, headers = await env.start()
    await env.sessions.turn(row.id, ME, "hi", caller_token="t1")
    for method in ("PUT", "DELETE", "MKCOL", "MOVE", "COPY", "PROPPATCH", "LOCK"):
        r = await env.http.request(method, "/dav/public/steven-universe/a.md", headers=headers)
        assert r.status_code == 405 and "PROPFIND" in r.headers["allow"], method
    assert (await propfind(env, "/dav/", headers, depth=None)).status_code == 403   # infinity
    assert (await propfind(env, "/dav/public/steven-universe/%2E%2E/x/", headers)).status_code == 400
    assert (await env.http.request("OPTIONS", "/dav/", headers=headers)).headers["dav"] == "1"
    assert env.files.reads == []


async def test_a_session_without_files_sees_an_empty_root(env):
    _, headers = await env.start("chat")
    r = await propfind(env, "/dav/", headers)
    assert r.status_code == 207 and hrefs(r) == ["/dav/"]
    assert (await propfind(env, "/dav/public/", headers)).status_code == 404


# ---- the client itself: the exchange, and what it forwards ---------------------------------------------
async def test_the_exchange_asks_for_the_grant_and_forwards_reads_only_with_its_token():
    from service_auth import TokenRequestError

    from app.file_management import FileManagement

    def token(details):
        seg = base64.urlsafe_b64encode(json.dumps({"authorization_details": details}).encode()).rstrip(b"=").decode()
        return f"h.{seg}.s"

    class Tokens:
        def __init__(self):
            self.asked, self.refuse = [], None

        async def exchange(self, subject, audience, scopes=None, details=None):
            self.asked.append((subject, audience, scopes, details))
            if self.refuse:
                raise self.refuse
            return token(details)

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(207, stream=httpx.ByteStream(b"ok"))

    tokens = Tokens()
    fm = FileManagement(tokens, "https://files.test/", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    request = PUBLIC + [{"type": "file_access", "actions": ["write"], "locations": ["public/out/"]}]
    assert await fm.grant("T1", request) == request
    assert tokens.asked[0] == ("T1", "file-management", ["files:list", "files:read", "files:write"], request)
    r = await fm.dav("T1", PUBLIC, "PROPFIND", "public/steven-universe/a%20b/",
                     {"Depth": "1", "Authorization": "Bearer capability", "X-Sandbox-Id": "pod", "Cookie": "c"})
    await r.aclose()
    sent = seen[0]
    assert str(sent.url) == "https://files.test/dav/public/steven-universe/a%20b/"
    assert sent.headers["authorization"].startswith("Bearer h.")              # the exchanged token, not the capability
    assert sent.headers["depth"] == "1" and "x-sandbox-id" not in sent.headers and "cookie" not in sent.headers
    tokens.refuse = TokenRequestError("invalid_scope", "nothing in common", status=400)   # not transient
    with pytest.raises(NoFiles):
        await fm.grant("T1", PUBLIC)
    with pytest.raises(NoFiles):
        await fm.grant("T1", [])
    await fm.aclose()
