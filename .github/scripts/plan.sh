#!/usr/bin/env bash
# plan.sh: what this run builds and deploys. Run by .github/workflows/build.yml (job `plan`) from the
# root of the checked-out commit; tested by .github/scripts/tests/test_plan.sh.
#
# Builds (deployer spec D1a): for every infra/services/*/deploy.yaml, the image is named by the
# commit that last changed its inputs: its `code` folder and the shared libraries its Dockerfile
# copies in (`COPY libs/<name> …`, read from the Dockerfile itself). It is built only if
# $REGISTRY/<service>:<that sha> doesn't exist yet, so a config-only commit builds nothing and a
# re-run is always safe. Build settings (context, Dockerfile, image name) come from the service's
# compose file: one place for them (D12).
#
# Deploys (D16, D19): only `pipeline: true` services. On a push: those whose image inputs or
# infra/services/<svc>/ changed since the previous push. On workflow_dispatch: the one named in
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

for m in infra/services/*/deploy.yaml; do
  svc="$(yq -r '.service' "$m")"
  code="$(yq -r '.code // ""' "$m")"
  compose="$(yq -r '.compose' "$m")"
  [ -n "$code" ] || { echo "::error file=$m::no 'code' folder (needed for the image sha, D1a)"; exit 1; }
  q() { yq -r "explode(.) | .services[\"$svc\"] | $1" "$compose"; }
  context="$(realpath -m --relative-to=. "$(dirname "$compose")/$(q .build.context)")"
  dockerfile="$context/$(q .build.dockerfile)"
  [ -f "$dockerfile" ] || { echo "::error file=$compose::no Dockerfile at $dockerfile"; exit 1; }

  # Inputs (D1a): the code, plus each shared library the Dockerfile copies (a source `libs/<name>`
  # of a COPY line, relative to the build context). A library no service copies changes no image.
  prefix="$([ "$context" = . ] || echo "$context/")"
  libs="$(sed -nE 's/^[[:space:]]*COPY[[:space:]]+//p' "$dockerfile" | tr -s ' \t' '\n' \
    | sed -nE "s|^(libs/[A-Za-z0-9._-]+)/?$|$prefix\1|p" | sort -u | tr '\n' ' ')"
  inputs="$code $libs"                       # repository paths, no spaces
  image_sha="$(git log -1 --format=%H HEAD -- $inputs)"
  [ -n "$image_sha" ] || { echo "::error file=$m::no commit touches $code"; exit 1; }
  watched="$(printf '%s|' $inputs "infra/services/$svc" | sed 's/|$//; s/\./\\./g')"

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

{
  echo "matrix={\"include\":$entries}"
  echo "any=$([ "$entries" = '[]' ] && echo false || echo true)"
  echo "wave1=$wave1"
  echo "wave2=$wave2"
} >>"$GITHUB_OUTPUT"
