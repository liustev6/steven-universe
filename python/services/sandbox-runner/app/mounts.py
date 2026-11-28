"""The mounters' folders on the VM (sandbox-runner.md R10; files-and-mounts.md F2, F9).

Each pod's mounter (rclone, `/dev/fuse` and `SYS_ADMIN`) mounts the gateway's /dav at
`<root>/<pod>/inputs`; its bind is `rshared`, so the mount appears on the VM, and the sandbox binds
that folder read-only at /workspace/inputs. The root is on the VM's /run (a tmpfs: a reboot leaves
nothing behind), and the runner binds it `rshared` too, so it sees these mounts and can unmount them
(it runs privileged, R3).

A mounter stopped gracefully unmounts by itself (rclone on SIGTERM); one killed leaves its mount
behind, attached to nothing, and `stat` on it fails ("Transport endpoint is not connected"). So the
runner unmounts at teardown anyway (lazily: MNT_DETACH), and at start sweeps whatever is left (R9).
Mounts are read from /proc/self/mountinfo, never by touching the folders.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)
MNT_DETACH = 2


def _unescape(field: str) -> str:
    """mountinfo escapes space, tab, newline and backslash as \\ooo (octal)."""
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and i + 3 < len(field) and field[i + 1:i + 4].isdigit():
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def mounter_command(gateway_url: str) -> list[str]:
    """rclone, as every pod's mounter runs it: the gateway's /dav (through the pod's proxy), read-only,
    its Bearer token read from the capability the claim writes (empty before: rclone reads it once at
    start, where a failing command is fatal, so the warm-up writes an empty one; reads fail until then).
    `--low-level-retries 3` (rclone's default is 10): before the claim, rclone's own Statfs gets 401s, and
    each retry raises its pacer's sleep, to 2 s at 10 retries; that sleep then decays only a quarter
    per request, so a session's first `ls -R` took 5.5 s. With 3 it peaks at 160 ms (first `ls -R`
    377 ms, on VM 200): one retry picks up the token after the claim, one is spare; 1 fails.
    `--disable About`: rclone never asks the server for the disk size (Statfs answers with a placeholder),
    so a warm pod sends nothing before the claim: no 401s, no backoff (first `ls -R` 128 ms). Only the
    one expected 401 after the claim remains: rclone keeps its token in memory and re-reads the
    file only when the server rejects it. Nobody lists it before the claim (agent-runtime waits for its session file), so
    no cache needs refreshing then. `--dir-cache-time 2s` (was 10 s): a folder's listing is kept 2 s, so
    a file saved with `sfsync` shows within 2 s (files-and-mounts.md F11; nothing invalidates it).
    `--allow-other`: the sandbox's user reads what root mounted."""
    return ["mount", ":webdav:", "/mnt/inputs", "--webdav-url", f"{gateway_url.rstrip('/')}/dav/",
            "--webdav-bearer-token-command", "cat /run/session/capability",
            "--read-only", "--allow-other", "--dir-cache-time", "2s", "--vfs-cache-mode", "off",
            "--low-level-retries", "3", "--disable", "About", "--log-level", "NOTICE"]


class Mounts:
    def __init__(self, root: Path, mountinfo: Path = Path("/proc/self/mountinfo")):
        self.root, self.mountinfo = Path(root), mountinfo

    def folder(self, sandbox_id: uuid.UUID) -> Path:
        """What the mounter binds (rshared) at /mnt; its mount is `<folder>/inputs`."""
        return self.root / str(sandbox_id)

    def inputs(self, sandbox_id: uuid.UUID) -> Path:
        return self.folder(sandbox_id) / "inputs"

    def create(self, sandbox_id: uuid.UUID) -> Path:
        path = self.inputs(sandbox_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _mounted_under(self, prefix: Path) -> list[Path]:
        try:
            lines = self.mountinfo.read_text().splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            fields = line.split(" ")
            if len(fields) > 4:
                target = Path(_unescape(fields[4]))
                if target == prefix or prefix in target.parents:
                    out.append(target)
        return sorted(out, key=lambda p: len(p.parts), reverse=True)        # deepest first

    def mounted(self, sandbox_id: uuid.UUID) -> bool:
        return self.inputs(sandbox_id) in self._mounted_under(self.folder(sandbox_id))

    def _unmount(self, target: Path) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.umount2(os.fsencode(target), MNT_DETACH) != 0:
            err = ctypes.get_errno()
            raise OSError(err, f"umount2 {target}: {os.strerror(err)}")

    def remove(self, sandbox_id: uuid.UUID) -> None:
        """Unmount whatever is still mounted for the pod (its mounter already stopped), then its folder."""
        for target in self._mounted_under(self.folder(sandbox_id)):
            logger.warning("pod %s: %s still mounted after its mounter stopped: unmounted", sandbox_id, target)
            self._unmount(target)
        shutil.rmtree(self.folder(sandbox_id), ignore_errors=True)

    def sweep(self) -> int:
        """At start (R9): every mount under the root unmounted, every folder removed. Returns the mounts."""
        targets = self._mounted_under(self.root)
        for target in targets:
            try:
                self._unmount(target)
            except OSError as exc:
                logger.error("cannot unmount %s: %s", target, exc)
        if self.root.exists():
            for child in self.root.iterdir():
                shutil.rmtree(child, ignore_errors=True)
        return len(targets)
