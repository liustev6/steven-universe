"""The host allowlist (deployer spec D6): /etc/deployer/hosts.yaml.

A manifest names a host alias; only aliases listed here can be deployed to, each with a pinned SSH
host key. Its source is infra/services/deployer/hosts.yaml, but the deployer reads only the copy
installed by a hand deploy of the deployer itself (D13): a merged commit can't widen its reach.
Read on every deploy, so a reinstall needs no restart.

    hosts:
      files-host:
        address: 192.168.50.66
        user: root
        host_key: "ssh-ed25519 AAAA..."
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ALIAS = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_ADDRESS = re.compile(r"^[A-Za-z0-9.:-]+$")
_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_HOST_KEY = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]+$")


class HostsError(Exception):
    pass


@dataclass(frozen=True)
class Host:
    name: str
    address: str
    user: str
    host_key: str


def load_hosts(path: Path) -> dict[str, Host]:
    try:
        data = yaml.safe_load(Path(path).read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise HostsError(f"cannot read {path}: {exc}") from None
    hosts: dict[str, Host] = {}
    for name, h in (data.get("hosts") or {}).items():
        if not isinstance(h, dict):
            raise HostsError(f"{path}: {name}: expected a mapping")
        host = Host(name=str(name), address=str(h.get("address", "")), user=str(h.get("user", "root")),
                    host_key=str(h.get("host_key", "")).strip())
        for field, pattern, value in [("alias", _ALIAS, host.name), ("address", _ADDRESS, host.address),
                                      ("user", _USER, host.user), ("host_key", _HOST_KEY, host.host_key)]:
            if not pattern.match(value):
                raise HostsError(f"{path}: {name}: invalid {field}")
        hosts[host.name] = host
    return hosts
