#!/usr/bin/env bash
# Tests for plan.sh against a throwaway git history: what gets built, what gets deployed in which wave.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/lib.sh"
PLAN="$HERE/../plan.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
REGISTRY=ghcr.io/example

# Stand-ins: `docker manifest inspect` (an image exists if listed in $T/existing) and, on macOS,
# a GNU-compatible `realpath -m --relative-to=.`.
mkdir -p "$T/bin"
cat >"$T/bin/docker" <<'SH'
#!/bin/sh
grep -qxF "$3" "$EXISTING"
SH
cat >"$T/bin/realpath" <<'SH'
#!/bin/sh
for a; do last="$a"; done
python3 -c 'import os,sys; print(os.path.relpath(os.path.normpath(sys.argv[1])))' "$last"
SH
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH" EXISTING="$T/existing"
: >"$EXISTING"

# A repository with three services: a (pipeline, wave 1), b (pipeline, wave 2), c (by hand). Each
# Dockerfile copies the shared library libs/x; libs/y is copied by none (like agent-runtime).
R="$T/repo"; mkdir -p "$R"; cd "$R"
git init -q -b main; git config user.email t@example.com; git config user.name t
manifest() {   # service, pipeline, wave
  mkdir -p "infra/services/$1" "python/services/$1"
  printf 'service: %s\nhost: h\ncode: python/services/%s\nlibs: [x]\ncompose: python/services/%s/compose.yaml\nhealth: https://x/health\npipeline: %s\nwave: %s\n' \
    "$1" "$1" "$1" "$2" "$3" >"infra/services/$1/deploy.yaml"
  printf 'services:\n  %s:\n    image: %s/%s:${IMAGE_TAG:-local}\n    build:\n      context: ../..\n      dockerfile: services/%s/Dockerfile\n' \
    "$1" "$REGISTRY" "$1" "$1" >"python/services/$1/compose.yaml"
  echo v1 >"python/services/$1/app.py"
  printf 'FROM scratch\nCOPY libs/x /tmp/x\nCOPY services/%s/ /app\n' "$1" >"python/services/$1/Dockerfile"
}
manifest a true 1; manifest b true 2; manifest c false 1
mkdir -p python/libs/x python/libs/y docs; echo lib >python/libs/x/x.py; echo lib >python/libs/y/y.py; echo doc >docs/x.md
commit() { git add -A; git commit -qm "$1"; git rev-parse HEAD; }
C1="$(commit first)"
echo v2 >python/services/a/app.py;            C2="$(commit "a code")"
echo X=1 >infra/services/b/config.env;         C3="$(commit "b config")"
echo doc2 >docs/x.md;                          C4="$(commit "docs only")"
echo lib2 >python/libs/x/x.py;                 C5="$(commit "shared library")"
echo lib2 >python/libs/y/y.py;                 C6="$(commit "a library no service copies")"

run() {   # head, event, before, redeploy -> prints the outputs
  git checkout -q "$1"
  out="$T/out"; : >"$out"
  EVENT="$2" BEFORE="$3" REDEPLOY="$4" REGISTRY="$REGISTRY" GITHUB_SHA="${5:-$1}" GITHUB_OUTPUT="$out" \
    bash "$PLAN" >"$T/log" 2>&1 || echo "exit=$?" >>"$out"
  cat "$out"
}
svcs() { sed -n "s/^$1=//p" <<<"$2" | jq -r '[.[].service] | join(",")' 2>/dev/null; }
builds() { sed -n 's/^matrix=//p' <<<"$1" | jq -r '[.include[].service] | join(",")'; }

o="$(run "$C2" push "$C1" "")"
expect_eq "a's code changed: deploy a in wave 1" "$(svcs wave1 "$o")/$(svcs wave2 "$o")" "a/"
expect_eq "nothing exists yet: build all three" "$(builds "$o")" "a,b,c"

