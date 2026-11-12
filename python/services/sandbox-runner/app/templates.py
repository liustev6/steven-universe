"""Templates: the kinds of sandbox the runner can make (sandbox-runner.md R14).

A template is a name and everything a pod of that kind needs: the image, the container runtime,
how many to keep warm, and the limits. A claim names a template, never an image or a path: the
runner maps the name to what it runs, from this reviewed file. Which template a session uses is
the profile's choice (agent-service, R1); the runner just carries it out.

    templates:
      codex-chat:
        image: ghcr.io/…/agent-runtime@sha256:…
        warm: 2
        memory_mb: 1024
      codex-dev:
        image: ghcr.io/…/agent-runtime-dev@sha256:…
        warm: 0                    # started on demand
        workspace_mb: 20480
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    image: str                                   # by digest in production (R8)
    runtime: str = "runsc"                       # gVisor (systrap), measured in CP2
    warm: int = Field(0, ge=0)                   # pods kept ready
    max: int | None = Field(None, ge=1)          # pods of this kind in total; None = the runner's per-template cap
    memory_mb: int = Field(2048, ge=128)
    cpus: float = Field(2.0, gt=0)
    pids: int = Field(256, ge=16)
    tmp_mb: int = Field(512, ge=16)              # /tmp, in RAM
    workspace_mb: int = Field(10240, ge=16)      # /workspace, on the XFS disk (R13)


class TemplateFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    templates: dict[str, Template]

    @model_validator(mode="after")
    def _names(self) -> "TemplateFile":
        for name in self.templates:
            simple = name.replace("-", "").isalnum() and name.isascii() and name.islower()
            if not simple or not name[0].isalpha() or len(name) > 32:
                raise ValueError(f"template name {name!r}: lowercase letters, digits and dashes, starting with a letter")
        if not self.templates:
            raise ValueError("no templates")
        return self


def load_templates(path: Path) -> dict[str, Template]:
    return TemplateFile.model_validate(yaml.safe_load(path.read_text())).templates
