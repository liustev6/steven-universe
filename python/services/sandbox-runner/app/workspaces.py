"""Workspaces under quota (sandbox-runner.md R13): one directory per pod on the XFS disk.

    /srv/workspaces/<sandbox id>/        an XFS project with a hard block limit; root's, bound at /workspace
        work/                            the only folder the sandbox's uid may write (Codex starts here)
        inputs/                          root's, empty: where the mounter's folder is bound read-only

The agent writes only in `work/` (and its in-memory /tmp); nothing else under /workspace
(files-and-mounts.md F1; `outputs/` dropped 2026-11-28).

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
    def create(self, sandbox_id: uuid.UUID, slot: int, quota_mb: int) -> Path: ...
    def remove(self, sandbox_id: uuid.UUID, slot: int) -> None: ...
    def leftovers(self) -> list[uuid.UUID]: ...
    def remove_leftover(self, sandbox_id: uuid.UUID) -> None: ...


class XfsWorkspaces:
    def __init__(self, root: Path, uid: int):
        self.root, self.uid = root, uid

    def _xfs_quota(self, *commands: str) -> None:
        args = ["xfs_quota", "-x"]
        for c in commands:
            args += ["-c", c]
        subprocess.run([*args, str(self.root)], check=True, capture_output=True, text=True)

    def create(self, sandbox_id: uuid.UUID, slot: int, quota_mb: int) -> Path:
        """The pod's directory, with a hard limit of `quota_mb` (its template's workspace_mb)."""
        path = self.root / str(sandbox_id)
        (path / "work").mkdir(parents=True)
        (path / "inputs").mkdir()
        path.chmod(0o755)                                   # readable by the sandbox, writable only by root
        shutil.chown(path / "work", self.uid, self.uid)
        project = PROJECT_BASE + slot
        self._xfs_quota(f"project -s -p {path} {project}", f"limit -p bhard={quota_mb}m {project}")
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
