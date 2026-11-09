"""The deploy: refusals before anything runs, success, failures, rollback, audit."""

import asyncio
import json

import pytest

from app.deploys import DeployError
from app.openbao import SecretsError

from .conftest import MANIFEST, git


def run(deployer, service, sha, digest=None, **kw):
    """prepare + start + collect all events."""
    async def go():
        plan = await deployer.prepare(service, sha, digest, **kw)
        deployment = deployer.start(plan, {"actor": "test"})
        return [e async for e in deployment.follow() if e["event"] != "heartbeat"]
    return asyncio.run(go())


def refused(deployer, service, sha, **kw) -> DeployError:
    with pytest.raises(DeployError) as e:
        asyncio.run(deployer.prepare(service, sha, **kw))
    return e.value


def result(events):
    assert events[-1]["event"] == "result"
    return events[-1]


# ---- refused before anything runs --------------------------------------------------------------

def test_unknown_commit(deployer):
    assert refused(deployer, "demo", "f" * 40).status == 422


def test_not_on_main(deployer, origin):
    git(origin.path, "checkout", "-q", "-b", "feature")
    sha = origin.commit({"python/services/demo/app.py": "v2\n"})
    git(origin.path, "checkout", "-q", "main")
    assert "not on main" in refused(deployer, "demo", sha).message
    # the CLI (D18) may deploy any pushed branch, e.g. for a rollback drill
    assert asyncio.run(deployer.prepare("demo", sha, require_main=False)).sha == sha


def test_unknown_service(deployer, origin):
    assert refused(deployer, "nope", git(origin.path, "rev-parse", "HEAD")).status == 404


def test_pipeline_false_is_refused(deployer, origin):
    sha = origin.commit({"infra/services/demo/deploy.yaml": MANIFEST.format(pipeline="false")})
    e = refused(deployer, "demo", sha)
    assert e.status == 403 and "by hand only" in e.message
    asyncio.run(deployer.prepare("demo", sha, require_pipeline=False))   # the CLI may


def test_host_not_in_allowlist(deployer, origin):
    sha = origin.commit({"infra/services/demo/deploy.yaml": MANIFEST.format(pipeline="true").replace("demo-host", "auth-host")})
    e = refused(deployer, "demo", sha)
    assert e.status == 403 and "allowlist" in e.message


def test_manifest_must_match_schema(deployer, origin):
    sha = origin.commit({"infra/services/demo/deploy.yaml": MANIFEST.format(pipeline="true") + "evil: true\n"})
    e = refused(deployer, "demo", sha)
    assert e.status == 422 and "evil" in e.message


def test_one_deploy_per_service(deployer, origin, fake_host):
    sha = git(origin.path, "rev-parse", "HEAD")

    async def go():
        gate = asyncio.Event()
        original = fake_host.run

        async def slow(*a):
            await gate.wait()
            return await original(*a)
        fake_host.run = slow
        d = deployer.start(await deployer.prepare("demo", sha), {})
        with pytest.raises(DeployError) as e:
            await deployer.prepare("demo", sha)
        gate.set()
        async for _ in d.follow():
            pass
        await deployer.prepare("demo", sha)  # free again
        return e.value.status
    assert asyncio.run(go()) == 409


# ---- running -------------------------------------------------------------------------------------

def test_success(deployer, origin, fake_host, fake_secrets, tmp_path):
    sha = git(origin.path, "rev-parse", "HEAD")
    events = run(deployer, "demo", sha, "sha256:" + "b" * 64)
    assert events[0]["event"] == "accepted" and events[0]["host"] == "demo-host"
    assert [e["name"] for e in events if e["event"] == "step"][-1] == "Smoke test"
    assert result(events)["status"] == "succeeded"
    # the manifest's secrets went to the host on stdin; the digest was passed through
    assert fake_secrets.calls == [[{"from": "services/demo/api_key", "to": "/etc/demo/credentials/api_key", "owner": "10001:10001"}]]
    assert fake_host.calls[0][1:] == (sha, "sha256:" + "b" * 64, b"/etc/demo/credentials/api_key\towner\tdmFsdWU=\n")
    audit = [json.loads(line) for line in (tmp_path / "state/audit.log").read_text().splitlines()]
    assert audit[-1]["status"] == "succeeded" and audit[-1]["requested_by"] == {"actor": "test"}


