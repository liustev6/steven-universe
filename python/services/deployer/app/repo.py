"""A read-only clone of the public repository (deployer spec D4).

Everything about a deploy comes from the repository at the requested commit: the manifest
(infra/services/<service>/deploy.yaml), its schema, and which image that commit runs (D1a). The
clone is bare and partial (no file contents until read), and is fetched before every deploy.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


class RepoError(Exception):
    pass


class Repo:
    def __init__(self, url: str, path: Path, branch: str = "main"):
        self.url = url
        self.path = Path(path)
        self.branch = branch
        self._fetch_lock = asyncio.Lock()

    async def fetch(self) -> None:
        async with self._fetch_lock:
            if not (self.path / "HEAD").exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                await _git(None, "clone", "--quiet", "--bare", "--filter=blob:none", self.url, str(self.path))
            # A bare clone has no fetch refspec: mirror every branch (needed to tell pushed from unknown).
            await _git(self.path, "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*")

    async def resolve(self, ref: str) -> str:
        """A branch name or (short) sha -> the full commit sha."""
        out = await _git(self.path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        if not out:
            raise RepoError(f"unknown commit or branch: {ref}")
        return out

    async def exists(self, sha: str) -> bool:
        return await _ok(self.path, "cat-file", "-e", f"{sha}^{{commit}}")

    async def on_main(self, sha: str) -> bool:
        return await _ok(self.path, "merge-base", "--is-ancestor", sha, f"refs/heads/{self.branch}")

    async def on_any_branch(self, sha: str) -> bool:
        out = await _git(self.path, "branch", "--contains", sha, check=False)
        return bool(out)

    async def show(self, sha: str, path: str) -> str | None:
        """A file's text at that commit, or None if it doesn't exist there."""
        proc = await _run(self.path, "show", f"{sha}:{path}")
        return proc[1] if proc[0] == 0 else None

    async def image_sha(self, sha: str, *paths: str) -> str:
        """The commit that last changed these paths, at or before `sha` (the image name, D1a)."""
        out = await _git(self.path, "log", "-1", "--format=%H", sha, "--", *paths)
        if not out:
            raise RepoError(f"no commit at or before {sha[:12]} touches {', '.join(paths)}")
        return out

    async def subject(self, sha: str) -> str:
        return await _git(self.path, "log", "-1", "--format=%s", sha, check=False)


async def _run(cwd: Path | None, *args: str) -> tuple[int, str, str]:
    cmd = ["git", *(["-C", str(cwd)] if cwd else []), *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode().rstrip("\n"), err.decode().strip()


async def _git(cwd: Path | None, *args: str, check: bool = True) -> str:
    code, out, err = await _run(cwd, *args)
    if code and check:
        raise RepoError(f"git {args[0]} failed: {err or out}")
    return out.strip() if code == 0 else ""


async def _ok(cwd: Path, *args: str) -> bool:
    return (await _run(cwd, *args))[0] == 0
