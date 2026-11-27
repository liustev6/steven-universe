#!/usr/bin/env bash
# sync.sh: one sync (deployer D21) through api-service -> deployer: a folder of the repository at this
# commit mirrored into file-management (infra/syncs/$SYNC.yaml). Run by .github/workflows/deploy.yml
# (job `sync`); tested by .github/scripts/tests/test_sync.sh.
#
#   1. GitHub's OIDC token for the deployer (D2), bound to this run's repository, branch,
#      environment, workflow and commit (D5); single use
#   2. POST {sync, sha}: the deployer reads the manifest and the files at that commit from its own
#      clone (never from this request), and answers with the result
#   3. pass only if the result is `succeeded`
#
# Environment: SYNC, GITHUB_SHA, ACTIONS_ID_TOKEN_REQUEST_TOKEN/_URL (GitHub), GITHUB_STEP_SUMMARY;
# optional API_URL.
set -euo pipefail

API_URL="${API_URL:-https://api.liustev6.ca/deploy/v1/syncs}"
fail() { echo "::error::sync $SYNC: $*"; exit 1; }

token="$(curl -fsS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=steven-universe-deployer" | jq -r .value)"
echo "::add-mask::$token"

body="$(jq -nc --arg s "$SYNC" --arg sha "$GITHUB_SHA" '{sync: $s, sha: $sha}')"
out="$(curl -sS --max-time 600 -X POST "$API_URL" -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" -d "$body" -w '\n%{http_code}' || echo $'\n000')"
status="${out##*$'\n'}"
result="${out%$'\n'*}"

if ! jq -e . >/dev/null 2>&1 <<<"$result"; then
  fail "HTTP $status: $(head -c 300 <<<"$result")"
fi
outcome="$(jq -r '.status // empty' <<<"$result")"
jq -r '"\(.destination // ""): \(.files // 0) files, \(.uploaded // [] | length) uploaded, \(.unchanged // 0) unchanged, \(.deleted // [] | length) deleted"' <<<"$result"
jq -r '(.uploaded // [] | map("  uploaded " + .)), (.deleted // [] | map("  deleted " + .)) | .[]' <<<"$result"
{
  echo "### sync $SYNC: ${outcome:-refused (HTTP $status)}"
  jq -r '"`\(.destination // "")`: \(.uploaded // [] | length) uploaded, \(.unchanged // 0) unchanged, \(.deleted // [] | length) deleted (\(.seconds // 0) s)"' <<<"$result" 2>/dev/null || true
} >>"${GITHUB_STEP_SUMMARY:-/dev/null}"
[ "$status" = 200 ] && [ "$outcome" = succeeded ] || fail "${outcome:-HTTP $status} $(jq -r '.error // empty' <<<"$result")"
echo "$SYNC: succeeded"
