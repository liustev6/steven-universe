from pathlib import Path

import pytest

from app.profiles import load_profiles

REAL = Path(__file__).resolve().parents[4] / "infra/services/agent-service/profiles.yaml"


def write(tmp_path, text: str) -> Path:
    p = tmp_path / "profiles.yaml"
    p.write_text(text)
    return p


def test_the_real_file_loads():
    profiles = load_profiles(REAL)
    assert {"steven-ai", "voice", "smoke"} <= profiles.keys()
    assert profiles["smoke"].limits.max_turns == 2
    assert profiles["smoke"].history.carry_turns == 0


def test_runtime_profile_carries_only_what_the_sandbox_needs():
    p = load_profiles(REAL)["voice"]
    runtime = p.runtime("voice").model_dump()
    assert runtime["name"] == "voice" and runtime["effort"] == "low"
    # No template, budgets or history: those stay with agent-service.
    assert set(runtime) == {"name", "model", "effort", "instructions", "codex_instructions", "tools", "limits"}
    assert runtime["limits"] == {"wall_seconds": 1800}


def test_context_follows_the_instructions():
    p = load_profiles(REAL)["voice"]
    text = p.runtime("voice", context="Earlier in this conversation:\nYou: hi\nAssistant: hello").instructions
    assert text.startswith("You answer by voice.")
    assert text.endswith("Assistant: hello")
    assert p.runtime("voice").instructions == p.instructions.strip()


@pytest.mark.parametrize("body, problem", [
    ("profiles: {a: {template: chat, effort: loud}}", "effort"),          # the runtime would refuse it
    ("profiles: {a: {template: chat, tools: [rm_rf]}}", "tools"),
    ("profiles: {a: {template: chat, colour: red}}", "colour"),           # unknown keys are errors
    ("profiles: {Bad_Name: {template: chat}}", "names"),
    ("profiles: {a: {template: chat, limits: {max_turns: 0}}}", "max_turns"),
    ("profiles: {}", "profiles"),
    ("profiles: [", "profiles.yaml"),                                      # not YAML
])
def test_bad_files_fail_at_load(tmp_path, body, problem):
    with pytest.raises(ValueError, match=problem):
        load_profiles(write(tmp_path, body))
