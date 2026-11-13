#!/usr/bin/env bash
# plan.sh: what this run builds and deploys. Run by .github/workflows/build.yml (job `plan`) from the
# root of the checked-out commit; tested by .github/scripts/tests/test_plan.sh.
#
# Builds (deployer spec D1a): for every infra/services/*/deploy.yaml, the image is named by the
# commit that last changed its inputs: its `code` folder and its `libs` (python/libs/<name>). The
# manifest's `libs` must match the Dockerfile's `COPY libs/<name> …` lines exactly, or this fails:
# deploy.sh and deploy-target.sh name the image from `libs` too, so they can't drift. It is built only if
# $REGISTRY/<service>:<that sha> doesn't exist yet, so a config-only commit builds nothing and a
# re-run is always safe. Build settings (context, Dockerfile, image name) come from the service's
# compose file: one place for them (D12).
#
# Images that aren't services (infra/images/*.yaml, e.g. the sandbox image agent-runtime) are built
# the same way, and never deployed themselves: a service that lists one under `images` is deployed
# when that image's inputs change too (sandbox-runner.md R8).
#
# Deploys (D16, D19): only `pipeline: true` services. On a push: those whose image inputs, listed
# images' inputs or infra/services/<svc>/ changed since the previous push. On workflow_dispatch: the one named in
# $REDEPLOY. Each goes into its manifest's `wave` (default 1; 2 = the edge, after wave 1).
#
# Environment: EVENT (push | workflow_dispatch), BEFORE (the previous push's sha), REDEPLOY,
# REGISTRY, GITHUB_SHA, GITHUB_OUTPUT. Outputs: matrix, any, wave1, wave2 (JSON).
set -euo pipefail

entries='[]'   # images to build
wave1='[]'     # services to deploy, by wave
wave2='[]'

# What changed in this push (a new or rewritten history: treat everything as changed).
all_changed=false
changed=""
if [ "$EVENT" = push ]; then
  if [ -n "${BEFORE:-}" ] && git cat-file -e "$BEFORE^{commit}" 2>/dev/null; then
    changed="$(git diff --name-only "$BEFORE" HEAD)"
  else
    all_changed=true
  fi
fi
if [ -n "${REDEPLOY:-}" ] && [ "$(git rev-parse HEAD)" != "$GITHUB_SHA" ]; then
  echo "::error::redeploy only works at main's latest commit (ref=main)"
  exit 1
fi

# libs <yaml file> <Dockerfile>: the file's `libs`, checked against what the Dockerfile copies (a
# source `libs/<name>` of a COPY line); prints them space-separated, or fails the plan.
libs() {
  local copied declared
  copied="$(sed -nE 's/^[[:space:]]*COPY[[:space:]]+//p' "$2" | tr -s ' \t' '\n' \
    | sed -nE 's|^libs/([A-Za-z0-9._-]+)/?$|\1|p' | sort -u | tr '\n' ' ')"
  declared="$(yq -r '.libs // [] | .[]' "$1" | sort -u | tr '\n' ' ')"
  [ "$copied" = "$declared" ] || {
    echo "::error file=$1::libs [${declared% }] doesn't match what $2 copies [${copied% }]" >&2; return 1; }
  printf '%s' "$declared"
}
lib_paths() { for l in $1; do printf 'python/libs/%s ' "$l"; done; }
regex() { sed 's/\./\\./g'; }