def test_failure_after_start_rolls_back(deployer, origin, fake_host):
    good = git(origin.path, "rev-parse", "HEAD")
    run(deployer, "demo", good)
    bad = origin.commit({"python/services/demo/app.py": "broken\n"})
    fake_host.fail[bad] = 6                           # health check fails after the new container started
    events = run(deployer, "demo", bad)
    rb = [e for e in events if e["event"] == "rollback"]
    assert rb and rb[0]["to"] == good
    assert result(events)["status"] == "rolled_back" and result(events)["previous"] == good
    assert [c[1] for c in fake_host.calls] == [good, bad, good] and fake_host.running == good


def test_failure_before_changes_is_not_rolled_back(deployer, origin, fake_host):
    good = git(origin.path, "rev-parse", "HEAD")
    run(deployer, "demo", good)
    bad = origin.commit({"python/services/demo/app.py": "v2\n"})
    deployer.secrets.error = SecretsError("OpenBao is sealed")
    events = run(deployer, "demo", bad)
    assert result(events)["status"] == "failed" and "sealed" in result(events)["error"]
    assert not [e for e in events if e["event"] == "rollback"]
    assert [c[1] for c in fake_host.calls] == [good]  # the host was never contacted


def test_first_deploy_failing_has_nothing_to_roll_back_to(deployer, origin, fake_host):
    sha = git(origin.path, "rev-parse", "HEAD")
    fake_host.fail[sha] = 5
    events = run(deployer, "demo", sha)
    assert result(events)["status"] == "failed" and not [e for e in events if e["event"] == "rollback"]


def test_rollback_failing_too(deployer, origin, fake_host):
    good = git(origin.path, "rev-parse", "HEAD")
    run(deployer, "demo", good)
    bad = origin.commit({"python/services/demo/app.py": "broken\n"})
    fake_host.fail[bad] = 7
    fake_host.fail[good] = 7                          # e.g. a dependency is down: nothing passes
    assert result(run(deployer, "demo", bad))["status"] == "failed"


def test_rollback_ignores_pipeline_flag_of_the_previous_commit(deployer, origin, fake_host):
    # The first pipeline deploy follows a manual one whose manifest said pipeline: false.
    manual = origin.commit({"infra/services/demo/deploy.yaml": MANIFEST.format(pipeline="false")})
    run(deployer, "demo", manual, require_pipeline=False)
    bad = origin.commit({"infra/services/demo/deploy.yaml": MANIFEST.format(pipeline="true"),
                         "python/services/demo/app.py": "broken\n"})
    fake_host.fail[bad] = 7                           # smoke test fails
    assert result(run(deployer, "demo", bad))["status"] == "rolled_back"
    assert fake_host.running == manual


# ---- reconnecting (a dropped stream, e.g. while api-service itself is replaced) ---------------------

def test_events_are_numbered_and_resumable(deployer, origin):
    sha = git(origin.path, "rev-parse", "HEAD")

    async def go():
        d = deployer.start(await deployer.prepare("demo", sha), {})
        everything = [e async for e in d.follow()]
        resumed = [e async for e in d.follow(after=3)]           # after the deploy is over
        return d, everything, resumed
    d, everything, resumed = asyncio.run(go())
    assert [e["seq"] for e in everything] == list(range(len(everything)))
    assert everything[0] == {**everything[0], "event": "accepted", "id": d.id}
    assert resumed == everything[4:] and resumed[-1]["event"] == "result"


def test_a_deploy_nobody_watches_still_finishes(deployer, origin, fake_host):
    sha = git(origin.path, "rev-parse", "HEAD")

    async def go():
        gate = asyncio.Event()
        original = fake_host.run

        async def slow(*a):
            await gate.wait()
            return await original(*a)
        fake_host.run = slow
        d = deployer.start(await deployer.prepare("demo", sha), {})
        stream = d.follow()
        first = [await stream.__anext__()]                       # caller sees "accepted", then drops
        await stream.aclose()
        gate.set()
        rest = [e async for e in d.follow(after=first[-1]["seq"]) if e["event"] != "heartbeat"]
        return first, rest
    first, rest = asyncio.run(go())
    assert first[0]["event"] == "accepted"
    assert rest[0]["seq"] == 1 and rest[-1]["status"] == "succeeded"
    assert deployer.get(first[0]["id"]).done
