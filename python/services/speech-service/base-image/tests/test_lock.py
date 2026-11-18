"""speech-base's lock (requirements.lock): exact, complete enough to import, and without what the image
leaves out. The packages themselves were tested by every speech-service test, in this image (4.6)."""

from pathlib import Path

LOCK = Path(__file__).parent.parent / "requirements.lock"


def pins() -> dict[str, str]:
    lines = [l.strip() for l in LOCK.read_text().splitlines() if l.strip() and not l.startswith("#")]
    out = {}
    for line in lines:
        assert "==" in line and " " not in line, f"not an exact pin: {line}"
        name, version = line.split("==")
        key = name.lower().replace("_", "-")
        assert key not in out, f"twice: {name}"
        out[key] = version
    return out


def test_every_line_is_an_exact_pin_once():
    assert len(pins()) > 100


def test_the_model_stack_is_the_tested_one():
    p = pins()
    assert p["torch"] == "2.15.0+cu132" and p["triton"] == "3.9.0"
    assert p["transformers"] == "4.57.6"                  # qwen-asr's pin (qwen-tts pins 4.57.3: spike 10)
    assert p["qwen-tts"] == "0.1.1" and p["qwen-asr"] == "0.0.6"
    assert any(k.startswith("nvidia-cudnn") for k in p) and "nvidia-cublas" in p


def test_what_the_image_leaves_out():
    p = pins()
    for name in ("gradio", "torchvision", "pandas", "pyarrow", "jiwer", "pytest", "service-auth"):
        assert name not in p, name


def test_service_auths_dependencies_are_in():
    """speech-service copies service-auth onto the path instead of installing it (its image has only
    COPY steps), so its dependencies must be here."""
    p = pins()
    for name in ("pyjwt", "cryptography", "httpx", "fastapi"):
        assert name in p, name


def test_text_normalization_is_in():
    """wetext and its dependencies (spike 13: numbers and symbols as words before the TTS)."""
    p = pins()
    assert p["wetext"] == "0.2.1" and p["kaldifst"] == "1.9.0"
    for name in ("contractions", "textsearch", "anyascii", "pyahocorasick", "click"):
        assert name in p, name
