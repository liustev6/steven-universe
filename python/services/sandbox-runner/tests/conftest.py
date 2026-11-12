"""Fakes for Docker and the XFS workspaces: they record what the runner asks for."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from app import pool as pool_module
from app.backend import ContainerSpec
from app.config import Settings
from app.pool import READY_MARK, Pool
from app.templates import Template

TEMPLATES = {
    "chat": Template(image="ghcr.io/x/agent-runtime@sha256:chat", warm=2, memory_mb=1024, workspace_mb=1024),
    "dev": Template(image="ghcr.io/x/agent-runtime-dev@sha256:dev", warm=0, memory_mb=4096, workspace_mb=20480),
}


class FakeBackend:
    def __init__(self):
        self.images, self.egress, self.networks, self.containers = set(), set(), {}, {}
        self.removed_containers, self.removed_networks = [], []
        self.never_ready = 0                          # the next N sandboxes never log READY_MARK
        self.dead_proxies = set()                     # proxies reported as not running
        self.left = ([], [])                          # leftovers to report at start

    def ensure_image(self, ref): self.images.add(ref)
    def ensure_egress_network(self, name): self.egress.add(name)
    def create_pod_network(self, name, subnet): self.networks[name] = subnet

    def run(self, spec: ContainerSpec):
        ready = True
        if spec.name.startswith("sandbox-") and self.never_ready:
            self.never_ready -= 1
            ready = False
        self.containers[spec.name] = (spec, ready)

    def logs(self, name):
        spec_ready = self.containers.get(name)
        return f"core INFO {READY_MARK} /run/session/session.json" if spec_ready and spec_ready[1] else ""

    def running(self, name):
        return name in self.containers and name not in self.dead_proxies

    def remove_container(self, name):
        self.removed_containers.append(name)
        self.containers.pop(name, None)

    def remove_network(self, name):
        self.removed_networks.append(name)
        self.networks.pop(name, None)

    def leftovers(self): return self.left


class FakeWorkspaces:
    def __init__(self, root: Path):
        self.root, self.live, self.left, self.removed_leftovers = root, {}, [], []

    def create(self, sandbox_id, slot, quota_mb):
        path = self.root / str(sandbox_id)
        path.mkdir(parents=True)
        self.live[sandbox_id] = (slot, quota_mb)
        return path

    def remove(self, sandbox_id, slot): self.live.pop(sandbox_id, None)
    def leftovers(self): return self.left
    def remove_leftover(self, sandbox_id): self.removed_leftovers.append(sandbox_id)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(pool_module, "READY_POLL", 0.01)
    monkeypatch.setattr(pool_module, "RETRY_DELAY", 0.01)
    monkeypatch.setattr(pool_module, "REAP_EVERY", 0.05)


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "token").write_text("dev-token\n")
    return Settings(GATEWAY_URL="http://gw:8080", MAX=6, MAX_PER_TEMPLATE=3,
                    CLAIM_WAIT_SECONDS=0.5, READY_TIMEOUT_SECONDS=0.3,
                    WORKSPACE_ROOT=tmp_path / "ws", STATE_ROOT=tmp_path / "state", DEV_TOKEN_FILE=tmp_path / "token")


@pytest.fixture
def make_pool(settings, tmp_path):
    def make(templates=None, **overrides):
        s = settings.model_copy(update=overrides)
        return Pool(s, templates or TEMPLATES, FakeBackend(), FakeWorkspaces(tmp_path / "ws"))
    return make


async def until(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


PROFILE = {"name": "voice", "limits": {"wall_seconds": 600}}
CAP = "c" * 32