for m in infra/services/*/deploy.yaml; do
  svc="$(yq -r '.service' "$m")"
  code="$(yq -r '.code // ""' "$m")"
  compose="$(yq -r '.compose' "$m")"
  [ -n "$code" ] || { echo "::error file=$m::no 'code' folder (needed for the image sha, D1a)"; exit 1; }
  q() { yq -r "explode(.) | .services[\"$svc\"] | $1" "$compose"; }
  context="$(realpath -m --relative-to=. "$(dirname "$compose")/$(q .build.context)")"
  dockerfile="$context/$(q .build.dockerfile)"
  [ -f "$dockerfile" ] || { echo "::error file=$compose::no Dockerfile at $dockerfile"; exit 1; }
  # Config `files` reach the container through a mount compose can't see change: its compose must
  # use their hash (deploy-target.sh sets CONFIG_FILES_SHA), or a new file never restarts it.
  if [ "$(yq -r '.files // [] | length' "$m")" != 0 ] && ! grep -q 'CONFIG_FILES_SHA' "$compose"; then
    echo "::error file=$compose::$svc has config files; label its container with \${CONFIG_FILES_SHA} (see sandbox-runner)"; exit 1
  fi

  # Inputs (D1a): the code and the manifest's `libs` (checked against the Dockerfile). A library no
  # image includes changes no image.
  declared="$(libs "$m" "$dockerfile")" || exit 1
  inputs="$code $(lib_paths "$declared")"                     # repository paths, no spaces
  image_sha="$(git log -1 --format=%H HEAD -- $inputs)"
  [ -n "$image_sha" ] || { echo "::error file=$m::no commit touches $code"; exit 1; }
  watched="$inputs infra/services/$svc"
  for img in $(yq -r '.images // [] | .[]' "$m"); do            # images it runs (R8): their inputs too
    f="infra/images/$img.yaml"
    [ -f "$f" ] || { echo "::error file=$m::images: no $f"; exit 1; }
    watched="$watched $(yq -r .code "$f") $(lib_paths "$(yq -r '.libs // [] | .[]' "$f")")"
  done
  watched="$(printf '%s|' $watched | sed 's/|$//' | regex)"

  # ---- deploy? ---------------------------------------------------------------------------------
  if [ "$(yq -r '.pipeline // false' "$m")" = true ]; then
    want=false
    if [ "$EVENT" = workflow_dispatch ]; then
      [ "${REDEPLOY:-}" = "$svc" ] && want=true
    elif [ "$all_changed" = true ] || grep -qE "^($watched)/" <<<"$changed"; then
      want=true
    fi
    if [ "$want" = true ]; then
      wave="$(yq -r '.wave // 1' "$m")"
      item="$(jq -nc --arg s "$svc" --arg i "$image_sha" '{service: $s, image_sha: $i}')"
      case "$wave" in
        1) wave1="$(jq -c --argjson x "$item" '. + [$x]' <<<"$wave1")" ;;
        2) wave2="$(jq -c --argjson x "$item" '. + [$x]' <<<"$wave2")" ;;
        *) echo "::error file=$m::wave must be 1 or 2"; exit 1 ;;
      esac
      echo "$svc: deploy in wave $wave (image ${image_sha:0:7})"
    fi
  fi

  # ---- build? ----------------------------------------------------------------------------------
  want_image="$REGISTRY/$svc:\${IMAGE_TAG:-local}"
  [ "$(q .image)" = "$want_image" ] || { echo "::error file=$compose::image must be $want_image"; exit 1; }
  if docker manifest inspect "$REGISTRY/$svc:$image_sha" >/dev/null 2>&1; then
    echo "$svc: $REGISTRY/$svc:${image_sha:0:7} exists, skip"
    continue
  fi
  echo "$svc: build ${image_sha:0:7} (last change to: ${inputs% })"
  entries="$(jq -c --arg s "$svc" --arg c "$code" --arg i "$image_sha" --arg x "$context" --arg f "$dockerfile" \
    '. + [{service: $s, code: $c, image_sha: $i, context: $x, dockerfile: $f}]' <<<"$entries")"
done

# ---- images that aren't services: built like service images, never deployed themselves ----------
for f in infra/images/*.yaml; do
  [ -e "$f" ] || continue
  name="$(yq -r .image "$f")"; code="$(yq -r .code "$f")"
  context="$(yq -r .context "$f")"; dockerfile="$(yq -r .dockerfile "$f")"
  [ "$(basename "$f" .yaml)" = "$name" ] || { echo "::error file=$f::image must equal the file name"; exit 1; }
  [ -f "$dockerfile" ] || { echo "::error file=$f::no Dockerfile at $dockerfile"; exit 1; }
  declared="$(libs "$f" "$dockerfile")" || exit 1
  inputs="$code $(lib_paths "$declared")"
  image_sha="$(git log -1 --format=%H HEAD -- $inputs)"
  [ -n "$image_sha" ] || { echo "::error file=$f::no commit touches $code"; exit 1; }
  if docker manifest inspect "$REGISTRY/$name:$image_sha" >/dev/null 2>&1; then
    echo "$name (image): $REGISTRY/$name:${image_sha:0:7} exists, skip"
    continue
  fi
  echo "$name (image): build ${image_sha:0:7} (last change to: ${inputs% })"
  entries="$(jq -c --arg s "$name" --arg c "$code" --arg i "$image_sha" --arg x "$context" --arg f "$dockerfile" \
    '. + [{service: $s, code: $c, image_sha: $i, context: $x, dockerfile: $f}]' <<<"$entries")"
done

{
  echo "matrix={\"include\":$entries}"
  echo "any=$([ "$entries" = '[]' ] && echo false || echo true)"
  echo "wave1=$wave1"
  echo "wave2=$wave2"
} >>"$GITHUB_OUTPUT"
