"""The sandbox's file system, as the agent is told it (files-and-mounts.md F1, F13).

    /workspace/work     the one writable folder on disk (the pod's quota); Codex starts here
    /tmp                in memory, small (HOME and CODEX_HOME live here too)
    /workspace/inputs   read-only: the session's files from storage (the mounter's)

The note goes first in the instructions of every session with a shell. Its sizes are measured here,
where the template's limits are real (`chat` and `dev` differ), so nothing has to be kept in sync.
"""

from __future__ import annotations

import os
import shutil

WORK = "/workspace/work"
TMP = "/tmp"
INPUTS = "/workspace/inputs"


def size_text(n: int) -> str:
    """1.0 GB, 512 MB: binary units, as the templates set them (workspace_mb, tmp_mb)."""
    mb = n / (1024 * 1024)
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _size(path: str) -> str | None:
    try:
        return size_text(shutil.disk_usage(path).total)
    except OSError:
        return None


def workspace_note(work: str = WORK, tmp: str = TMP, inputs: str = INPUTS) -> str:
    """What each folder is for, with its size; a folder that isn't there is left out."""
    lines = ["Your workspace (this sandbox's file system):"]
    if os.path.isdir(work):
        size = _size(work)
        lines.append(f"- {work}: your working folder, read-write, on disk{f' ({size})' if size else ''}. Clone, "
                     "build, edit and keep intermediate files here. Everything in it is deleted when the session ends.")
    size = _size(tmp)
    lines.append(f"- {tmp}: small, in memory{f' ({size})' if size else ''}, for short-lived temporary files. "
                 f"If it runs out of space, use {work}.")
    if os.path.isdir(inputs):
        lines.append(f"- {inputs}: read-only, files from storage (when this session has any; listed below).")
    lines.append("Everything else is read-only.")
    return "\n".join(lines)
