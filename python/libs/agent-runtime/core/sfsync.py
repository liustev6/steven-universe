"""`sfsync` (session file sync): save a file to storage, or remove one (files-and-mounts.md F7, F12).

    sfsync put LOCAL_FILE DESTINATION     create or replace <bucket>/<key> with a local file's bytes;
                                          a DESTINATION ending in '/' keeps the file's own name
    sfsync rm DESTINATION                 remove <bucket>/<key>

Reading needs no command: what the session may read is mounted at /workspace/inputs.

It holds no credential: it sends the session capability (already in /run/session) to the gateway, the
only host it can reach, which decides everything (the session's grant, its size limit, a turn
running). It checks nothing itself. Its output is for the agent loop: one line on stdout when it
worked, one line on stderr when it didn't, and an exit code from sysexits.h:

    0   done              66  no such file (NOINPUT)     75  try again (TEMPFAIL)
    64  usage (USAGE)     69  can't reach (UNAVAILABLE)  77  not allowed (NOPERM)
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

from core.types import SESSION_FILE

EX_OK, EX_USAGE, EX_NOINPUT, EX_UNAVAILABLE, EX_TEMPFAIL, EX_NOPERM = 0, 64, 66, 69, 75, 77
USAGE = "usage: sfsync put LOCAL_FILE DESTINATION | sfsync rm DESTINATION   (sfsync --help)"
SOURCE_HEADER = "X-Sfsync-Source"


class Failed(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def _session(path: str) -> tuple[str, str]:
    """(gateway URL, capability) from the session file."""
    try:
        s = json.loads(Path(path).read_text())
        return s["gateway_url"].rstrip("/"), s["capability"]
    except (OSError, ValueError, KeyError) as e:
        raise Failed(f"no session here ({path}: {e.__class__.__name__}); sfsync runs inside an agent session",
                     EX_UNAVAILABLE) from e


def _code(status: int) -> int:
    if status in (401, 403, 413):
        return EX_NOPERM
    if status == 404:
        return EX_NOINPUT
    if status == 400:
        return EX_USAGE
    return EX_TEMPFAIL                       # 409 (no turn), 5xx: nothing changed, try again


def _detail(r: httpx.Response) -> str:
    """The gateway's RFC 9457 `detail` (or whatever text it sent)."""
    try:
        body = r.json()
        if isinstance(body, dict) and body.get("detail"):
            return str(body["detail"])
    except ValueError:
        pass
    return (r.text.strip() or r.reason_phrase or "no answer")[:300]


def put(http: httpx.Client, base: str, local: str, destination: str) -> str:
    path = Path(local)
    if not path.is_file():
        raise Failed(f"no such local file: {local}" + (" (it is a folder: give a file)" if path.is_dir() else ""),
                     EX_NOINPUT)
    if destination.endswith("/"):
        destination += path.name
    size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as f:
        r = http.put(f"{base}/files/{quote(destination, safe='/')}", content=f,
                     headers={"Content-Type": content_type, "Content-Length": str(size),
                              SOURCE_HEADER: quote(str(path.absolute()), safe="/")})
    if r.status_code >= 300:
        raise Failed(f"put refused: {_detail(r)}", _code(r.status_code))
    return f"put {local} -> {destination} ({size_text(size)})"


def rm(http: httpx.Client, base: str, destination: str) -> str:
    r = http.delete(f"{base}/files/{quote(destination, safe='/')}")
    if r.status_code >= 300:
        raise Failed(f"rm: {_detail(r)}", _code(r.status_code))
    return f"rm {destination}: removed"


def size_text(n: int) -> str:
    if n < 1024:
        return f"{n} bytes"
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


def run(argv: list[str], *, session_file: str | None = None, http: httpx.Client | None = None) -> tuple[int, str]:
    """(exit code, the line to print): stdout for 0, stderr otherwise."""
    if argv[:1] in (["-h"], ["--help"], ["help"]):
        return EX_OK, (__doc__ or "").strip()
    if not ((len(argv) == 3 and argv[0] == "put") or (len(argv) == 2 and argv[0] == "rm")):
        return EX_USAGE, f"sfsync: {USAGE}"
    try:
        base, capability = _session(session_file or os.environ.get("AGENT_SESSION_FILE", SESSION_FILE))
        client = http or httpx.Client(timeout=httpx.Timeout(120.0, connect=5.0))
        client.headers["Authorization"] = f"Bearer {capability}"
        try:
            if argv[0] == "put":
                return EX_OK, put(client, base, argv[1], argv[2])
            return EX_OK, rm(client, base, argv[1])
        finally:
            if http is None:
                client.close()
    except Failed as e:
        return e.code, f"sfsync: {e}"
    except httpx.HTTPError as e:
        return EX_TEMPFAIL, f"sfsync: the gateway did not answer ({e.__class__.__name__}); nothing was changed, try again"


def main() -> None:
    code, line = run(sys.argv[1:])
    print(line, file=sys.stdout if code == EX_OK else sys.stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()
