#!/usr/bin/env bash
# Tests for sync.sh with a stand-in curl playing GitHub's token endpoint and the deployer.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/lib.sh"
SYNC_SH="$HERE/../sync.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
cat >"$T/bin/curl" <<SH
#!/usr/bin/env bash
url=""; for a; do case "\$a" in http*) url="\$a";; esac; done
case "\$url" in *audience=*) echo '{"value":"github-token"}'; exit 0 ;; esac
for a; do case "\$a" in '{"sync"'*) echo "\$a" >"$T/body";; esac; done
case "\$SCENARIO" in
  ok) printf '%s\n200' '{"sync":"specs","status":"succeeded","destination":"public/x/specs/","files":3,"uploaded":["a.md"],"unchanged":2,"deleted":["old.md"],"seconds":1.2}' ;;
  failed) printf '%s\n502' '{"sync":"specs","status":"failed","error":"403 forbidden","uploaded":[],"deleted":[]}' ;;
  refused) printf '%s\n404' '{"error":"no infra/syncs/specs.yaml at aaaaaaaaaaaa"}' ;;
  html) printf '%s\n502' '<html>Bad Gateway</html>' ;;
esac
SH
chmod +x "$T/bin/curl"

run() {
  : >"$T/summary"
  env PATH="$T/bin:$PATH" SCENARIO="$1" SYNC=specs GITHUB_SHA=aaaaaaaaaaaa ACTIONS_ID_TOKEN_REQUEST_TOKEN=x \
    ACTIONS_ID_TOKEN_REQUEST_URL='https://gh/token?v=1' GITHUB_STEP_SUMMARY="$T/summary" API_URL=https://api/syncs \
    bash "$SYNC_SH" 2>&1
  echo "exit=$?"
}

o="$(run ok)"
expect_contains "success: passes" "$o" "specs: succeeded"
expect_contains "success: what changed is listed" "$o" "  deleted old.md"
expect_contains "success: run summary" "$(cat "$T/summary")" "1 uploaded, 2 unchanged, 1 deleted"
expect_eq "the request names the sync and the commit, nothing else" "$(cat "$T/body")" '{"sync":"specs","sha":"aaaaaaaaaaaa"}'
o="$(run failed)"
expect_contains "a failed mirror fails the job" "$o" "exit=1"
expect_contains "... with the deployer's reason" "$o" "403 forbidden"
o="$(run refused)"
expect_contains "a refusal fails the job, with the reason" "$o" "no infra/syncs/specs.yaml"
o="$(run html)"
expect_contains "a proxy error page fails the job readably" "$o" "HTTP 502"
finish
