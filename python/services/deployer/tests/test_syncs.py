"""Syncs (D21): a folder of the repository at a commit, mirrored into file-management. A real git
"GitHub", a fake GitHub signer, and file-management in memory (ETag = MD5, as S3 for one part)."""

import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.github_oidc import GitHubVerifier
from app.main import create_app
from app.repo import Repo
from app.syncs import Remote, Syncer

from .conftest import git

SYNC = "sync: specs\nsource: docs/specs\ndestination: public/steven-universe/specs/\n"


class FakeFiles:
    def __init__(self):
        self.objects: dict[str, bytes] = {}        # "bucket/key" -> bytes
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.fail_put = False

    async def list(self, bucket, prefix):
        return [Remote(k.split("/", 1)[1], f'"{hashlib.md5(v).hexdigest()}"')
                for k, v in sorted(self.objects.items()) if k.startswith(f"{bucket}/{prefix}")]

    async def put(self, bucket, key, body):
        if self.fail_put:
            raise httpx.HTTPStatusError("403 forbidden", request=httpx.Request("PUT", "https://files"),
                                        response=httpx.Response(403))
        self.objects[f"{bucket}/{key}"] = body
        self.puts.append(key)

    async def delete(self, bucket, key):
        self.objects.pop(f"{bucket}/{key}", None)
        self.deletes.append(key)


@pytest.fixture
def files():
    return FakeFiles()


@pytest.fixture
def specs(origin):
    return origin.commit({"infra/syncs/specs.yaml": SYNC,
                          "docs/specs/a.md": "# A\n", "docs/specs/agent-service/b.md": "# B\n",
                          "docs/specs/c with space.md": "# C\n", "docs/other.md": "not synced\n"}, "specs")


@pytest.fixture
def client(settings, deployer, github, files, tmp_path):
    syncer = Syncer(repo=deployer.repo, files=files, audit_log=tmp_path / "state" / "audit.log")
    verifier = GitHubVerifier(settings, http=httpx.AsyncClient(transport=github.transport()))
    with TestClient(create_app(settings, deployer=deployer, syncer=syncer, verifier=verifier)) as c:
        yield c


def sync(client, github, sha, name="specs", **body):
    return client.post("/v1/syncs", headers={"Authorization": f"Bearer {github.token(sha)}"},
                       content=json.dumps({"sync": name, "sha": sha, **body}))


def test_the_folder_is_mirrored_byte_for_byte(client, github, specs, files):
    r = sync(client, github, specs)
    assert r.status_code == 200, r.text
    assert r.json() == {**r.json(), "status": "succeeded", "files": 3, "unchanged": 0, "deleted": [],
                        "destination": "public/steven-universe/specs/"}
    assert sorted(r.json()["uploaded"]) == ["a.md", "agent-service/b.md", "c with space.md"]
    assert files.objects == {"public/steven-universe/specs/a.md": b"# A\n",
                             "public/steven-universe/specs/agent-service/b.md": b"# B\n",
                             "public/steven-universe/specs/c with space.md": b"# C\n"}


def test_unchanged_files_are_not_written_again(client, github, specs, files):
    sync(client, github, specs)
    files.puts.clear()
    r = sync(client, github, specs)
    assert r.json()["unchanged"] == 3 and r.json()["uploaded"] == [] and files.puts == []


def test_a_change_and_a_removal(client, github, origin, specs, files):
    sync(client, github, specs)
    (origin.path / "docs/specs/c with space.md").unlink()
    sha = origin.commit({"docs/specs/a.md": "# A, edited\n"}, "edit a, remove c")
    r = sync(client, github, sha).json()
    assert r["uploaded"] == ["a.md"] and r["deleted"] == ["c with space.md"] and r["unchanged"] == 1
    assert files.objects["public/steven-universe/specs/a.md"] == b"# A, edited\n"
    assert "public/steven-universe/specs/c with space.md" not in files.objects


def test_only_its_destination_is_touched(client, github, specs, files):
    files.objects["public/steven-universe/other/keep.md"] = b"another sync's"
    files.objects["public/elsewhere.md"] = b"not ours"
    sync(client, github, specs)
    assert files.objects["public/steven-universe/other/keep.md"] == b"another sync's"
    assert files.objects["public/elsewhere.md"] == b"not ours"


def test_every_sync_is_audited(client, github, specs, tmp_path):
    sync(client, github, specs)
    entry = json.loads((tmp_path / "state" / "audit.log").read_text().splitlines()[-1])
    assert entry == {**entry, "kind": "sync", "sync": "specs", "sha": specs, "status": "succeeded",
                     "uploaded": 3, "deleted": 0}
    assert entry["requested_by"]["actor"] == "liustev6"


