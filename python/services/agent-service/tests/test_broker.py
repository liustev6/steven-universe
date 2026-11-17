"""The auth broker (C11, MA1–MA6): credentials, refresh, the durable copy in OpenBao."""

import asyncio
import json
import stat
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.broker import AuthBroker, NotSignedIn, OpenBaoStore, StoreError


def bundle(token: str = "access-1", hours_ago: float = 1, mode: str = "chatgpt") -> str:
    """auth.json as Codex writes it (the fields the broker reads)."""
    when = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return json.dumps({"auth_mode": mode, "last_refresh": when,
                       "tokens": {"access_token": token, "refresh_token": "r-" + token, "account_id": "acct-9"}})


class FakeStore:
    def __init__(self, value: str | None = None):
        self.value, self.saves, self.broken = value, 0, False

    async def load(self):
        if self.broken:
            raise StoreError("OpenBao unreachable")
        return self.value

    async def save(self, b):
        if self.broken:
            raise StoreError("OpenBao unreachable")
        self.value, self.saves = b, self.saves + 1


def codex_refresh_to(token: str, calls: list):
    """Codex's refresh: rewrites auth.json with a new access token and last_refresh."""
    async def refresher(codex_home):
        calls.append(token)
        await asyncio.sleep(0.05)
        (codex_home / "auth.json").write_text(bundle(token, hours_ago=0))
    return refresher


@pytest.fixture
def home(tmp_path):
    return tmp_path / "codex-home"


def broker(home, store=None, refresher=None) -> AuthBroker:
    return AuthBroker(home, store, **({"refresher": refresher} if refresher else {}))


