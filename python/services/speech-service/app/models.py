"""The models' weights, pinned and checked (speech-service.md T4). Not in the image: the deploy's
pre-start hook runs `fetch` with the new image, so the host has every file of every model, at its
pinned revision, each matching its SHA-256 in infra/services/speech-service/models.yaml, before the
service starts. The service mounts them read-only.

    python -m app.models fetch <models.yaml> <dir>     download what's missing or wrong; check every file
    python -m app.models lock <name> <repo> <revision> <dir>    a models.yaml entry from a verified copy

`fetch`, per model: if <dir>/<name>/.verified holds this entry's digest, nothing to do. If the folder
is there without it (copied in by hand, or an older lock) and every file matches, it's adopted: the
marker written, nothing downloaded. Otherwise the pinned revision's files are downloaded into <dir>/.incoming/<name>, every file's SHA-256 checked
against the lock (one wrong or missing file: nothing is moved, the deploy stops), and only then the
folder replaces <dir>/<name>. A model the lock no longer lists is left alone (deleted by hand).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def digest(entry: dict) -> str:
    """The entry's own digest: what .verified records (a changed lock means a new check)."""
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


def check(folder: Path, files: dict[str, str]) -> list[str]:
    """What's wrong with `folder` against `files` (path → SHA-256): missing or mismatched files."""
    problems = []
    for name, want in files.items():
        path = folder / name
        if not path.is_file():
            problems.append(f"missing: {name}")
        elif (got := sha256(path)) != want:
            problems.append(f"{name}: sha256 {got[:12]}…, expected {want[:12]}…")
    return problems


def fetch(lock_path: str, root: str) -> int:
    import yaml
    from huggingface_hub import snapshot_download

    models = yaml.safe_load(open(lock_path))["models"]
    root_dir = Path(root)
    for name, entry in models.items():
        target, marker = root_dir / name, root_dir / name / ".verified"
        if marker.is_file() and marker.read_text().strip() == digest(entry):
            print(f"{name}: verified already ({entry['revision'][:7]})")
            continue
        if target.is_dir() and not check(target, entry["files"]):              # all there and right: adopt it
            marker.write_text(digest(entry) + "\n")
            print(f"{name}: verified in place, {len(entry['files'])} files ({entry['revision'][:7]}), nothing downloaded")
            continue
        incoming = root_dir / ".incoming" / name
        shutil.rmtree(incoming, ignore_errors=True)
        print(f"{name}: downloading {entry['repo']} at {entry['revision'][:7]} ({len(entry['files'])} files)", flush=True)
        snapshot_download(entry["repo"], revision=entry["revision"], local_dir=incoming,
                          allow_patterns=list(entry["files"]))
        if problems := check(incoming, entry["files"]):
            print(f"{name}: NOT VERIFIED, nothing moved:\n  " + "\n  ".join(problems), file=sys.stderr)
            return 1
        shutil.rmtree(incoming / ".cache", ignore_errors=True)            # huggingface_hub's bookkeeping
        (incoming / ".verified").write_text(digest(entry) + "\n")
        old = root_dir / ".old" / name
        shutil.rmtree(old, ignore_errors=True)
        if target.exists():
            old.parent.mkdir(parents=True, exist_ok=True)
            target.rename(old)
        incoming.rename(target)
        shutil.rmtree(old, ignore_errors=True)
        print(f"{name}: verified, {len(entry['files'])} files")
    for leftover in (root_dir / ".incoming", root_dir / ".old"):
        shutil.rmtree(leftover, ignore_errors=True)
    for path in root_dir.rglob("*"):                                       # readable by the service (uid 10001)
        path.chmod(0o755 if path.is_dir() else 0o644)
    root_dir.chmod(0o755)
    return 0


def lock(name: str, repo: str, revision: str, folder: str) -> int:
    """A models.yaml entry (printed) from a copy you've verified yourself: every file but dotfiles."""
    import yaml

    base = Path(folder)
    files = {str(p.relative_to(base)): sha256(p) for p in sorted(base.rglob("*"))
             if p.is_file() and not any(part.startswith(".") for part in p.relative_to(base).parts)}
    print(yaml.safe_dump({name: {"repo": repo, "revision": revision, "files": files}}, sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    command, *rest = sys.argv[1:] or ["?"]
    if command == "fetch" and len(rest) == 2:
        sys.exit(fetch(*rest))
    if command == "lock" and len(rest) == 4:
        sys.exit(lock(*rest))
    sys.exit(__doc__)
