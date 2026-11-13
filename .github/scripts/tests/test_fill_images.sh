#!/usr/bin/env bash
# Tests for infra/deployment/fill-images.py: placeholders filled with pinned images, never left behind.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/lib.sh"
# The script under test is the host side (infra/), not CI's: in the repo next to these tests, or, in CI
# (whose .ci/ checkout holds only .github/scripts), in the main checkout the job runs from.
FILL="$HERE/../../../infra/deployment/fill-images.py"
[ -f "$FILL" ] || FILL="$PWD/infra/deployment/fill-images.py"
[ -f "$FILL" ] || { echo "FAIL  fill-images.py not found"; exit 1; }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
REF="ghcr.io/x/agent-runtime@sha256:$(printf 'a%.0s' $(seq 64))"
printf 'chat:\n  image: ${image:agent-runtime}\ndev:\n  image: ${image:agent-runtime}\n' >"$T/in.yaml"

python3 "$FILL" "$T/in.yaml" "$T/out.yaml" "agent-runtime=$REF"
expect_eq "every placeholder is filled" "$(grep -c "$REF" "$T/out.yaml")" "2"
expect_eq "no placeholder left" "$(grep -c '\${image:' "$T/out.yaml")" "0"

rm -f "$T/out2.yaml"
o="$(python3 "$FILL" "$T/in.yaml" "$T/out2.yaml" "other=$REF" 2>&1; echo "exit=$?")"
expect_contains "an image not given fails" "$o" "no image given for agent-runtime"
expect_eq "and writes nothing" "$([ -e "$T/out2.yaml" ] && echo written || echo none)" "none"

o="$(python3 "$FILL" "$T/in.yaml" "$T/out3.yaml" "agent-runtime=ghcr.io/x/agent-runtime:latest" 2>&1; echo "exit=$?")"
expect_contains "a ref not pinned by digest is refused" "$o" "expected <name>=<image>@sha256"
finish
