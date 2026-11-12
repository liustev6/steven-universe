"""Docker, as the runner uses it: pod networks, hardened containers, logs, and finding leftovers.

`Backend` is what the pool depends on; `DockerBackend` is the real one (the Docker Engine API through
the official SDK, over the VM's socket: R3). Tests use a fake. The runner can only create, inspect
and destroy: there is no exec, no file access, no network change after creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

LABEL = "dev.steven-universe.sandbox-runner"        # on everything the runner creates (R9)


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    networks: list[tuple[str, str | None]]           # (network, fixed IPv4 or None); the first is joined at creation
    labels: dict[str, str]
    runtime: str | None = None                       # "runsc" for sandboxes; Docker's default for proxies
    user: str | None = None
    binds: list[tuple[str, str, bool]] = field(default_factory=list)   # (host path, container path, read-only)
    tmpfs: dict[str, str] = field(default_factory=dict)                 # container path -> mount options
    memory_mb: int | None = None
    cpus: float | None = None
    pids: int | None = None
    env: dict[str, str] = field(default_factory=dict)


class Backend(Protocol):
    def ensure_image(self, ref: str) -> None: ...
    def ensure_egress_network(self, name: str) -> None: ...
    def create_pod_network(self, name: str, subnet: str) -> None: ...
    def run(self, spec: ContainerSpec) -> None: ...
    def logs(self, name: str) -> str: ...
    def running(self, name: str) -> bool: ...
    def remove_container(self, name: str) -> None: ...
    def remove_network(self, name: str) -> None: ...
    def leftovers(self) -> tuple[list[str], list[str]]: ...     # (containers, networks) carrying LABEL


class DockerBackend:
    """The real backend. Every container is hardened the same way: read-only root, no capabilities,
    no-new-privileges, limits, and capped logs."""

    def __init__(self, client=None):
        import docker

        self._docker = docker
        self._api = (client or docker.from_env()).api

    def ensure_image(self, ref: str) -> None:
        try:
            self._api.inspect_image(ref)
        except self._docker.errors.ImageNotFound:
            self._api.pull(ref)

    def ensure_egress_network(self, name: str) -> None:
        if not self._api.networks(names=[name]):
            self._api.create_network(name, driver="bridge", labels={LABEL: "egress"})

    def create_pod_network(self, name: str, subnet: str) -> None:
        types = self._docker.types
        self._api.create_network(
            name, driver="bridge", internal=True, labels={LABEL: "pod"},
            ipam=types.IPAMConfig(pool_configs=[types.IPAMPool(subnet=subnet)]),
            # No address on the VM for this bridge: otherwise a sandbox reaches the VM's own ports (R12).
            options={"com.docker.network.bridge.inhibit_ipv4": "true"},
        )

    def run(self, spec: ContainerSpec) -> None:
        api, types = self._api, self._docker.types
        host_config = api.create_host_config(
            runtime=spec.runtime,
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            binds={host: {"bind": path, "mode": "ro" if ro else "rw"} for host, path, ro in spec.binds},
            tmpfs=spec.tmpfs,
            mem_limit=f"{spec.memory_mb}m" if spec.memory_mb else None,
            nano_cpus=int(spec.cpus * 1e9) if spec.cpus else None,
            pids_limit=spec.pids,
            log_config=types.LogConfig(type="json-file", config={"max-size": "1m", "max-file": "2"}),
        )
        (first_net, first_ip), *more = spec.networks
        container = api.create_container(
            spec.image, name=spec.name, user=spec.user, labels=spec.labels, environment=spec.env or None,
            host_config=host_config,
            networking_config=api.create_networking_config(
                {first_net: api.create_endpoint_config(ipv4_address=first_ip)}),
        )
        for net, ip in more:                         # joined before start: never running half-connected
            api.connect_container_to_network(container, net, ipv4_address=ip)
        api.start(container)

    def logs(self, name: str) -> str:
        try:
            return self._api.logs(name, stdout=True, stderr=True, tail=200).decode(errors="replace")
        except self._docker.errors.NotFound:
            return ""

    def running(self, name: str) -> bool:
        try:
            return bool(self._api.inspect_container(name)["State"]["Running"])
        except self._docker.errors.NotFound:
            return False

    def remove_container(self, name: str) -> None:
        try:
            self._api.remove_container(name, force=True)
        except self._docker.errors.NotFound:
            pass

    def remove_network(self, name: str) -> None:
        try:
            self._api.remove_network(name)
        except self._docker.errors.NotFound:
            pass

    def leftovers(self) -> tuple[list[str], list[str]]:
        containers = [c["Names"][0].lstrip("/") for c in self._api.containers(all=True, filters={"label": LABEL})]
        networks = [n["Name"] for n in self._api.networks(filters={"label": f"{LABEL}=pod"})]
        return containers, networks
