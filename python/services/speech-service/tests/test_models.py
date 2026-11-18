"""The weights' fetch (app/models.py) without the network: a fake download writes the files."""

import sys
import types
from pathlib import Path

import pytest

from app import models

FILES = {"config.json": b'{"a": 1}', "sub/model.bin": b"weights"}


@pytest.fixture
def lock(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    import yaml

    downloads = []

    def snapshot_download(repo, revision, local_dir, allow_patterns):
        downloads.append((repo, revision))
        for name, data in FILES.items():
            path = Path(local_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download))
    entry = {"repo": "org/m", "revision": "abc123", "files": {n: models.hashlib.sha256(d).hexdigest() for n, d in FILES.items()}}
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump({"models": {"m": entry}}))
    return path, tmp_path / "models", downloads


def test_downloads_checks_and_then_skips(lock):
    path, root, downloads = lock
    root.mkdir()
    assert models.fetch(str(path), str(root)) == 0
    assert (root / "m" / "sub" / "model.bin").read_bytes() == b"weights" and (root / "m" / ".verified").is_file()
    assert models.fetch(str(path), str(root)) == 0 and len(downloads) == 1        # verified already


def test_files_already_in_place_are_adopted_without_a_download(lock):
    path, root, downloads = lock
    for name, data in FILES.items():
        (root / "m" / name).parent.mkdir(parents=True, exist_ok=True)
        (root / "m" / name).write_bytes(data)
    assert models.fetch(str(path), str(root)) == 0 and downloads == []
    assert (root / "m" / ".verified").is_file()


def test_a_wrong_file_is_replaced_by_a_checked_download(lock):
    path, root, downloads = lock
    (root / "m").mkdir(parents=True)
    (root / "m" / "config.json").write_bytes(b"tampered")
    assert models.fetch(str(path), str(root)) == 0 and len(downloads) == 1
    assert (root / "m" / "config.json").read_bytes() == FILES["config.json"]


def test_a_download_that_doesnt_match_moves_nothing(lock, monkeypatch):
    path, root, downloads = lock
    root.mkdir()
    import yaml
    data = yaml.safe_load(path.read_text())
    data["models"]["m"]["files"]["config.json"] = "0" * 64
    path.write_text(yaml.safe_dump(data))
    assert models.fetch(str(path), str(root)) == 1
    assert not (root / "m").exists()
