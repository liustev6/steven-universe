"""Settings (prefix RUNNER_). The environment's values will live in infra/services/sandbox-runner/
config.env (2.6). Secrets are read from files, never from environment variables.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNNER_", extra="ignore")

    # ---- What a pod runs (R8: images by digest in production) --------------------------------
    SANDBOX_IMAGE: str
    PROXY_IMAGE: str = "nginxinc/nginx-unprivileged:stable-alpine"
    SANDBOX_RUNTIME: str = "runsc"               # gVisor, systrap (measured in CP2)
    SANDBOX_UID: int = 10001

    # ---- Where the pods' proxies forward to, and how they get there (R5, R12) ----------------
    GATEWAY_URL: str                             # agent-service's gateway (CP2: the dev gateway)
    EGRESS_NETWORK: str = "sandbox-egress"       # an ordinary bridge: the proxies' only way out
    POD_SUBNETS: str = "172.29.0.0/16"           # split into /28s, one per pod

    # ---- The pool (R6) and the limits of one sandbox (R4) ------------------------------------
    WARM: int = 2                                # pods kept ready
    MAX: int = 4                                 # pods in total, ready or claimed
    MEMORY_MB: int = 2048
    CPUS: float = 2.0
    PIDS: int = 256
    TMP_MB: int = 512                            # /tmp, in RAM
    WORKSPACE_QUOTA_MB: int = 10240              # /workspace, on the XFS disk (R13)
    MAX_SESSION_SECONDS: int = 3600              # hard timeout, whatever the profile asks
    READY_TIMEOUT_SECONDS: float = 60            # a pod that isn't warm by then is destroyed
    CLAIM_WAIT_SECONDS: float = 10.0             # a claim waits this long for a pod still warming up

    # ---- Paths on the VM (the runner sees them at the same paths) ----------------------------
    WORKSPACE_ROOT: Path = Path("/srv/workspaces")              # the XFS disk, prjquota
    STATE_ROOT: Path = Path("/run/sandbox-runner")              # tmpfs: session files, proxy configs

    # ---- Callers: a dev token until 2.5 brings auth-service tokens (aud=sandbox-runner) -------
    DEV_TOKEN_FILE: Path | None = None

    LOG_LEVEL: str = "INFO"
