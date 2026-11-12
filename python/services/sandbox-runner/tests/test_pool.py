"""The pool's lifecycle (R6, R7, R9, R12, R13) against fake Docker and workspaces."""

import asyncio
import json
import stat
import uuid

import pytest

from app import pool as pool_module
from app.pool import NoSandbox
from tests.conftest import CAP, PROFILE, until


def run(coro):
    return asyncio.run(coro)


def test_start_cleans_leftovers_then_warms_whole_pods(make_pool):
    pool = make_pool()
    old = uuid.uuid4()
    pool.backend.left = (["sandbox-old", "proxy-old"], ["pod-old"])
    pool.workspaces.left = [old]

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        await pool.stop()
    run(go())
    b = pool.backend
    assert b.removed_containers[:2] == ["sandbox-old", "proxy-old"] and b.removed_networks[0] == "pod-old"
    assert pool.workspaces.removed_leftovers == [old]
    assert b.images == {"ghcr.io/x/agent-runtime@sha256:abc", "nginxinc/nginx-unprivileged:stable-alpine"}
    assert b.egress == {"sandbox-egress"}


def test_a_pod_is_network_proxy_sandbox_and_workspace(make_pool):
    pool = make_pool()

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        pods = list(pool.pods.values())
        specs = dict(pool.backend.containers)
        networks = dict(pool.backend.networks)
        await pool.stop()
        return pods, specs, networks
    pods, specs, networks = run(go())
    assert sorted(networks.values()) == ["172.29.0.0/28", "172.29.0.16/28"]      # one /28 each
    for pod in pods:
        net = f"pod-{pod.name}"
        proxy, _ = specs[f"proxy-{pod.name}"]
        sandbox, _ = specs[f"sandbox-{pod.name}"]
        assert proxy.networks == [(net, pod.proxy_ip), ("sandbox-egress", None)]   # its pod, and the way out
        conf = proxy.binds[0]
        assert conf[1:] == ("/etc/nginx/nginx.conf", True)
        assert sandbox.runtime == "runsc" and sandbox.user == "10001:10001"
        assert sandbox.networks == [(net, pod.sandbox_ip)]                        # its pod only
        assert ("/run/session", True) == sandbox.binds[0][1:] and ("/workspace", False) == sandbox.binds[1][1:]
        assert sandbox.tmpfs["/tmp"].endswith("size=512m") and sandbox.memory_mb == 2048
        assert pod.gateway_url == f"http://{pod.proxy_ip}:8080"


def test_proxy_config_is_written_for_its_pod(make_pool, settings):
    pool = make_pool()

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        pod = next(iter(pool.pods.values()))
        conf = (settings.STATE_ROOT / "proxies" / f"{pod.id}.conf")
        text, mode = conf.read_text(), stat.S_IMODE(conf.stat().st_mode)
        await pool.stop()
        return pod, text, mode
    pod, text, mode = run(go())
    assert f'X-Sandbox-Id "{pod.id}"' in text and "proxy_pass http://gw:8080;" in text and mode == 0o444


def test_claim_writes_the_session_file_and_refills(make_pool, settings):
    pool = make_pool()

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        pod = await pool.claim("s_1", CAP, PROFILE)
        await until(lambda: pool.count("warm") == 2)               # replaced: 2 warm + 1 claimed = MAX
        f = settings.STATE_ROOT / "sessions" / str(pod.id) / "session.json"
        content, mode = json.loads(f.read_text()), stat.S_IMODE(f.stat().st_mode)
        state = (pool.count("claimed"), len(pool.pods))
        await pool.stop()
        return pod, content, mode, state
    pod, content, mode, state = run(go())
    assert content == {"session_id": "s_1", "capability": CAP, "gateway_url": pod.gateway_url, "profile": PROFILE}
    assert mode == 0o444 and pod.state == "gone" and state == (1, 3)     # gone: stop() destroyed it


def test_claims_beyond_max_get_no_sandbox(make_pool):
    pool = make_pool()

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        for i in range(3):
            await pool.claim(f"s_{i}", CAP, PROFILE)
        with pytest.raises(NoSandbox):
            await pool.claim("s_too_many", CAP, PROFILE)
        await pool.stop()
    run(go())


def test_a_claim_waits_for_a_pod_still_warming(make_pool):
    pool = make_pool(WARM=1, MAX=2)

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 1)
        await pool.claim("s_1", CAP, PROFILE)                      # takes the warm one; a new one starts
        second = await pool.claim("s_2", CAP, PROFILE)             # waits for it instead of failing
        await pool.stop()
        return second
    assert run(go()).session_id == "s_2"


def test_release_destroys_everything_then_refills(make_pool, settings):
    pool = make_pool()

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        pod = await pool.claim("s_1", CAP, PROFILE)
        assert await pool.release(pod.id)
        assert not await pool.release(pod.id)                      # gone: never reused
        await until(lambda: pool.count("warm") == 2)
        state = dict(pool.backend.containers), dict(pool.backend.networks), dict(pool.workspaces.live)
        await pool.stop()
        return pod, state
    pod, (containers, networks, live) = run(go())
    assert f"sandbox-{pod.name}" not in containers and f"proxy-{pod.name}" not in containers
    assert f"pod-{pod.name}" not in networks and pod.id not in live
    assert not (settings.STATE_ROOT / "sessions" / str(pod.id)).exists()
    assert not (settings.STATE_ROOT / "proxies" / f"{pod.id}.conf").exists()


def test_hard_timeout_destroys_a_claimed_pod(make_pool, monkeypatch):
    monkeypatch.setattr(pool_module, "GRACE_SECONDS", 0)
    pool = make_pool()

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        pod = await pool.claim("s_1", CAP, {"limits": {"wall_seconds": 0}})
        await until(lambda: pod.id not in pool.pods)
        await pool.stop()
    run(go())


def test_a_pod_that_never_gets_ready_is_destroyed_and_replaced(make_pool):
    pool = make_pool()
    pool.backend.never_ready = 1

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        removed = list(pool.backend.removed_containers)
        await pool.stop()
        return removed
    removed = run(go())
    assert any(n.startswith("sandbox-") for n in removed) and any(n.startswith("proxy-") for n in removed)


def test_subnets_are_reused_only_after_release(make_pool):
    pool = make_pool(WARM=1, MAX=1)

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 1)
        first = await pool.claim("s_1", CAP, PROFILE)
        slot = first.slot
        await pool.release(first.id)
        await until(lambda: pool.count("warm") == 1)
        again = next(iter(pool.pods.values()))
        await pool.stop()
        return slot, again
    slot, again = run(go())
    assert again.slot == slot                                       # the freed /28 comes back


def test_a_warm_pod_whose_proxy_dies_is_replaced(make_pool):
    pool = make_pool()

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 2)
        victim = next(iter(pool.pods.values()))
        pool.backend.dead_proxies.add(f"proxy-{victim.name}")
        await until(lambda: victim.id not in pool.pods and pool.count("warm") == 2)
        await pool.stop()
    run(go())
