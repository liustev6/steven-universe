"""`sfsync` (files-and-mounts.md F7, F12): what it sends, and the one line and exit code the agent gets
back for each answer; and the workspace note (F13). The gateway is faked with its real answers'
shapes (RFC 9457 problem details)."""

import json
from pathlib import Path

import httpx
import pytest

from core.agent import instructions
from core.sfsync import EX_NOINPUT, EX_NOPERM, EX_OK, EX_TEMPFAIL, EX_UNAVAILABLE, EX_USAGE, run
from core.types import Profile
from core.workspace import size_text, workspace_note

MINE = "private/session-files/s-1/"


def problem(status, detail, **extra):
    return httpx.Response(status, json={"type": "about:blank", "title": "x", "status": status, "detail": detail, **extra},
                          headers={"content-type": "application/problem+json"})


@pytest.fixture
def session_file(tmp_path):
    f = tmp_path / "session.json"
    f.write_text(json.dumps({"session_id": "s-1", "capability": "cap-xyz", "gateway_url": "http://10.0.0.2:8080/",
                             "profile": {"name": "saver"}}))
    return str(f)


def client(answer):
    seen = []

    def handler(request):
        seen.append(request)
        return answer(request) if callable(answer) else answer
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_put_sends_the_bytes_with_the_capability(tmp_path, session_file):
    local = tmp_path / "report one.md"
    local.write_bytes(b"# report")
    http, seen = client(httpx.Response(201, json={"location": f"{MINE}reports/report one.md", "size": 8}))
    code, line = run(["put", str(local), f"{MINE}reports/"], session_file=session_file, http=http)
    assert code == EX_OK and line == f"put {local} -> {MINE}reports/report one.md (8 bytes)"
    r = seen[0]
    assert r.method == "PUT" and str(r.url) == f"http://10.0.0.2:8080/files/{MINE}reports/report%20one.md"
    assert r.headers["authorization"] == "Bearer cap-xyz" and r.headers["content-type"] == "text/markdown"
    assert r.read() == b"# report" and r.headers["x-sfsync-source"].endswith("report%20one.md")


def test_rm(session_file):
    http, seen = client(httpx.Response(204))
    assert run(["rm", f"{MINE}chart.png"], session_file=session_file, http=http) == (EX_OK, f"rm {MINE}chart.png: removed")
    assert seen[0].method == "DELETE"


@pytest.mark.parametrize("answer, code, words", [
    (problem(403, f"private/other/x.md is outside the folders you may write to ({MINE})", allowed=[MINE]),
     EX_NOPERM, "outside the folders you may write to"),
    (problem(413, "40.2 MB is over this session's limit of 25 MB per file"), EX_NOPERM, "limit of 25 MB"),
    (problem(409, "no turn is running; nothing was changed"), EX_TEMPFAIL, "no turn is running"),
    (problem(503, "storage did not answer; nothing was changed, try again"), EX_TEMPFAIL, "try again"),
    (problem(400, "'x' is not a file: give <bucket>/<folders…>/<file name>"), EX_USAGE, "is not a file"),
    (httpx.Response(502, text="Bad Gateway"), EX_TEMPFAIL, "Bad Gateway"),       # the proxy's own, not a problem
])
def test_a_refusal_is_one_line_the_agent_can_act_on(tmp_path, session_file, answer, code, words):
    local = tmp_path / "x.md"
    local.write_text("x")
    http, _ = client(answer)
    got, line = run(["put", str(local), "private/other/x.md"], session_file=session_file, http=http)
    assert got == code and line.startswith("sfsync: put refused: ") and words in line and "\n" not in line


def test_rm_of_nothing_is_no_input(session_file):
    http, _ = client(problem(404, f"{MINE}old.md isn't there; nothing was removed"))
    code, line = run(["rm", f"{MINE}old.md"], session_file=session_file, http=http)
    assert code == EX_NOINPUT and "isn't there" in line


def test_local_mistakes_never_reach_the_gateway(tmp_path, session_file):
    http, seen = client(httpx.Response(201))
    code, line = run(["put", str(tmp_path / "missing.md"), MINE], session_file=session_file, http=http)
    assert code == EX_NOINPUT and line == f"sfsync: no such local file: {tmp_path / 'missing.md'}"
    code, line = run(["put", str(tmp_path), MINE], session_file=session_file, http=http)
    assert code == EX_NOINPUT and "it is a folder" in line
    for argv in ([], ["put", "a"], ["ls", MINE], ["get", f"{MINE}a", "/tmp/a"], ["rm"]):
        code, line = run(argv, session_file=session_file, http=http)
        assert code == EX_USAGE and line.startswith("sfsync: usage: sfsync put LOCAL_FILE DESTINATION"), argv
    assert seen == []
    assert run(["--help"])[0] == EX_OK and "sfsync rm DESTINATION" in run(["--help"])[1]


def test_outside_a_session_or_unreachable(tmp_path, session_file):
    code, line = run(["rm", f"{MINE}a"], session_file=str(tmp_path / "none.json"))
    assert code == EX_UNAVAILABLE and "runs inside an agent session" in line

    def down(request):
        raise httpx.ConnectError("refused")
    http, _ = client(down)
    code, line = run(["rm", f"{MINE}a"], session_file=session_file, http=http)
    assert code == EX_TEMPFAIL and "nothing was changed, try again" in line


# ---- the workspace note (F13) ---------------------------------------------------------------------------
def test_the_note_names_each_folder_and_its_size(tmp_path):
    work, inputs = tmp_path / "work", tmp_path / "inputs"
    work.mkdir()
    inputs.mkdir()
    note = workspace_note(str(work), str(tmp_path), str(inputs))
    assert note.startswith("Your workspace (this sandbox's file system):")
    assert f"- {work}: your working folder, read-write, on disk (" in note
    assert f"If it runs out of space, use {work}." in note and f"- {inputs}: read-only" in note
    assert note.endswith("Everything else is read-only.")
    assert "inputs" not in workspace_note(str(work), str(tmp_path), str(tmp_path / "none"))
    assert size_text(1024 ** 3) == "1.0 GB" and size_text(512 * 1024 ** 2) == "512 MB" and size_text(20 * 1024 ** 3) == "20.0 GB"


def test_the_note_comes_first_only_with_a_shell():
    with_shell = instructions(Profile(name="s", tools=["shell"], instructions="Answer from the specs."))
    assert with_shell.startswith("Your workspace") and with_shell.endswith("Answer from the specs.")
    assert instructions(Profile(name="v", tools=["web_search"], instructions="Be brief.")) == "Be brief."
    assert instructions(Profile(name="v")) is None
