"""Settings (prefix DEPLOYER_). Secrets are read from files, never from environment variables.

The environment's values live in infra/services/deployer/config.env (deployer spec D12). The
auth-service client comes from SERVICE_AUTH_ISSUER / _CLIENT_ID / _CLIENT_SECRET_FILE, like every
other service (service_auth.TokenClient.from_env).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEPLOYER_", extra="ignore")

    # ---- Who may ask for a deploy: GitHub Actions OIDC tokens matching ALL of these ------------
    # (spec "Token checks"). Numeric ids, not names: names can be renamed or re-registered.
    GITHUB_ISSUER: str = "https://token.actions.githubusercontent.com"
    GITHUB_AUDIENCE: str = "steven-universe-deployer"
    GITHUB_REPOSITORY_ID: str
    GITHUB_OWNER_ID: str
    GITHUB_WORKFLOW_REF: str            # <owner>/<repo>/.github/workflows/<file>@refs/heads/main
    GITHUB_REF: str = "refs/heads/main"
    GITHUB_ENVIRONMENT: str = "production"
    GITHUB_LEEWAY_SECONDS: int = 60

    # ---- The repository: manifests are read from it at the requested commit (D4) --------------
    REPO_URL: str = "https://github.com/liustev6/steven-universe.git"
    BRANCH: str = "main"
    STATE_DIR: Path = Path("/var/lib/deployer")   # writable: the clone and the audit log

    # ---- Hosts (D6): only those in HOSTS_FILE, with pinned host keys, via the restricted key ---
    HOSTS_FILE: Path = Path("/etc/deployer/hosts.yaml")
    SSH_KEY_FILE: Path = Path("/run/secrets/ssh_key")
    DEPLOY_TIMEOUT_SECONDS: int = 900

    # ---- OpenBao: login with an auth-service token (spec "The deployer's identity") -----------
    BAO_ADDR: str = "https://bao.lan.liustev6.ca:8200"
    BAO_AUTH_PATH: str = "auth-service"
    BAO_ROLE: str = "deployer"
    BAO_AUDIENCE: str = "secrets-manager"

    LOG_LEVEL: str = "INFO"

    @property
    def repo_dir(self) -> Path:
        return self.STATE_DIR / "repo.git"

    @property
    def audit_log(self) -> Path:
        return self.STATE_DIR / "audit.log"
