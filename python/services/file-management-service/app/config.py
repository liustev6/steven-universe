"""Settings (prefix FILES_). Secrets are read from files, never from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MiB = 1024 * 1024
GiB = 1024 * MiB


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FILES_", extra="ignore")

    # The buckets this service manages (spec D10). Must equal auth-service's
    # policy `location_roots` for the `file_access` detail type.
    BUCKETS: list[str] = ["public", "private"]

    # Storage (RustFS, S3 API). Credentials are files mounted read-only.
    STORAGE_ENDPOINT: str = "http://192.168.50.123:9000"
    STORAGE_REGION: str = "us-east-1"
    STORAGE_ACCESS_KEY_FILE: Path = Path("/run/secrets/rustfs_access_key")
    STORAGE_SECRET_KEY_FILE: Path = Path("/run/secrets/rustfs_secret_key")
    STORAGE_TIMEOUT_SECONDS: float = 30

    # Uploads: largest object per request, optionally per bucket, and the multipart part
    # size (S3 minimum 5 MiB). Memory per upload is about one part.
    MAX_UPLOAD_BYTES: int = 1 * GiB
    MAX_UPLOAD_BYTES_PER_BUCKET: dict[str, int] = Field(default_factory=dict)
    PART_SIZE_BYTES: int = 8 * MiB

    LIST_MAX_KEYS: int = 1000
    LOG_LEVEL: str = "INFO"

    def max_upload_bytes(self, bucket: str) -> int:
        return self.MAX_UPLOAD_BYTES_PER_BUCKET.get(bucket, self.MAX_UPLOAD_BYTES)

    def storage_credentials(self) -> tuple[str, str]:
        for name in ("FILES_STORAGE_ACCESS_KEY", "FILES_STORAGE_SECRET_KEY"):
            if os.environ.get(name):
                raise RuntimeError(f"{name} is not allowed; mount the secret as a file (*_FILE)")
        return (
            self.STORAGE_ACCESS_KEY_FILE.read_text().strip(),
            self.STORAGE_SECRET_KEY_FILE.read_text().strip(),
        )
