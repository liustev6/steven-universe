"""OpenBao secrets (payload format = deploy.sh's), the host allowlist, the SSH command."""

import asyncio
import base64
from pathlib import Path

import httpx
import pytest

from app.hosts import HostsError, load_hosts
from app.openbao import OpenBaoSecrets, SecretsError, formatted
from app.ssh import ssh_command

from .conftest import HOST_KEY

BAO = "https://bao.lan.liustev6.ca:8200/v1"


class FakeTokens:
    def __init__(self):
        self.audiences = []

    async def token(self, audience):
        self.audiences.append(audience)
        return "auth-service-jwt"


def bao(values: dict[str, str], *, login_status=200, log=None):
    def handler(request: httpx.Request) -> httpx.Response:
        (log if log is not None else []).append((request.method, str(request.url)))
        if request.url.path == "/v1/auth/auth-service/login":
            assert request.read() == b'{"role":"deployer","jwt":"auth-service-jwt"}'
            if login_status != 200:
                return httpx.Response(login_status, json={"errors": ["nope"]})
            return httpx.Response(200, json={"auth": {"client_token": "bao-token"}})
        if request.url.path == "/v1/auth/token/revoke-self":
            return httpx.Response(204)
        assert request.headers["X-Vault-Token"] == "bao-token"
        path = request.url.path.removeprefix("/v1/kv/data/")
        if path not in values:
            return httpx.Response(403, json={"errors": ["permission denied"]})
        return httpx.Response(200, json={"data": {"data": {"value": values[path]}}})
    return httpx.MockTransport(handler)


def fetch(settings, transport, declared, tokens=None):
    async def go():
        async with httpx.AsyncClient(transport=transport) as http:
            return await OpenBaoSecrets(settings, tokens or FakeTokens(), http=http).payload(declared)
    return asyncio.run(go())


def test_payload_matches_deploy_sh(settings):
    log, tokens = [], FakeTokens()
    out = fetch(settings, bao({"shared/cloudflare_dns_token": "tok", "services/x/key": "k"}, log=log), [
        {"from": "shared/cloudflare_dns_token", "to": "/root/.secrets/cloudflare.ini", "owner": "0:0",
         "format": "dns_cloudflare_api_token = {value}\n"},
        {"from": "services/x/key", "to": "/etc/x/credentials/key", "owner": "10001:10001"},
    ], tokens)
    lines = out.decode().splitlines()
    to, owner, b64 = lines[0].split("\t")
    assert (to, owner, base64.b64decode(b64)) == ("/root/.secrets/cloudflare.ini", "0:0", b"dns_cloudflare_api_token = tok\n")
    assert base64.b64decode(lines[1].split("\t")[2]) == b"k"
    assert tokens.audiences == ["secrets-manager"]
    assert log[-1] == ("POST", f"{BAO}/auth/token/revoke-self")   # always revoked


def test_undeclared_secret_names_tofu_apply(settings):
    log = []
    with pytest.raises(SecretsError, match="tofu apply"):
        fetch(settings, bao({}, log=log), [{"from": "services/x/new", "to": "/etc/x/new"}])
    assert log[-1][1].endswith("/auth/token/revoke-self")


def test_sealed(settings):
    with pytest.raises(SecretsError, match="sealed"):
        fetch(settings, bao({}, login_status=503), [{"from": "shared/a", "to": "/a"}])


def test_no_secrets_no_login(settings):
    assert fetch(settings, httpx.MockTransport(lambda r: pytest.fail("no request expected")), []) == b""


def test_formatted():
    assert formatted("{value}", "v") == "v"
    assert formatted("a = {value}\n", "v") == "a = v\n"
    with pytest.raises(SecretsError):
        formatted("no placeholder", "v")


def test_hosts(tmp_path, hosts_file):
    h = load_hosts(hosts_file)["demo-host"]
    assert (h.address, h.user, h.host_key) == ("192.0.2.10", "root", HOST_KEY)
    bad = tmp_path / "bad.yaml"
    bad.write_text('hosts:\n  x:\n    address: "1.2.3.4; rm -rf /"\n    host_key: "' + HOST_KEY + '"\n')
    with pytest.raises(HostsError, match="address"):
        load_hosts(bad)
    with pytest.raises(HostsError):
        load_hosts(tmp_path / "missing.yaml")


def test_ssh_command(hosts_file):
    h = load_hosts(hosts_file)["demo-host"]
    cmd = ssh_command(h, Path("/run/secrets/ssh_key"), Path("/tmp/kh"), "demo", "a" * 40, None)
    assert cmd[-2:] == ["root@192.0.2.10", "deploy demo " + "a" * 40]
    assert "StrictHostKeyChecking=yes" in cmd and "UserKnownHostsFile=/tmp/kh" in cmd and "BatchMode=yes" in cmd
    with_digest = ssh_command(h, Path("k"), Path("kh"), "demo", "a" * 40, "sha256:" + "b" * 64)
    assert with_digest[-1].endswith(" sha256:" + "b" * 64)


def test_ssh_runner_streams_and_passes_stdin(tmp_path, hosts_file, monkeypatch):
    """The real SSHRunner against a stand-in `ssh`: stdin payload, line streaming, exit code, timeout."""
    from app.ssh import TIMED_OUT, SSHRunner

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ssh"
    fake.write_text(f"""#!/bin/sh
for a; do last="$a"; done
cat > {tmp_path}/stdin
grep -o 'UserKnownHostsFile=[^ ]*' <<ARGS > {tmp_path}/kh_arg
$*
ARGS
printf '\\033[0;32mStep 1/7: Commit\\033[0m\\n'
echo "request: $last"
case "$last" in *slow*) sleep 5;; esac
exit 3
""")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    host = load_hosts(hosts_file)["demo-host"]

    async def go(service, timeout):
        lines = []

        async def on_line(line):
            lines.append(line)
        code = await SSHRunner(Path("/k"), timeout).run(host, service, "a" * 40, None, b"payload\n", on_line)
        return code, lines

    code, lines = asyncio.run(go("demo", 10))
    assert code == 3
    assert lines == ["Step 1/7: Commit", "request: deploy demo " + "a" * 40]   # colors stripped
    assert (tmp_path / "stdin").read_bytes() == b"payload\n"
    known = Path((tmp_path / "kh_arg").read_text().strip().split("=", 1)[1])
    assert not known.exists()                                                 # temp known_hosts removed

    code, lines = asyncio.run(go("slow", 0.5))
    assert code == TIMED_OUT and "timed out" in lines[-1]