def write(home, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(text)


# ---- credentials ----------------------------------------------------------------------------------
def test_headers_carry_the_token_and_account(home):
    write(home, bundle("tok"))
    assert broker(home).headers() == {"authorization": "Bearer tok", "chatgpt-account-id": "acct-9"}


@pytest.mark.parametrize("content", [None, "not json", bundle(mode="apikey"), json.dumps({"tokens": {}})])
def test_no_usable_login(home, content):
    if content is not None:
        write(home, content)
    with pytest.raises(NotSignedIn):
        broker(home).headers()


# ---- refresh --------------------------------------------------------------------------------------
async def test_a_refresh_is_saved_to_openbao(home):
    write(home, bundle("old"))
    store, calls = FakeStore(), []
    b = broker(home, store, codex_refresh_to("new", calls))
    await b.refresh()
    assert b.headers()["authorization"] == "Bearer new"
    assert json.loads(store.value)["tokens"]["access_token"] == "new"
    await b.save()
    assert store.saves == 1                                    # unchanged: not saved again


async def test_concurrent_401s_refresh_once(home):
    """Five sessions hit an expired token together: one refresh (refresh tokens rotate)."""
    write(home, bundle("old"))
    calls = []
    b = broker(home, FakeStore(), codex_refresh_to("new", calls))
    await asyncio.gather(*(b.refresh() for _ in range(5)))
    assert calls == ["new"]


async def test_a_failed_save_is_not_fatal(home):
    write(home, bundle("old"))
    store, calls = FakeStore(), []
    store.broken = True
    b = broker(home, store, codex_refresh_to("new", calls))
    await b.refresh()
    assert b.headers()["authorization"] == "Bearer new"        # the model keeps working
    assert b.status()["saved_to_openbao"] is False


async def test_a_login_made_by_another_process_is_saved_by_the_running_service(home):
    """`python -m app.broker login` saved it itself; the running service learns of it on its next tick."""
    store = FakeStore()
    b = broker(home, store)
    await b.restore()                                          # started before the login: nothing anywhere
    write(home, bundle("from-the-cli"))                        # the CLI signs in (and saves on its own)
    assert b.status()["saved_to_openbao"] is False
    keeper = asyncio.create_task(b.keep_fresh(tick=0.05))
    await asyncio.sleep(0.2)
    keeper.cancel()
    assert b.status()["saved_to_openbao"] is True
    assert json.loads(store.value)["tokens"]["access_token"] == "from-the-cli"


async def test_keep_fresh_follows_the_logins_own_age_not_the_process(home):
    """A restart (a new broker) doesn't reset the clock: the login's last_refresh decides."""
    calls = []
    write(home, bundle("old", hours_ago=7))                    # refreshed 7 h ago, by an earlier process
    b = AuthBroker(home, FakeStore(), refresh_every=6 * 3600, refresher=codex_refresh_to("new", calls))
    assert b.refresh_due()
    keeper = asyncio.create_task(b.keep_fresh(tick=0.02))
    await asyncio.sleep(0.2)
    keeper.cancel()
    assert calls == ["new"]                                    # once: the new login is 0 h old
    assert not b.refresh_due()


def test_not_due_when_fresh_or_signed_out(home):
    b = AuthBroker(home, None, refresh_every=6 * 3600)
    assert not b.refresh_due()                                 # signed out: nothing to refresh
    write(home, bundle("tok", hours_ago=1))
    assert not b.refresh_due()


# ---- restore at start (MA3) -----------------------------------------------------------------------
async def test_a_new_disk_is_restored_from_openbao(home):
    store = FakeStore(bundle("saved"))
    b = broker(home, store)
    await b.restore()
    assert b.headers()["authorization"] == "Bearer saved"
    assert stat.S_IMODE((home / "auth.json").stat().st_mode) == 0o600
    assert b.status()["saved_to_openbao"] and store.saves == 0


async def test_the_newer_copy_wins(home):
    write(home, bundle("disk", hours_ago=5))
    store = FakeStore(bundle("vault", hours_ago=1))
    b = broker(home, store)
    await b.restore()
    assert b.headers()["authorization"] == "Bearer vault"      # OpenBao's was refreshed later

    write(home, bundle("disk2", hours_ago=0))
    store2 = FakeStore(bundle("vault2", hours_ago=3))
    b2 = broker(home, store2)
    await b2.restore()
    assert b2.headers()["authorization"] == "Bearer disk2"     # never replaced by an older copy
    assert json.loads(store2.value)["tokens"]["access_token"] == "disk2"   # and OpenBao catches up


async def test_openbao_unreachable_at_start_keeps_the_disks(home):
    write(home, bundle("disk"))
    store = FakeStore()
    store.broken = True
    b = broker(home, store)
    await b.restore()
    assert b.headers()["authorization"] == "Bearer disk"
    assert b.status() == {"signed_in": True, "refreshed_hours_ago": 1.0, "saved_to_openbao": False}


async def test_signed_out_everywhere(home):
    b = broker(home, FakeStore())
    await b.restore()
    assert b.status() == {"signed_in": False, "refreshed_hours_ago": None, "saved_to_openbao": False}


# ---- OpenBao's API --------------------------------------------------------------------------------
class FakeTokens:
    async def token(self, audience):
        assert audience == "secrets-manager"
        return "auth-service-jwt"


class Bao:
    """OpenBao's side: JWT login, KV v2 read and write, revoke-self."""

    def __init__(self):
        self.kv: dict | None = None
        self.calls: list[str] = []
        self.sealed = False

    def __call__(self, r: httpx.Request) -> httpx.Response:
        self.calls.append(f"{r.method} {r.url.path}")
        if self.sealed:
            return httpx.Response(503)
        if r.url.path == "/v1/auth/auth-service/login":
            assert json.loads(r.content) == {"role": "agent-service", "jwt": "auth-service-jwt"}
            return httpx.Response(200, json={"auth": {"client_token": "bao-token"}})
        assert r.headers["x-vault-token"] == "bao-token"
        if r.url.path == "/v1/auth/token/revoke-self":
            return httpx.Response(204)
        assert r.url.path == "/v1/kv/data/services/agent-service/codex_auth"
        if r.method == "GET":
            return httpx.Response(404) if self.kv is None else httpx.Response(200, json={"data": {"data": self.kv}})
        self.kv = json.loads(r.content)["data"]
        return httpx.Response(200, json={"data": {"version": 1}})


async def test_openbao_store_round_trip():
    bao = Bao()
    store = OpenBaoStore("https://bao.test:8200", FakeTokens(), http=httpx.AsyncClient(transport=httpx.MockTransport(bao)))
    assert await store.load() is None
    await store.save("the-bundle")
    assert await store.load() == "the-bundle"
    assert bao.kv == {"value": "the-bundle"}
    assert bao.calls.count("POST /v1/auth/token/revoke-self") == 3      # one token per operation, revoked
    bao.sealed = True
    with pytest.raises(StoreError, match="sealed"):
        await store.load()


async def test_openbao_unreachable_is_a_store_error():
    def down(r):
        raise httpx.ConnectError("no route")
    store = OpenBaoStore("https://bao.test:8200", FakeTokens(), http=httpx.AsyncClient(transport=httpx.MockTransport(down)))
    with pytest.raises(StoreError, match="unreachable"):
        await store.save("x")
