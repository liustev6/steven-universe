"""templates.yaml (R14): validated when the runner starts."""

import pytest
from pydantic import ValidationError

from app.templates import load_templates


def write(tmp_path, text):
    p = tmp_path / "templates.yaml"
    p.write_text(text)
    return p


def test_loads_with_defaults(tmp_path):
    t = load_templates(write(tmp_path, "templates:\n  codex-chat: {image: 'ghcr.io/x@sha256:1', warm: 2}\n"))
    assert t["codex-chat"].warm == 2 and t["codex-chat"].runtime == "runsc" and t["codex-chat"].max is None


@pytest.mark.parametrize("text", [
    "templates: {}\n",                                              # none
    "templates:\n  Chat: {image: x}\n",                             # not lowercase
    "templates:\n  1chat: {image: x}\n",                            # not starting with a letter
    "templates:\n  'chat;rm': {image: x}\n",                        # not a simple name
    "templates:\n  chat: {image: x, privileged: true}\n",           # unknown field
    "templates:\n  chat: {image: x, warm: -1}\n",                   # nonsense limit
])
def test_refuses_bad_files(tmp_path, text):
    with pytest.raises(ValidationError):
        load_templates(write(tmp_path, text))
