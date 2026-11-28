"""Files (docs/specs/agent-service/files-and-mounts.md): a session's file grant, the overlap of its
profile's `files` with its caller's grant, fixed at its start (F6); the gateway's /dav, paired like
every gateway route (C7), showing only that grant, reading with the turn's caller's token.
file-management and auth-service are faked; the gateway is the real app, in process."""

import base64
import hashlib
import json
import uuid

import httpx
import pytest

from app.config import Settings
from sqlalchemy import select

from app.db.models import ChatSession, Event
from app.protocol.events import ENVELOPE_FIELDS
from app.file_management import NoFiles, covers, file_instructions, granted, locations, resolve
from app.gateway import create_gateway
from app.history.eventlog import EventLog
from app.profiles import AgentProfile, FileAccess
from app.sessions import Sessions
from tests.helpers import ME, PROFILES, FakeRunner, Runtime

PUBLIC = [{"type": "file_access", "actions": ["read", "list"], "locations": ["public/steven-universe/"]}]
SESSION = object()                        # FakeFiles.answer: grant exactly what was asked (the session's own folder)


class FakeFiles:
    """file-management as the sessions core and the gateway see it: what it was asked, with which token."""

    def __init__(self):
        self.grants: list[tuple] = []
        self.reads: list[tuple] = []
        self.writes: list[tuple] = []
        self.answer = PUBLIC              # the overlap auth-service would give
        self.fail: Exception | None = None
        self.stored: set[str] = set()     # what file-management holds (for exists)
        self.status = None                # file-management's answer to a write, when not 2xx
        self.down = False

    async def grant(self, caller_token, request):
        self.grants.append((caller_token, request))
        if self.fail:
            raise self.fail
        return list(request) if self.answer is SESSION else self.answer

    async def put(self, caller_token, request, location, body, content_type):
        if self.down:
            raise httpx.ConnectError("down")
        self.writes.append(("PUT", caller_token, location, bytes(body), content_type))
        self.stored.add(location)
        return httpx.Response(self.status or 200, json={"key": location})

    async def delete(self, caller_token, request, location):
        self.writes.append(("DELETE", caller_token, location))
        self.stored.discard(location)
        return httpx.Response(self.status or 204)

    async def exists(self, caller_token, request, location):
        return location in self.stored

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
    assert file_instructions(PUBLIC) == ("Files you can read in this session (read-only; use your shell: "
                                         "ls, cat, grep):\n- /workspace/inputs/public/steven-universe/")
    assert file_instructions([]) == ""
    assert covers(PUBLIC, "read", "public/steven-universe/specs/a.md")
    assert not covers(PUBLIC, "read", "public/steven-universex/a.md") and not covers(PUBLIC, "write", "public/steven-universe/a.md")


def test_session_is_the_sessions_own_id():
    files = PROFILES["saver"].files
    assert resolve(files, "service:agent-cli", "s-1")[1]["locations"] == ["private/session-files/s-1/"]
    assert len(resolve(files, "service:agent-cli")) == 1                   # no session id: its folder left out
    with pytest.raises(ValueError):
        FileAccess(actions=["write"], locations=["private/${sessions}/"])


def test_sfsync_is_told_only_with_write_or_delete():
    """The files block (F13): from what was granted; sfsync's part only with write/delete, each line
    only for a granted action; the real folders filled in."""
    mine = "private/session-files/s-1/"
    full = PUBLIC + [{"type": "file_access", "actions": ["read", "list", "write", "delete"], "locations": [mine]}]
    text = file_instructions(full)
    assert "- /workspace/inputs/private/session-files/s-1/\n- /workspace/inputs/public/steven-universe/" in text   # sorted
    assert f"You may write to: {mine}" in text and f"You may delete in: {mine}" in text
    assert f"sfsync put /tmp/chart.png {mine}chart.png" in text
    assert f"sfsync put /workspace/work/report.md {mine}reports/report.md" in text
    assert f"sfsync rm  {mine}chart.png" in text
    assert f"appear under /workspace/inputs/{mine} within about 2 seconds" in text
    assert "sfsync" not in file_instructions(PUBLIC)                         # read only: never told
    write_only = file_instructions([{"type": "file_access", "actions": ["write"], "locations": [mine]}])
    assert "sfsync put" in write_only and "sfsync rm" not in write_only and "delete" not in write_only
    assert "appear under" not in write_only and "Files you can read" not in write_only


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


# ---- /files: sfsync's writes, every action decided at the gateway (F7, F10, F12) -------------------------
async def saver(env, caller_token="caller-of-this-turn"):
    env.files.answer = SESSION
    row, headers = await env.start("saver")
    await env.sessions.turn(row.id, ME, "save it", caller_token=caller_token)
    return row, headers, f"private/session-files/{row.id}/"


async def test_a_session_asks_for_its_own_folder(env):
    row, _, mine = await saver(env)
    assert env.files.grants[-1][1][1]["locations"] == [mine]
    assert f"sfsync put /workspace/work/report.md {mine}reports/report.md" in env.runner.claims[-1]["profile"]["instructions"]


