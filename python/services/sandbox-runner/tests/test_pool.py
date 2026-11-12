"""The pool's lifecycle (R6, R7, R9, R12, R13, R14) against fake Docker and workspaces."""

import asyncio
import json
import stat
import uuid

import pytest

from app import pool as pool_module
from app.pool import NoSandbox, UnknownTemplate
from app.templates import Template
from tests.conftest import CAP, PROFILE, TEMPLATES, until


def run(coro):
    return asyncio.run(coro)


async def started(pool, warm_chat=2):
    await pool.start()
    await until(lambda: pool.count("warm", template="chat") == warm_chat)


def test_start_cleans_leftovers_then_warms_each_template(make_pool):
    pool = make_pool()
    old = uuid.uuid4()
    pool.backend.left = (["sandbox-old", "proxy-old"], ["pod-old"])
    pool.workspaces.left = [old]

    async def go():
        await started(pool)
        counts = {t: pool.count("warm", "starting", template=t) for t in TEMPLATES}
        await pool.stop()
        return counts
    counts = run(go())
    b = pool.backend
    assert b.removed_containers[:2] == ["sandbox-old", "proxy-old"] and b.removed_networks[0] == "pod-old"
    assert pool.workspaces.removed_leftovers == [old]
    assert b.images == {t.image for t in TEMPLATES.values()} | {"nginxinc/nginx-unprivileged:stable-alpine"}
    assert counts == {"chat": 2, "dev": 0}                          # warm: 0 means on demand only


def test_a_pod_is_built_from_its_template(make_pool):
    pool = make_pool()

    async def go():
        await started(pool)
        dev = await pool.claim("dev", "s_dev", CAP, PROFILE)      # started on demand
        chat = next(p for p in pool.pods.values() if p.template == "chat")
        specs, networks, live = dict(pool.backend.containers), dict(pool.backend.networks), dict(pool.workspaces.live)
        await pool.stop()
        return chat, dev, specs, networks, live
    chat, dev, specs, networks, live = run(go())
    for pod, t in ((chat, TEMPLATES["chat"]), (dev, TEMPLATES["dev"])):
        net = f"pod-{pod.name}"
        proxy, _ = specs[f"proxy-{pod.name}"]
        sandbox, _ = specs[f"sandbox-{pod.name}"]
        assert networks[net] == str(pod.subnet) and pod.subnet.prefixlen == 28
        assert proxy.networks == [(net, pod.proxy_ip), ("sandbox-egress", None)]
        assert sandbox.image == t.image and sandbox.runtime == "runsc" and sandbox.memory_mb == t.memory_mb
        assert sandbox.labels["dev.steven-universe.sandbox-runner.template"] == pod.template
        assert sandbox.networks == [(net, pod.sandbox_ip)] and sandbox.user == "10001:10001"
        assert ("/run/session", True) == sandbox.binds[0][1:] and ("/workspace", False) == sandbox.binds[1][1:]
        assert live[pod.id] == (pod.slot, t.workspace_mb)           # the template's quota


def test_proxy_config_is_written_for_its_pod(make_pool, settings):
    pool = make_pool()

    async def go():
        await started(pool)
        pod = next(iter(pool.pods.values()))
        conf = settings.STATE_ROOT / "proxies" / f"{pod.id}.conf"
        text, mode = conf.read_text(), stat.S_IMODE(conf.stat().st_mode)
        await pool.stop()
        return pod, text, mode
    pod, text, mode = run(go())
    assert f'X-Sandbox-Id "{pod.id}"' in text and "proxy_pass http://gw:8080;" in text and mode == 0o444


def test_claim_writes_the_session_file_and_refills_its_template(make_pool, settings):
    pool = make_pool()

    async def go():
        await started(pool)
        pod = await pool.claim("chat", "s_1", CAP, PROFILE)
        await until(lambda: pool.count("warm", template="chat") == 2)
        f = settings.STATE_ROOT / "sessions" / str(pod.id) / "session.json"
        content, mode = json.loads(f.read_text()), stat.S_IMODE(f.stat().st_mode)
        await pool.stop()
        return pod, content, mode
    pod, content, mode = run(go())
    assert content == {"session_id": "s_1", "capability": CAP, "gateway_url": pod.gateway_url, "profile": PROFILE}
    assert mode == 0o444 and pod.template == "chat"


def test_unknown_template_is_refused(make_pool):
    pool = make_pool()

    async def go():
        await started(pool)
        with pytest.raises(UnknownTemplate):
            await pool.claim("ghcr.io/evil/image", "s", CAP, PROFILE)
        await pool.stop()
    run(go())


