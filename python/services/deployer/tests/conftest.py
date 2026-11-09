"""Fixtures: a real git "GitHub" in a temp dir, fake secrets and a fake host, a fake GitHub signer."""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.deploys import Deployer
from app.repo import Repo

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA = (REPO_ROOT / "infra/services/deploy.schema.json").read_text()
HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFTFg1f4ffXCIyyiXpO51jqJuj5NeBQdFBNMKReN5H1a"

MANIFEST = """\
service: demo
host: demo-host
pipeline: {pipeline}
code: python/services/demo
compose: python/services/demo/compose.yaml
health: https://demo.lan.example/health
secrets:
  - from: services/demo/api_key
    to: /etc/demo/credentials/api_key
    owner: "10001:10001"
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class Origin:
    """A stand-in for GitHub: a normal repository the deployer clones from."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir()
        git(path, "init", "-q", "-b", "main")
        git(path, "config", "user.email", "t@example.com")
        git(path, "config", "user.name", "t")

    def commit(self, files: dict[str, str], message: str = "change") -> str:
        for name, text in files.items():
            f = self.path / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", message)
        return git(self.path, "rev-parse", "HEAD")


@pytest.fixture
def origin(tmp_path) -> Origin:
    o = Origin(tmp_path / "origin")
    o.commit({"infra/services/deploy.schema.json": SCHEMA, "python/services/demo/app.py": "v1\n",
              "infra/services/demo/deploy.yaml": MANIFEST.format(pipeline="true")}, "first")
    return o


@pytest.fixture
def hosts_file(tmp_path) -> Path:
    p = tmp_path / "hosts.yaml"
    p.write_text(f'hosts:\n  demo-host:\n    address: 192.0.2.10\n    user: root\n    host_key: "{HOST_KEY}"\n')
    return p


class FakeSecrets:
    def __init__(self):
        self.calls: list[list[dict]] = []
        self.error: Exception | None = None

    async def payload(self, declared):
        self.calls.append(list(declared))
        if self.error:
            raise self.error
        return b"".join(f"{s['to']}\towner\tdmFsdWU=\n".encode() for s in declared)


class FakeHost:
    """Plays deploy-target: per sha, the lines it prints and its exit code."""

    def __init__(self):
        self.running: str | None = None
        self.calls: list[tuple[str, str, str | None, bytes]] = []
        self.fail: dict[str, int] = {}     # sha -> fail at this step (exit 1)

    async def run(self, host, service, sha, digest, payload, on_line) -> int:
        self.calls.append((host.name, sha, digest, payload))
        await on_line("Step 1/7: Commit")
        if self.running:
            await on_line(f"  previous: {self.running}")
        steps = ["Config and secrets", "certbot hook", "Pulling image", "Starting container", "Checking", "Smoke test"]
        for n, name in enumerate(steps, start=2):
            await on_line(f"Step {n}/7: {name}")
            if self.fail.get(sha) == n:
                await on_line("✗ it broke")
                if n >= 5:
                    self.running = sha  # the new container was started
                return 1
        self.running = sha
        return 0


@pytest.fixture
def fake_secrets() -> FakeSecrets:
    return FakeSecrets()


@pytest.fixture
def fake_host() -> FakeHost:
    return FakeHost()


@pytest.fixture
def deployer(tmp_path, origin, hosts_file, fake_secrets, fake_host) -> Deployer:
    return Deployer(repo=Repo(str(origin.path), tmp_path / "state" / "repo.git"), secrets=fake_secrets,
                    runner=fake_host, hosts_file=hosts_file, audit_log=tmp_path / "state" / "audit.log")


@pytest.fixture
def settings(tmp_path, hosts_file) -> Settings:
    key = tmp_path / "ssh_key"
    key.write_text("not a real key\n")
    return Settings(GITHUB_REPOSITORY_ID="1102380459", GITHUB_OWNER_ID="90568652",
                    GITHUB_WORKFLOW_REF="liustev6/steven-universe/.github/workflows/deploy.yml@refs/heads/main",
                    STATE_DIR=tmp_path / "state", HOSTS_FILE=hosts_file, SSH_KEY_FILE=key)


class GitHubSigner:
    """Signs tokens like GitHub Actions does; serves its JWKS through an httpx MockTransport."""

    def __init__(self, kid: str = "gh-key-1"):
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": kid, "use": "sig", "alg": "RS256"}]}
        self.jwks_requests = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == "https://token.actions.githubusercontent.com/.well-known/jwks"
            self.jwks_requests += 1
            return httpx.Response(200, json=self.jwks)
        return httpx.MockTransport(handler)

    def claims(self, commit: str, /, **overrides: Any) -> dict[str, Any]:
        now = int(time.time())
        c = {
            "iss": "https://token.actions.githubusercontent.com", "aud": "steven-universe-deployer",
            "sub": "repo:liustev6/steven-universe:environment:production", "jti": str(uuid.uuid4()),
            "iat": now, "nbf": now, "exp": now + 300,
            "repository": "liustev6/steven-universe", "repository_id": "1102380459",
            "repository_owner_id": "90568652", "ref": "refs/heads/main", "environment": "production",
            "job_workflow_ref": "liustev6/steven-universe/.github/workflows/deploy.yml@refs/heads/main",
            "runner_environment": "github-hosted", "sha": commit, "actor": "liustev6", "run_id": "42",
            "workflow": "build",
        }
        c.update(overrides)
        return {k: v for k, v in c.items() if v is not None}

    def token(self, commit: str, /, *, key=None, kid: str | None = None, alg: str = "RS256", **overrides: Any) -> str:
        return jwt.encode(self.claims(commit, **overrides), key or self.key, algorithm=alg,
                          headers={"kid": kid or self.kid})


@pytest.fixture
def github() -> GitHubSigner:
    return GitHubSigner()