for s in a b c; do echo "$REGISTRY/$s:$(git log -1 --format=%H "$C5" -- python/services/$s python/libs/x)" >>"$EXISTING"; done
for s in a b c; do echo "$REGISTRY/$s:$(git log -1 --format=%H "$C3" -- python/services/$s python/libs/x)" >>"$EXISTING"; done

o="$(run "$C3" push "$C2" "")"
expect_eq "b's config changed: deploy b in wave 2, nothing to build" "$(svcs wave1 "$o")/$(svcs wave2 "$o")/$(builds "$o")" "/b/"
o="$(run "$C4" push "$C3" "")"
expect_eq "docs only: no deploys" "$(svcs wave1 "$o")/$(svcs wave2 "$o")" "/"
o="$(run "$C5" push "$C4" "")"
expect_eq "shared library changed: every pipeline service, by wave (never c)" "$(svcs wave1 "$o")/$(svcs wave2 "$o")" "a/b"
o="$(run "$C6" push "$C5" "")"
expect_eq "a library no Dockerfile copies: no deploys, no builds" "$(svcs wave1 "$o")/$(svcs wave2 "$o")/$(builds "$o")" "//"
o="$(run "$C5" push "0000000000000000000000000000000000000000" "")"
expect_eq "unknown previous push: everything changed" "$(svcs wave1 "$o")/$(svcs wave2 "$o")" "a/b"
o="$(run "$C5" workflow_dispatch "" b)"
expect_eq "redeploy=b: only b" "$(svcs wave1 "$o")/$(svcs wave2 "$o")" "/b"
o="$(run "$C5" workflow_dispatch "" c)"
expect_eq "redeploy=c (pipeline: false): nothing" "$(svcs wave1 "$o")/$(svcs wave2 "$o")" "/"
o="$(run "$C5" workflow_dispatch "" "")"
expect_eq "build-only dispatch: no deploys" "$(svcs wave1 "$o")/$(svcs wave2 "$o")" "/"
o="$(run "$C4" workflow_dispatch "" b "$C5")"
expect_contains "redeploy at an older ref is refused" "$o" "exit=1"
expect_eq "image sha of a at C4 = the last commit touching a or the libraries it copies" \
  "$(run "$C4" push "$C3" "" >/dev/null; git log -1 --format=%h "$C4" -- python/services/a python/libs/x)" "$(git rev-parse --short "$C2")"
git checkout -q "$C6"; rm python/services/a/Dockerfile; git commit -qam "no Dockerfile"; C7="$(git rev-parse HEAD)"
expect_contains "a missing Dockerfile is an error" "$(run "$C7" push "$C6" "")" "exit=1"
git checkout -q "$C6"; sed -i.bak 's/^libs: \[x\]$/libs: [x, y]/' infra/services/b/deploy.yaml && rm infra/services/b/deploy.yaml.bak
git commit -qam "b declares a library its Dockerfile doesn't copy"; C8="$(git rev-parse HEAD)"
o="$(run "$C8" push "$C6" "")"
expect_contains "libs that don't match the Dockerfile fail the plan" "$o$(cat "$T/log")" "doesn't match what"

# Images that aren't services (infra/images/): built when their inputs change; a service listing one
# is deployed then too (sandbox-runner.md R8), and no other.
git checkout -q "$C6"
mkdir -p infra/images python/libs/img && echo v1 >python/libs/img/x.py
printf 'FROM scratch\nCOPY x.py /x\n' >python/libs/img/Dockerfile
printf 'image: img\ncode: python/libs/img\ncontext: python/libs/img\ndockerfile: python/libs/img/Dockerfile\n' >infra/images/img.yaml
printf 'images: [img]\n' >>infra/services/a/deploy.yaml
C9="$(commit "an image, used by a")"
echo v2 >python/libs/img/x.py; C10="$(commit "the image changes")"
o="$(run "$C10" push "$C9" "")"
expect_eq "an image's change: builds it, deploys the service that runs it, and only that one" \
  "$(builds "$o")/$(svcs wave1 "$o")/$(svcs wave2 "$o")" "img/a/"
finish
