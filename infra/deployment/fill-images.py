#!/usr/bin/env python3
"""Fill in a deployed file's image references (deployer: manifest `files` and `images`).

    fill-images.py <in> <out> <name>=<ref> [<name>=<ref> ...]

Replaces every ${image:<name>} in <in> with <ref> (an image pinned by digest, e.g.
ghcr.io/…/agent-runtime@sha256:…) and writes <out>. Fails, writing nothing, if the file names an
image that wasn't given: a deployed file never keeps a placeholder. Standard library only (it runs
on every host).
"""

import os
import re
import sys
import tempfile

PLACEHOLDER = re.compile(r"\$\{image:([a-z][a-z0-9-]*)\}")   # names as in the manifest schema
ANYTHING_LIKE_ONE = "${image:"


def fill(text: str, refs: dict[str, str]) -> str:
    unknown = sorted({m.group(1) for m in PLACEHOLDER.finditer(text)} - refs.keys())
    if unknown:
        raise SystemExit(f"fill-images: no image given for {', '.join(unknown)} (the manifest's `images`)")
    filled = PLACEHOLDER.sub(lambda m: refs[m.group(1)], text)
    if ANYTHING_LIKE_ONE in filled:
        line = next(n for n, l in enumerate(filled.splitlines(), 1) if ANYTHING_LIKE_ONE in l)
        raise SystemExit(f"fill-images: line {line}: not a valid placeholder (${{image:<lowercase name>}})")
    return filled


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    src, out, *pairs = argv
    refs = {}
    for pair in pairs:
        name, sep, ref = pair.partition("=")
        if not sep or not name or "@sha256:" not in ref:
            raise SystemExit(f"fill-images: expected <name>=<image>@sha256:<digest>, got {pair!r}")
        refs[name] = ref
    text = fill(open(src).read(), refs)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(out)))
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, out)


if __name__ == "__main__":
    main(sys.argv[1:])
