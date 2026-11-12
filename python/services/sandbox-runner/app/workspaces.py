"""Workspaces under quota (sandbox-runner.md R13): one directory per pod on the XFS disk.

    /srv/workspaces/<sandbox id>/        an XFS project with a hard block limit
        work/   outputs/                 owned by the sandbox's uid; bind-mounted at /workspace

The VM's kernel enforces the limit, so it holds under gVisor (measured: writes past it fail with
"No space left on device"). Deleted with the pod.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Protocol

PROJECT_BASE = 1_000_000          # XFS project ids: base + the pod's slot (unique among live pods)


class Workspaces(Protocol):
    def create(self, sandbox_id: uuid.UUID, slot: int) -> Path: ...
    def remove(self, sandbox_id: uuid.UUID, slot: int) -> None: ...
    def leftovers(self) -> list[uuid.UUID]: ...
    def remove_leftover(self, sandbox_id: uuid.UUID) -> None: ...


class XfsWorkspaces:
    def __init__(self, root: Path, quota_mb: int, uid: int):
        self.root, self.quota_mb, self.uid = root, quota_mb, uid

    def _xfs_quota(self, *commands: str) -> None:
        args = ["xfs_quota", "-x"]
        for c in commands:
            args += ["-c", c]
        subprocess.run([*args, str(self.root)], check=True, capture_output=True, text=True)

    def create(self, sandbox_id: uuid.UUID, slot: int) -> Path:
        path = self.root / str(sandbox_id)
        for sub in ("work", "outputs"):
            (path / sub).mkdir(parents=True)
        for p in (path, path / "work", path / "outputs"):
            shutil.chown(p, self.uid, self.uid)
        project = PROJECT_BASE + slot
        self._xfs_quota(f"project -s -p {path} {project}", f"limit -p bhard={self.quota_mb}m {project}")
        return path

    def remove(self, sandbox_id: uuid.UUID, slot: int) -> None:
        shutil.rmtree(self.root / str(sandbox_id), ignore_errors=True)
        self._xfs_quota(f"limit -p bhard=0 {PROJECT_BASE + slot}")

    def leftovers(self) -> list[uuid.UUID]:
        found = []
        for p in self.root.iterdir() if self.root.is_dir() else []:
            try:
                found.append(uuid.UUID(p.name))
            except ValueError:
                continue                              # not ours: never touch it
        return found

    def remove_leftover(self, sandbox_id: uuid.UUID) -> None:
        """At start (R9): the slot is unknown, so only the directory; its quota is reset on reuse."""
        shutil.rmtree(self.root / str(sandbox_id), ignore_errors=True)