async def test_put_saves_into_the_granted_folder_with_the_turns_token(env):
    row, headers, mine = await saver(env)
    r = await env.http.put(f"/files/{mine}reports/r%20one.md", content=b"# report",
                           headers={**headers, "content-type": "text/markdown",
                                    "x-sfsync-source": "/workspace/work/r%20one.md"})
    assert r.status_code == 201, r.text
    assert r.json() == {"location": f"{mine}reports/r one.md", "size": 8,
                        "sha256": hashlib.sha256(b"# report").hexdigest()}
    assert env.files.writes == [("PUT", "caller-of-this-turn", f"{mine}reports/r one.md", b"# report", "text/markdown")]


async def test_put_outside_the_grant_says_where_it_may_write(env):
    _, headers, mine = await saver(env)
    for where in ("private/other/x.md", "public/steven-universe/x.md", f"private/session-files/{uuid.uuid4()}/x.md"):
        r = await env.http.put(f"/files/{where}", content=b"x", headers=headers)
        assert r.status_code == 403 and r.headers["content-type"] == "application/problem+json", where
        p = r.json()
        assert p["status"] == 403 and p["allowed"] == [mine] and where in p["detail"] and mine in p["detail"]
    assert env.files.writes == []


async def test_put_over_the_limit_is_refused(env):
    _, headers, mine = await saver(env)
    r = await env.http.put(f"/files/{mine}big.bin", content=b"x" * (1024 * 1024 + 1), headers=headers)
    assert r.status_code == 413 and "limit of 1 MB per file" in r.json()["detail"]
    assert env.files.writes == []


async def test_bad_destinations_are_400(env):
    _, headers, mine = await saver(env)
    for where in (f"{mine}", f"{mine}a/%2E%2E/b.md", f"{mine}a//b.md", "private"):
        r = await env.http.put(f"/files/{where}", content=b"x", headers=headers)
        assert r.status_code == 400 and r.json()["detail"], where


async def test_rm_removes_what_is_there_and_says_when_nothing_is(env):
    row, headers, mine = await saver(env)
    await env.http.put(f"/files/{mine}chart.png", content=b"png", headers=headers)
    assert (await env.http.delete(f"/files/{mine}chart.png", headers=headers)).status_code == 204
    r = await env.http.delete(f"/files/{mine}chart.png", headers=headers)
    assert r.status_code == 404 and "isn't there" in r.json()["detail"]
    r = await env.http.delete("/files/public/steven-universe/a.md", headers=headers)
    assert r.status_code == 403 and r.json()["allowed"] == [mine]


async def test_each_change_is_an_artifact_event(env):
    row, headers, mine = await saver(env)
    await env.http.put(f"/files/{mine}r.md", content=b"abc", headers={**headers, "x-sfsync-source": "/tmp/r.md"})
    await env.http.delete(f"/files/{mine}r.md", headers=headers)
    async with env.db() as db:
        rows = (await db.execute(select(Event).where(Event.session_id == row.id).order_by(Event.seq))).scalars().all()
    kinds = [(e.payload["type"], {k: v for k, v in e.payload.items() if k not in ENVELOPE_FIELDS})
             for e in rows if e.payload["type"].startswith("artifact.")]
    assert kinds == [("artifact.created", {"location": f"{mine}r.md", "source": "/tmp/r.md", "size": 3,
                                           "sha256": hashlib.sha256(b"abc").hexdigest()}),
                     ("artifact.deleted", {"location": f"{mine}r.md"})]


async def test_writes_need_a_turn_its_pod_and_a_grant(env):
    row, headers, mine = await saver(env)
    assert (await env.http.put(f"/files/{mine}x.md", content=b"x")).status_code == 401
    assert (await env.http.put(f"/files/{mine}x.md", content=b"x",
                               headers={**headers, "x-sandbox-id": str(uuid.uuid4())})).status_code == 403
    turn = env.sessions._live[row.id].turn_id
    rt = Runtime(row.id)
    rt.seq = 1
    await env.sessions.ingest(row.id, rt.answer(turn))
    r = await env.http.put(f"/files/{mine}x.md", content=b"x", headers=headers)
    assert r.status_code == 409 and "no turn is running" in r.json()["detail"]
    _, plain = await env.start("filed")                                      # read-only session: never writes
    r = await env.http.put("/files/public/steven-universe/x.md", content=b"x", headers=plain)
    assert r.status_code == 403 and r.json()["allowed"] == [] and "may not write to any file" in r.json()["detail"]
    assert env.files.writes == []


async def test_storage_trouble_is_try_again(env):
    _, headers, mine = await saver(env)
    env.files.down = True
    r = await env.http.put(f"/files/{mine}x.md", content=b"x", headers=headers)
    assert r.status_code == 503 and "try again" in r.json()["detail"]
    env.files.down, env.files.status = False, 500
    r = await env.http.put(f"/files/{mine}x.md", content=b"x", headers=headers)
    assert r.status_code == 502 and "try again" in r.json()["detail"]
