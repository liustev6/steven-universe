#!/usr/bin/env bash
# deploy.sh: deploy one service through api-service -> deployer, and follow it to the result.
# Run by .github/workflows/deploy.yml (one job per service); tested by
# .github/scripts/tests/test_deploy.sh.
#
#   1. the image's digest from GHCR (the host must pull exactly this image)
#   2. GitHub's OIDC token for the deployer (D2): signed by GitHub, bound to this run's repository,
#      branch, environment, workflow and commit (D5); a few minutes, single use
#   3. POST {service, sha, image_digest} -> the deployer's NDJSON stream, one log group per step
#   4. if the stream drops before the result (e.g. this deploy replaces api-service itself), get a
#      fresh token and reconnect: GET .../<id>/events?after=<last seq>, until the result or a deadline
#   5. pass only if the result is `succeeded` (a rollback is a failed deploy)
#
# Environment: SERVICE, IMAGE_SHA, GITHUB_SHA, ACTIONS_ID_TOKEN_REQUEST_TOKEN/_URL (GitHub),
# GITHUB_STEP_SUMMARY; optional API_URL, RECONNECT_SECONDS (900), RECONNECT_INTERVAL (5).
set -euo pipefail

API_URL="${API_URL:-https://api.liustev6.ca/deploy/v1/deployments}"
RECONNECT_SECONDS="${RECONNECT_SECONDS:-900}"
RECONNECT_INTERVAL="${RECONNECT_INTERVAL:-5}"
REPO="liustev6/steven-universe/$SERVICE"
STATE="$(mktemp -d)"            # id, seq, status, open (log group), result.json
echo -1 >"$STATE/seq"

fail() { echo "::error::deploy of $SERVICE: $*"; exit 1; }

# ---- 1. digest ---------------------------------------------------------------------------------
registry_token="$(curl -fsS "https://ghcr.io/token?scope=repository:$REPO:pull" | jq -r .token)"
DIGEST="$(curl -fsSI -H "Authorization: Bearer $registry_token" \
  -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json" \
  "https://ghcr.io/v2/$REPO/manifests/$IMAGE_SHA" | tr -d '\r' | awk -F': ' 'tolower($1) == "docker-content-digest" {print $2}')"
[[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "no image ghcr.io/$REPO:$IMAGE_SHA"
echo "image ghcr.io/$REPO:${IMAGE_SHA:0:7} = $DIGEST"

# ---- 2. GitHub's OIDC token (a fresh one per request: each is single-use) -----------------------
oidc_token() {
  local token
  token="$(curl -fsS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
    "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=steven-universe-deployer" | jq -r .value)"
  echo "::add-mask::$token" >&2
  printf '%s' "$token"
}

# ---- 3. the stream: deployer events (and curl's status line) -> log groups + state files ----------
close_group() { if [ -s "$STATE/open" ]; then echo "::endgroup::"; : >"$STATE/open"; fi; }

handle() {
  local line event text seq
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "") continue ;;
      "__HTTP_STATUS__ "*) echo "${line#__HTTP_STATUS__ }" >"$STATE/status"; continue ;;
    esac
    event="$(jq -r '.event // "error"' <<<"$line" 2>/dev/null || echo raw)"
    seq="$(jq -r '.seq // empty' <<<"$line" 2>/dev/null || true)"
    [ -z "$seq" ] || echo "$seq" >"$STATE/seq"
    case "$event" in
      accepted)
        jq -r .id <<<"$line" >"$STATE/id"
        echo "Deploying $SERVICE ${GITHUB_SHA:0:7} to $(jq -r .host <<<"$line")" ;;
      log)
        text="$(jq -r .line <<<"$line")"
        if [[ "$text" =~ ^Step\ [0-9]+/[0-9]+: ]]; then
          close_group
          echo "::group::$text"; echo 1 >"$STATE/open"
        else
          printf '%s\n' "$text"
        fi ;;
      rollback)
        close_group
        echo "::warning::$SERVICE: deploy failed, rolling back to $(jq -r .to <<<"$line")" ;;
      result)
        close_group
        printf '%s\n' "$line" >"$STATE/result.json" ;;
      step|heartbeat) ;;
      *) printf '%s\n' "$line" ;;   # a refusal from api-service or the deployer, or a proxy error page
    esac
  done
}

request() {   # METHOD URL [BODY]: streams into handle; the HTTP status ends up in $STATE/status
  local token
  token="$(oidc_token)"
  echo 000 >"$STATE/status"
  if [ "$1" = POST ]; then
    curl -sS -N --max-time 1800 -X POST "$2" -H "Authorization: Bearer $token" \
      -H "Content-Type: application/json" -d "$3" -w '\n__HTTP_STATUS__ %{http_code}\n' 2>"$STATE/curl.err" | handle || true
  else
    curl -sS -N --max-time 1800 "$2" -H "Authorization: Bearer $token" \
      -w '\n__HTTP_STATUS__ %{http_code}\n' 2>"$STATE/curl.err" | handle || true
  fi
}

body="$(jq -nc --arg s "$SERVICE" --arg sha "$GITHUB_SHA" --arg d "$DIGEST" '{service: $s, sha: $sha, image_digest: $d}')"
request POST "$API_URL" "$body"

# ---- 4. reconnect until the result -----------------------------------------------------------------
deadline=$((SECONDS + RECONNECT_SECONDS))
while [ ! -s "$STATE/result.json" ]; do
  status="$(cat "$STATE/status")"
  [ -s "$STATE/id" ] || fail "the request failed before the deploy started (HTTP $status)"
  case "$status" in
    401|403|404|409|413|422) fail "reconnecting was refused (HTTP $status)" ;;
  esac
  [ "$SECONDS" -lt "$deadline" ] || fail "no result after ${RECONNECT_SECONDS} s (the deploy may still be running: check the deployer's audit log)"
  close_group
  echo "Stream lost (HTTP $status, $(tr -d '\n' <"$STATE/curl.err" | cut -c1-80)); reconnecting in ${RECONNECT_INTERVAL} s"
  sleep "$RECONNECT_INTERVAL"
  request GET "$API_URL/$(cat "$STATE/id")/events?after=$(cat "$STATE/seq")"
done

# ---- 5. the result -----------------------------------------------------------------------------------
status="$(jq -r '.status // empty' "$STATE/result.json")"
{
  echo "### deploy $SERVICE: $status"
  jq -r '"commit `\(.sha[0:7])`, previous `\(.previous // "none" | .[0:7])`, \(.seconds) s"' "$STATE/result.json" 2>/dev/null || true
} >>"${GITHUB_STEP_SUMMARY:-/dev/null}"
[ "$status" = succeeded ] || fail "$status $(jq -r '.error // empty' "$STATE/result.json")"
echo "$SERVICE: $status"
