"""Profiles: what an agent is for (control-plane.md C12). Read from profiles.yaml, a reviewed file.

A profile holds two kinds of settings:
- what the **runtime** needs (model, effort, instructions, tools, wall time): sent to the sandbox in
  the session file as protocol A's `Profile`, built by `AgentProfile.runtime()`;
- what only **agent-service** uses: the runner's `template`, the budgets the gateway enforces
  (C13), how much history a new session carries (C9), and whether a caller may add instructions
  (C15). The sandbox never sees these.

The agent's instructions, in order, from what never changes to what changes most (the prompt
cache shares the longest identical start of a prompt): the profile's own rules, the session's tools
(`tool_instructions`, generated from its tool list), the caller's (C15), the carried-over turns (C9). The profile's come first and a caller can only add to them; what an agent may *do*
(model, tools, budgets) is enforced at the gateway, never by the instructions.

Any caller with `chat:write` may start any profile (C2): a profile's cost is bounded by its limits.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.types import Limits as RuntimeLimits
from core.types import Profile as RuntimeProfile
from core.types import Tool

NAME_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"
# tools-service locations (tools.md): a namespace ("weather/") or one tool ("weather/forecast")
TOOL_LOCATION = re.compile(r"^[a-z][a-z0-9_]*/([a-z][a-z0-9_]*)?$")
# file-management locations (files-and-mounts.md F6): a bucket or a folder in it, ending in "/";
# `${client}` is the session's caller (as auth-service's policy writes it)
FILE_LOCATION = re.compile(r"^[a-z0-9-]+/(([A-Za-z0-9._-]+|\$\{client\})/)*$")
CALLER_INSTRUCTIONS_MAX = 4000                             # characters (C15)


class FileAccess(BaseModel):
    """What a session asks file-management for (RFC 9396 `file_access`, as in policy.yaml): it gets the
    overlap with its caller's own grant (files-and-mounts.md F6)."""

    model_config = ConfigDict(extra="forbid")
    actions: list[Literal["read", "list", "write", "delete"]] = Field(min_length=1)
    locations: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _locations(self) -> "FileAccess":
        bad = [loc for loc in self.locations
               if not FILE_LOCATION.match(loc) or any(seg in (".", "..") for seg in loc.split("/"))]
        if bad:
            raise ValueError(f"files locations: <bucket>/<folder>/…/, ending in '/': {bad}")
        return self


class Limits(BaseModel):
    """Budgets (C13): the gateway refuses the model past any of them."""

    model_config = ConfigDict(extra="forbid")
    # The whole session, then it ends. At most the runner's own hard limit (its MAX_SESSION_SECONDS,
    # 3600): past that the runner destroys the pod, and the session would end as sandbox_lost.
    wall_seconds: int = Field(3600, gt=0, le=3600)
    max_turns: int = Field(100, gt=0)                      # turns per session
    max_turn_tokens: int = Field(200_000, gt=0)            # tokens in one turn, from the runtime's usage events
    max_tool_calls: int = Field(20, gt=0)                  # tools-service calls per session, counted at the gateway (TL10)


class History(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carry_turns: int = Field(6, ge=0, le=50)               # the conversation's last N turns, into a new session (C9)


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str = Field(pattern=NAME_PATTERN)            # the runner's template (sandbox-runner R14)
    model: str | None = None                               # None: Codex's default for your plan
    effort: str | None = None                              # checked against protocol A's values below
    instructions: str = ""                                 # the profile's own rules; optional
    caller_instructions: Literal["none", "append"] = "none"   # may a caller add its own (C15)
    # append: the session's tools-service tools, listed in its instructions (tools.md): Codex keeps MCP
    # tools behind its tool search, so a model not told about them takes a built-in tool instead
    tool_instructions: Literal["none", "append"] = "none"
    codex_instructions: bool = True                        # False: `instructions` replace Codex's own
    tools: list[Tool] = Field(default_factory=list)       # Codex's built-in tools only (web_search, ...)
    # tools-service's tools (tools.md TL5): None (the default) = the owner's whole grant there; a list
    # of locations narrows it (the overlap); [] = none. Never a second copy of the grant.
    mcp_tools: list[str] | None = None
    # file-management (files-and-mounts.md F6): what its sessions ask for; [] (the default) = no files.
    # read/list locations are mounted at /workspace/inputs/<bucket>/<folder>/; write/delete: `publish`.
    files: list[FileAccess] = Field(default_factory=list)
    # append: the session's granted folders, listed in its instructions after the tools (where to look)
    files_instructions: Literal["none", "append"] = "none"
    limits: Limits = Field(default_factory=Limits)
    history: History = Field(default_factory=History)

    @model_validator(mode="after")
    def _mcp_tools(self) -> "AgentProfile":
        bad = [t for t in self.mcp_tools or [] if not TOOL_LOCATION.match(t)]
        if bad:
            raise ValueError(f"mcp_tools: <namespace>/ or <namespace>/<tool>, lowercase: {bad}")
        return self

    @model_validator(mode="after")
    def _runtime_accepts_it(self) -> "AgentProfile":
        self.runtime("check")       # a profile the runtime would reject fails at load, not at a claim
        return self

    def runtime(self, name: str, context: str = "", caller: str = "", tools: str = "") -> RuntimeProfile:
        """Protocol A's Profile for the session file: the profile's instructions, then `tools` (the
        session's tool block, when the profile appends it), then the caller's (`caller`, C15; checked
        by the caller of this), then `context` (the carried-over turns, C9), as one text, so the
        runtime needs nothing new to use them."""
        parts = (self.instructions.strip(), tools.strip(), caller.strip(), context.strip())
        instructions = "\n\n".join(part for part in parts if part)
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