def test_a_failed_upload_is_a_failed_sync(client, github, specs, files):
    files.fail_put = True
    r = sync(client, github, specs)
    assert r.status_code == 502 and r.json()["status"] == "failed"


# ---- refused before anything changes ----------------------------------------------------------

def test_github_token_required(client, specs):
    assert client.post("/v1/syncs", content=json.dumps({"sync": "specs", "sha": specs})).status_code == 401


def test_the_token_is_for_this_commit(client, github, origin, specs):
    other = origin.commit({"x.txt": "x"})
    r = client.post("/v1/syncs", headers={"Authorization": f"Bearer {github.token(other)}"},
                    content=json.dumps({"sync": "specs", "sha": specs}))
    assert r.status_code == 422


@pytest.mark.parametrize("body", [{"source": "docs"}, {"destination": "public/x/"}, {"extra": 1}])
def test_the_request_names_a_sync_and_nothing_else(client, github, specs, body):
    """What is copied and where is the manifest's, never the request's."""
    assert sync(client, github, specs, **body).status_code == 422


def test_unknown_sync_is_404(client, github, specs):
    assert sync(client, github, specs, name="nope").status_code == 404


@pytest.mark.parametrize("manifest", [
    "sync: specs\nsource: ../etc\ndestination: public/steven-universe/specs/\n",
    "sync: specs\nsource: docs/specs\ndestination: public/steven-universe/specs\n",       # no final /
    "sync: specs\nsource: docs/specs\ndestination: public/../private/x/\n",
    "sync: other\nsource: docs/specs\ndestination: public/steven-universe/specs/\n",      # wrong name
])
def test_a_bad_manifest_is_refused(client, github, origin, files, manifest):
    sha = origin.commit({"infra/syncs/specs.yaml": manifest, "docs/specs/a.md": "# A\n"})
    assert sync(client, github, sha).status_code == 422 and files.puts == []


def test_an_empty_source_never_empties_the_destination(client, github, origin, specs, files):
    sync(client, github, specs)
    sha = origin.commit({"infra/syncs/specs.yaml": SYNC.replace("docs/specs", "docs/none")})
    r = sync(client, github, sha)
    assert r.status_code == 422 and files.deletes == [] and len(files.objects) == 3


def test_a_commit_not_on_main_is_refused(client, github, origin, specs):
    git(origin.path, "checkout", "-q", "-b", "feature")
    sha = origin.commit({"docs/specs/a.md": "# A from a branch\n"})
    git(origin.path, "checkout", "-q", "main")
    assert sync(client, github, sha).status_code == 422


def test_files_lists_regular_files_only(tmp_path, origin, specs):
    import asyncio

    repo = Repo(str(origin.path), tmp_path / "r.git")

    async def go():
        await repo.fetch()
        return await repo.files(specs, "docs/specs"), await repo.blob(specs, "docs/specs/a.md")
    paths, body = asyncio.run(go())
    assert sorted(paths) == ["docs/specs/a.md", "docs/specs/agent-service/b.md", "docs/specs/c with space.md"]
    assert body == b"# A\n"


def test_the_file_management_client(tmp_path):
    """List pages until the last cursor; keys URL-encoded; the deployer's own token on every call."""
    import asyncio

    from app.syncs import FileManagement
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode(), request.headers["authorization"]))
        if request.method == "GET":
            if "cursor" not in request.url.params:
                return httpx.Response(200, json={"objects": [{"key": "s/a.md", "etag": '"1"'}], "next_cursor": "c1"})
            return httpx.Response(200, json={"objects": [{"key": "s/b.md", "etag": '"2"'}], "next_cursor": None})
        return httpx.Response(204 if request.method == "DELETE" else 200, json={})

    async def token():
        return "T"

    fm = FileManagement("https://files.test/", token, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def go():
        listed = await fm.list("public", "s/")
        await fm.put("public", "s/c with space.md", b"x")
        await fm.delete("public", "s/a.md")
        return listed
    assert asyncio.run(go()) == [Remote("s/a.md", '"1"'), Remote("s/b.md", '"2"')]
    assert ("PUT", "/objects/public/s/c%20with%20space.md", "Bearer T") in seen
    assert ("DELETE", "/objects/public/s/a.md", "Bearer T") in seen
    assert all(auth == "Bearer T" for _, _, auth in seen)