def test_at_most_three_of_a_template_while_others_still_get_room(make_pool):
    pool = make_pool()

    async def go():
        await started(pool)
        for i in range(3):
            await pool.claim("chat", f"s_{i}", CAP, PROFILE)
        with pytest.raises(NoSandbox):
            await pool.claim("chat", "s_4th_chat", CAP, PROFILE)    # its cap of 3, though the VM has room
        dev = await pool.claim("dev", "s_dev", CAP, PROFILE)         # another kind still gets one
        state = pool.count("claimed", template="chat"), pool.count("warm", "starting", template="chat")
        await pool.stop()
        return dev, state
    dev, (claimed_chat, spare_chat) = run(go())
    assert dev.template == "dev" and claimed_chat == 3 and spare_chat == 0   # no warm chat beyond its cap


def test_six_in_all_whatever_the_templates(make_pool):
    templates = {"a": Template(image="img-a", warm=1), "b": Template(image="img-b", warm=1),
                 "c": Template(image="img-c", warm=1)}
    pool = make_pool(templates=templates)

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 3)
        for name in ("a", "a", "b", "b", "c", "c"):
            await pool.claim(name, f"s_{name}", CAP, PROFILE)
        assert pool.count("claimed") == 6
        with pytest.raises(NoSandbox):
            await pool.claim("a", "s_seventh", CAP, PROFILE)          # a has room (2/3), the VM doesn't (6/6)
        await pool.stop()
    run(go())


def test_a_claim_evicts_a_warm_spare_of_another_template_when_the_vm_is_full(make_pool):
    pool = make_pool(templates={"chat": Template(image="img-chat", warm=3), "dev": Template(image="img-dev")}, MAX=3)

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm", template="chat") == 3)    # the VM is full of chat spares
        chat = await pool.claim("chat", "s_chat", CAP, PROFILE)
        dev = await pool.claim("dev", "s_dev", CAP, PROFILE)            # evicts one chat spare, never a claimed pod
        await until(lambda: len(pool.pods) == 3)
        state = chat.state, dev.state, pool.count("warm", template="chat")
        await pool.stop()
        return state
    assert run(go()) == ("claimed", "claimed", 1)


def test_on_demand_claim_waits_for_its_pod(make_pool):
    pool = make_pool()

    async def go():
        await started(pool)
        assert pool.count("warm", "starting", template="dev") == 0
        pod = await pool.claim("dev", "s_dev", CAP, PROFILE)
        await pool.stop()
        return pod
    pod = run(go())
    assert pod.template == "dev" and pod.session_id == "s_dev"


def test_release_destroys_everything_then_refills(make_pool, settings):
    pool = make_pool()

    async def go():
        await started(pool)
        pod = await pool.claim("chat", "s_1", CAP, PROFILE)
        assert await pool.release(pod.id)
        assert not await pool.release(pod.id)                      # gone: never reused
        await until(lambda: pool.count("warm", template="chat") == 2)
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
        await started(pool)
        pod = await pool.claim("chat", "s_1", CAP, {"limits": {"wall_seconds": 0}})
        await until(lambda: pod.id not in pool.pods)
        await pool.stop()
    run(go())


def test_a_pod_that_never_gets_ready_is_destroyed_and_replaced(make_pool):
    pool = make_pool()
    pool.backend.never_ready = 1

    async def go():
        await started(pool)
        removed = list(pool.backend.removed_containers)
        await pool.stop()
        return removed
    removed = run(go())
    assert any(n.startswith("sandbox-") for n in removed) and any(n.startswith("proxy-") for n in removed)


def test_a_warm_pod_whose_proxy_dies_is_replaced(make_pool):
    pool = make_pool()

    async def go():
        await started(pool)
        victim = next(iter(pool.pods.values()))
        pool.backend.dead_proxies.add(f"proxy-{victim.name}")
        await until(lambda: victim.id not in pool.pods and pool.count("warm", template="chat") == 2)
        await pool.stop()
    run(go())


def test_subnets_are_reused_only_after_release(make_pool):
    pool = make_pool(templates={"chat": Template(image="img", warm=1, max=1)})

    async def go():
        await pool.start()
        await until(lambda: pool.count("warm") == 1)
        first = await pool.claim("chat", "s_1", CAP, PROFILE)
        slot = first.slot
        await pool.release(first.id)
        await until(lambda: pool.count("warm") == 1)
        again = next(iter(pool.pods.values()))
        await pool.stop()
        return slot, again
    slot, again = run(go())
    assert again.slot == slot
