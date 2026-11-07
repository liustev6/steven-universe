"""Writing and checking secret files (mode 600, atomic replace)."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class InsecureFile(Exception):
    """A secret file is readable by group or others."""


def check_private(path: Path) -> None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise InsecureFile(f"{path} is readable by others (mode {oct(mode & 0o777)}); chmod 600 it")


def write_private(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    """Write `data` to `path` with mode 600.

    `exclusive=True` refuses to overwrite. Otherwise the file is replaced
    atomically, so readers never see a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
