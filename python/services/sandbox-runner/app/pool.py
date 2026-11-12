"""The pool of pods (sandbox-runner.md R6, R7, R9, R12, R13).

    starting ──(agent-runtime waits for its session file)──▶ warm ──claim──▶ claimed ──release or timeout──▶ gone
        └────────────────── not warm in time, or failed ─────────────────────────────────────────────────▶ gone

A pod is everything one sandbox needs, created in this order and destroyed in reverse:
    1. its workspace on the XFS disk, under quota (R13)
    2. its network: internal, its own /28, no address on the VM (R12)
    3. its egress proxy, the network's only way out (R5, R12)
    4. the sandbox: agent-runtime under gVisor, waiting for its session file (R7)
A claim only writes the session file, so it takes milliseconds. Pods are single-use: a released one
is destroyed and replaced, never reused (R6). On start, the runner destroys whatever it finds (R9).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.backend import LABEL, Backend, ContainerSpec
from app.config import Settings
from app.egress import LISTEN_PORT, new_sandbox_id, proxy_config
from app.workspaces import Workspaces

logger = logging.getLogger(__name__)
READY_MARK = "waiting for"            # agent-runtime's log line while it waits for its session file
REAP_EVERY = 5.0                      # seconds between hard-timeout checks
READY_POLL = 0.1                      # seconds between readiness checks while a pod starts
RETRY_DELAY = 1.0                     # after a pod fails to start, before trying again
GRACE_SECONDS = 30                    # added to a session's wall time before the hard timeout


class NoSandbox(Exception):
    """No warm pod within the claim's wait, and the pool is at its maximum."""


@dataclass
class Pod:
    id: uuid.UUID
    slot: int
    subnet: ipaddress.IPv4Network
    state: str = "starting"           # starting | warm | claimed | gone
    created: float = field(default_factory=time.monotonic)
    claimed: float | None = None
    deadline: float | None = None
    session_id: str | None = None

    @property
    def name(self) -> str:
        return self.id.hex[:12]

    @property
    def proxy_ip(self) -> str:
        return str(self.subnet.network_address + 2)

    @property
    def sandbox_ip(self) -> str:
        return str(self.subnet.network_address + 3)

    @property
    def gateway_url(self) -> str:
        """What the sandbox calls "the gateway": its proxy, by IP (R11)."""
        return f"http://{self.proxy_ip}:{LISTEN_PORT}"

    def describe(self, now: float) -> dict[str, Any]:
        return {"sandbox_id": str(self.id), "state": self.state, "subnet": str(self.subnet),
                "session_id": self.session_id, "age_s": round(now - self.created, 1),
                "expires_in_s": round(self.deadline - now, 1) if self.deadline else None}


