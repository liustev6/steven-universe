"""Profiles: what an agent is for (control-plane.md C12). Read from profiles.yaml, a reviewed file.

A profile holds two kinds of settings:
- what the **runtime** needs (model, effort, instructions, tools, wall time): sent to the sandbox in
  the session file as protocol A's `Profile`, built by `AgentProfile.runtime()`;
- what only **agent-service** uses: the runner's `template`, the budgets the gateway enforces
  (C13), and how much history a new session carries (C9). The sandbox never sees these.

Any caller with `chat:write` may start any profile (C2): a profile's cost is bounded by its limits.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.types import Limits as RuntimeLimits
from core.types import Profile as RuntimeProfile
from core.types import Tool

NAME_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"


class Limits(BaseModel):
    """Budgets (C13): the gateway refuses the model past any of them."""

    model_config = ConfigDict(extra="forbid")
    # The whole session, then it ends. At most the runner's own hard limit (its MAX_SESSION_SECONDS,
    # 3600): past that the runner destroys the pod, and the session would end as sandbox_lost.
    wall_seconds: int = Field(3600, gt=0, le=3600)
    max_turns: int = Field(100, gt=0)                      # turns per session
    max_turn_tokens: int = Field(200_000, gt=0)            # tokens in one turn, from the runtime's usage events


class History(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carry_turns: int = Field(6, ge=0, le=50)               # the conversation's last N turns, into a new session (C9)


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str = Field(pattern=NAME_PATTERN)            # the runner's template (sandbox-runner R14)
    model: str | None = None                               # None: Codex's default for your plan
    effort: str | None = None                              # checked against protocol A's values below
    instructions: str = ""
    codex_instructions: bool = True                        # False: `instructions` replace Codex's own
    tools: list[Tool] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)
    history: History = Field(default_factory=History)

    @model_validator(mode="after")
    def _runtime_accepts_it(self) -> "AgentProfile":
        self.runtime("check")       # a profile the runtime would reject fails at load, not at a claim
        return self

    def runtime(self, name: str, context: str = "") -> RuntimeProfile:
        """Protocol A's Profile for the session file. `context` (the carried-over turns, C9) is
        added after the instructions, so the runtime needs nothing new to use it."""
        instructions = "\n\n".join(part for part in (self.instructions.strip(), context.strip()) if part)
        return RuntimeProfile(
            name=name, model=self.model, effort=self.effort, instructions=instructions,
            codex_instructions=self.codex_instructions, tools=self.tools,
            limits=RuntimeLimits(wall_seconds=self.limits.wall_seconds),
        )


class ProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profiles: dict[str, AgentProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def _names(self) -> "ProfilesFile":
        bad = [n for n in self.profiles if not re.match(NAME_PATTERN, n)]
        if bad:
            raise ValueError(f"profile names must match {NAME_PATTERN}: {bad}")
        return self


def load_profiles(path: Path) -> dict[str, AgentProfile]:
    """Every profile in the file, validated; raises ValueError on any problem (the service won't start)."""
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return ProfilesFile.model_validate(raw).profiles
