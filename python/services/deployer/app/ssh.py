"""Running a host's deploy-target over SSH (deployer spec D6).

The deployer's key is restricted on every host to `/usr/local/sbin/deploy-target` (the wrapper), so
the only thing sent is the request `deploy <service> <sha> [<digest>]`; the secrets payload goes on
stdin. The host key must be the one pinned in hosts.yaml: no other host key is trusted, ever.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from app.hosts import Host

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TIMED_OUT = 124

OnLine = Callable[[str], Awaitable[None]]


def ssh_command(host: Host, key_file: Path, known_hosts: Path, service: str, sha: str, digest: str | None) -> list[str]:
    request = " ".join(["deploy", service, sha] + ([digest] if digest else []))
    return [
        "ssh", "-T",
        "-i", str(key_file),
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=4",
        "-o", "LogLevel=ERROR",
        f"{host.user}@{host.address}",
        request,
    ]


class SSHRunner:
    def __init__(self, key_file: Path, timeout: float):
        self.key_file = Path(key_file)
        self.timeout = timeout

    async def run(self, host: Host, service: str, sha: str, digest: str | None, payload: bytes, on_line: OnLine) -> int:
        """Run the deploy; every output line goes to on_line. Returns the exit code (124: timeout)."""
        fd, known = tempfile.mkstemp(prefix="known_hosts.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(f"{host.address} {host.host_key}\n")
            cmd = ssh_command(host, self.key_file, Path(known), service, sha, digest)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tempfile.gettempdir(), "LC_ALL": "C"}
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, env=env,
            )
            try:
                async with asyncio.timeout(self.timeout):
                    assert proc.stdin and proc.stdout
                    proc.stdin.write(payload)
                    await proc.stdin.drain()
                    proc.stdin.close()
                    async for raw in proc.stdout:
                        await on_line(_ANSI.sub("", raw.decode(errors="replace")).rstrip("\r\n"))
                    return await proc.wait()
            except TimeoutError:
                proc.kill()
                await proc.wait()
                await on_line(f"deploy timed out after {self.timeout:.0f} s; the SSH session was closed")
                return TIMED_OUT
        finally:
            os.unlink(known)
