from pathlib import Path

import pytest

from app.profiles import load_profiles
from tests.helpers import PROFILES

REAL = Path(__file__).resolve().parents[4] / "infra/services/agent-service/profiles.yaml"


def write(tmp_path, text: str) -> Path:
    p = tmp_path / "profiles.yaml"
    p.write_text(text)
    return p


def test_the_real_file_loads():
    profiles = load_profiles(REAL)
    assert {"pi", "smoke"} <= profiles.keys()
    assert profiles["smoke"].limits.max_turns == 2
    assert profiles["smoke"].history.carry_turns == 0
    assert profiles["smoke"].allow_caller_instructions is False
    pi = profiles["pi"]
    assert pi.allow_caller_instructions and pi.tools == ["web_search"]        # pi-server adds where it is
    assert not pi.allow_codex_instructions                                    # ours replace Codex's
    assert "Seattle" not in pi.instructions                                   # ... so the profile doesn't say


def test_runtime_profile_carries_only_what_the_sandbox_needs():
    p = PROFILES["voice"]
    runtime = p.runtime("voice").model_dump()
    assert runtime["name"] == "voice" and runtime["effort"] == "low"
    # No template, budgets, history or allow_caller_instructions: those stay with agent-service.
    # allow_codex_instructions goes as protocol A's `codex_instructions` (the runtime's own name).
    assert set(runtime) == {"name", "model", "effort", "instructions", "codex_instructions", "tools", "limits"}
    assert runtime["limits"] == {"wall_seconds": 1800}


def test_instructions_are_the_profiles_then_the_callers_then_the_context():
    p = PROFILES["panel"]
    text = p.runtime("panel", context="Earlier in this conversation:\nYou: hi\nAssistant: hello",
                     caller="In Seattle.").instructions
    assert text == "You are the panel's voice assistant.\n\nIn Seattle.\n\nEarlier in this conversation:\nYou: hi\nAssistant: hello"
    assert p.runtime("panel").instructions == p.instructions.strip()
    bare = load_profiles_text("profiles: {a: {template: chat, allow_caller_instructions: true}}")["a"]
    assert bare.instructions == "" and bare.runtime("a", caller="Only mine.").instructions == "Only mine."


@pytest.mark.parametrize("body, problem", [
    ("profiles: {a: {template: chat, effort: loud}}", "effort"),          # the runtime would refuse it
    ("profiles: {a: {template: chat, tools: [rm_rf]}}", "tools"),
    ("profiles: {a: {template: chat, colour: red}}", "colour"),           # unknown keys are errors
    ("profiles: {a: {template: chat, allow_caller_instructions: replace}}", "allow_caller_instructions"),  # a bool
    # the old keys fail loudly, never silently ignored
    ("profiles: {a: {template: chat, caller_instructions: append}}", "caller_instructions"),
    ("profiles: {a: {template: chat, codex_instructions: false}}", "codex_instructions"),
    ("profiles: {a: {template: chat, tool_instructions: append}}", "tool_instructions"),
    ("profiles: {a: {template: chat, files_instructions: append}}", "files_instructions"),
    ("profiles: {Bad_Name: {template: chat}}", "names"),
    ("profiles: {a: {template: chat, limits: {max_turns: 0}}}", "max_turns"),
    ("profiles: {}", "profiles"),
    ("profiles: [", "profiles.yaml"),                                      # not YAML
])
def test_bad_files_fail_at_load(tmp_path, body, problem):
    with pytest.raises(ValueError, match=problem):
        load_profiles(write(tmp_path, body))


def load_profiles_text(text: str):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        return load_profiles(write(Path(d), text))
