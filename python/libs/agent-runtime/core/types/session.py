"""The session file: what a claimed sandbox is for (written at claim, read once; sandbox-runner R7)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SESSION_FILE = "/run/session/session.json"

# Codex's built-in tools a profile may switch on: shell commands, web search (runs at OpenAI), image viewing.
Tool = Literal["shell", "web_search", "view_image"]


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wall_seconds: int = 600        # the whole session (a courtesy: the gateway enforces budgets, G14)


class Profile(BaseModel):
    """What the agent is for this session (agent-service G5). Tools/skills arrive in later milestones."""

    model_config = ConfigDict(extra="forbid")
    name: str
    model: str | None = None       # one of the models Codex offers under your plan; None = Codex's default
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"] | None = None
    instructions: str = ""
    # True: `instructions` are added to Codex's own (a coding agent, ~10k tokens per call).
    # False: `instructions` replace them: a much smaller prompt, for profiles that aren't coding agents.
    codex_instructions: bool = True
    tools: list[Tool] = Field(default_factory=list)   # Codex's built-in tools allowed; [] = none (milestone 1)
    limits: Limits = Field(default_factory=Limits)


class SessionFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    capability: str                # the only credential a sandbox holds (agent-service G3)
    gateway_url: str
    profile: Profile