class Pool:
    def __init__(self, settings: Settings, backend: Backend, workspaces: Workspaces):
        self.s, self.backend, self.workspaces = settings, backend, workspaces
        self.pods: dict[uuid.UUID, Pod] = {}
        self._slots = list(ipaddress.ip_network(settings.POD_SUBNETS).subnets(new_prefix=28))
        self._changed = asyncio.Condition()
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    # ---- lifecycle of the runner ---------------------------------------------------------------
    async def start(self) -> None:
        await self._clean_leftovers()
        await asyncio.to_thread(self.backend.ensure_image, self.s.SANDBOX_IMAGE)
        await asyncio.to_thread(self.backend.ensure_image, self.s.PROXY_IMAGE)
        await asyncio.to_thread(self.backend.ensure_egress_network, self.s.EGRESS_NETWORK)
        self._spawn(self._reaper())
        await self.refill()

    async def stop(self) -> None:
        """Stop taking claims and destroy every pod (claimed ones too: R9 would anyway)."""
        self._closing = True
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*(self._destroy(p) for p in list(self.pods.values())), return_exceptions=True)

    async def _clean_leftovers(self) -> None:
        containers, networks = await asyncio.to_thread(self.backend.leftovers)
        for name in containers:
            await asyncio.to_thread(self.backend.remove_container, name)
        for name in networks:
            await asyncio.to_thread(self.backend.remove_network, name)
        dirs = await asyncio.to_thread(self.workspaces.leftovers)
        for sid in dirs:
            await asyncio.to_thread(self.workspaces.remove_leftover, sid)
        if containers or networks or dirs:
            logger.warning("start: removed %d containers, %d networks, %d workspaces left from before (R9)",
                           len(containers), len(networks), len(dirs))

    # ---- the pool --------------------------------------------------------------------------------
    def count(self, *states: str) -> int:
        return sum(p.state in states for p in self.pods.values())

    async def refill(self) -> None:
        """Start pods until WARM are ready or starting, within MAX in total."""
        if self._closing:
            return
        while self.count("warm", "starting") < self.s.WARM and len(self.pods) < self.s.MAX:
            slot = self._free_slot()
            if slot is None:
                logger.error("no free pod subnet in %s", self.s.POD_SUBNETS)
                return
            pod = Pod(id=new_sandbox_id(), slot=slot, subnet=self._slots[slot])
            self.pods[pod.id] = pod
            self._spawn(self._warm_up(pod))

    def _free_slot(self) -> int | None:
        used = {p.slot for p in self.pods.values()}
        return next((i for i in range(len(self._slots)) if i not in used), None)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- one pod -------------------------------------------------------------------------------
    def _session_dir(self, pod: Pod) -> Path:
        return self.s.STATE_ROOT / "sessions" / str(pod.id)

    def _proxy_conf(self, pod: Pod) -> Path:
        return self.s.STATE_ROOT / "proxies" / f"{pod.id}.conf"

    async def _warm_up(self, pod: Pod) -> None:
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self._create, pod)
            deadline = t0 + self.s.READY_TIMEOUT_SECONDS
            # Warm = the sandbox waits for its session file AND its proxy runs (else no way out).
            while READY_MARK not in await asyncio.to_thread(self.backend.logs, f"sandbox-{pod.name}"):
                if time.monotonic() > deadline:
                    raise TimeoutError(f"not ready in {self.s.READY_TIMEOUT_SECONDS}s")
                await asyncio.sleep(READY_POLL)
            if not await asyncio.to_thread(self.backend.running, f"proxy-{pod.name}"):
                raise RuntimeError("its egress proxy isn't running")
        except Exception as exc:
            logger.error("pod %s failed to start: %r", pod.name, exc)
            await self._destroy(pod)
            await asyncio.sleep(RETRY_DELAY)           # don't spin on a persistent failure
            await self.refill()
            return
        async with self._changed:
            pod.state = "warm"
            self._changed.notify_all()
        logger.info("pod %s warm in %d ms", pod.name, round((time.monotonic() - t0) * 1000))

    def _create(self, pod: Pod) -> None:
        """Blocking: the four parts of a pod, in order (see the module docstring)."""
        labels = {LABEL: "pod", f"{LABEL}.sandbox-id": str(pod.id)}
        workspace = self.workspaces.create(pod.id, pod.slot)
        net = f"pod-{pod.name}"
        self.backend.create_pod_network(net, str(pod.subnet))

        conf = self._proxy_conf(pod)
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(proxy_config(pod.id, self.s.GATEWAY_URL))
        conf.chmod(0o444)
        self.backend.run(ContainerSpec(
            name=f"proxy-{pod.name}", image=self.s.PROXY_IMAGE, labels=labels,
            networks=[(net, pod.proxy_ip), (self.s.EGRESS_NETWORK, None)],
            binds=[(str(conf), "/etc/nginx/nginx.conf", True)], tmpfs={"/tmp": "size=16m"},
            memory_mb=64, cpus=0.5, pids=64))

        session_dir = self._session_dir(pod)
        session_dir.mkdir(parents=True, exist_ok=True)
        session_dir.chmod(0o755)
        self.backend.run(ContainerSpec(
            name=f"sandbox-{pod.name}", image=self.s.SANDBOX_IMAGE, labels=labels, runtime=self.s.SANDBOX_RUNTIME,
            user=f"{self.s.SANDBOX_UID}:{self.s.SANDBOX_UID}", networks=[(net, pod.sandbox_ip)],
            binds=[(str(session_dir), "/run/session", True), (str(workspace), "/workspace", False)],
            tmpfs={"/tmp": f"exec,mode=1777,size={self.s.TMP_MB}m"},
            memory_mb=self.s.MEMORY_MB, cpus=self.s.CPUS, pids=self.s.PIDS))

    async def _destroy(self, pod: Pod) -> None:
        """Everything the pod had, in reverse order; tolerant of parts that never got created."""
        pod.state = "gone"
        steps = [
            lambda: self.backend.remove_container(f"sandbox-{pod.name}"),
            lambda: self.backend.remove_container(f"proxy-{pod.name}"),
            lambda: self.backend.remove_network(f"pod-{pod.name}"),
            lambda: self.workspaces.remove(pod.id, pod.slot),
            lambda: self._proxy_conf(pod).unlink(missing_ok=True),
            lambda: _rmtree(self._session_dir(pod)),
        ]
        for step in steps:
            try:
                await asyncio.to_thread(step)
            except Exception as exc:
                logger.error("pod %s: cleanup step failed: %r", pod.name, exc)
        self.pods.pop(pod.id, None)
        async with self._changed:
            self._changed.notify_all()

    # ---- claims and releases -------------------------------------------------------------------
    async def claim(self, session_id: str, capability: str, profile: dict[str, Any]) -> Pod:
        """Hand over a warm pod: write its session file (R7). Waits briefly for a pod still warming up."""
        if self._closing:
            raise NoSandbox("the runner is stopping")
        deadline = time.monotonic() + self.s.CLAIM_WAIT_SECONDS
        async with self._changed:
            while True:
                pod = min((p for p in self.pods.values() if p.state == "warm"), key=lambda p: p.created, default=None)
                if pod is not None:
                    break
                await self.refill()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.count("starting") == 0:
                    raise NoSandbox(f"no sandbox available ({len(self.pods)}/{self.s.MAX} in use)")
                try:
                    await asyncio.wait_for(self._changed.wait(), remaining)
                except TimeoutError:
                    pass
            pod.state = "claimed"
        wall = int((profile.get("limits") or {}).get("wall_seconds", self.s.MAX_SESSION_SECONDS))
        pod.claimed, pod.session_id = time.monotonic(), session_id
        pod.deadline = pod.claimed + min(wall, self.s.MAX_SESSION_SECONDS) + GRACE_SECONDS
        session = {"session_id": session_id, "capability": capability, "gateway_url": pod.gateway_url,
                   "profile": profile}
        await asyncio.to_thread(_write_atomically, self._session_dir(pod) / "session.json", json.dumps(session))
        logger.info("pod %s claimed by session %s", pod.name, session_id)
        await self.refill()
        return pod

    async def release(self, sandbox_id: uuid.UUID) -> bool:
        pod = self.pods.get(sandbox_id)
        if pod is None or pod.state != "claimed":
            return False
        await self._destroy(pod)
        logger.info("pod %s released", pod.name)
        await self.refill()
        return True

    async def _reaper(self) -> None:
        """The hard timeout: a claimed pod past its deadline is destroyed, released or not."""
        while True:
            await asyncio.sleep(REAP_EVERY)
            if self._closing:
                return
            now = time.monotonic()
            for pod in [p for p in self.pods.values() if p.state == "claimed" and p.deadline and now > p.deadline]:
                logger.warning("pod %s passed its hard timeout: destroyed", pod.name)
                await self._destroy(pod)
            for pod in [p for p in self.pods.values() if p.state == "warm"]:
                if not await asyncio.to_thread(self.backend.running, f"proxy-{pod.name}"):
                    logger.error("pod %s: its proxy stopped while warm: replaced", pod.name)
                    await self._destroy(pod)
            await self.refill()


def _write_atomically(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.chmod(0o444)                  # readable by the sandbox's uid; mounted read-only anyway
    os.replace(tmp, path)             # the runtime sees all of it or nothing


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)
