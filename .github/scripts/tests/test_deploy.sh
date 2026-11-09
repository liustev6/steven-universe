#!/usr/bin/env bash
# Tests for deploy.sh with a stand-in curl playing GHCR, GitHub's token endpoint and the deployer.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/lib.sh"
DEPLOY="$HERE/../deploy.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
DIGEST="sha256:$(printf 'f%.0s' $(seq 64))"
mkdir -p "$T/bin"
cat >"$T/bin/curl" <<SH
#!/usr/bin/env bash
url=""; for a; do case "\$a" in http*) url="\$a";; esac; done
case "\$url" in
  *ghcr.io/token*) echo '{"token":"t"}'; exit 0 ;;
  *ghcr.io/v2/*) printf 'HTTP/2 200\r\ndocker-content-digest: $DIGEST\r\n\r\n'; exit 0 ;;
  *audience=*) echo '{"value":"github-token-\$RANDOM"}'; exit 0 ;;
esac
echo "\$url" >>"$T/requests"
n=\$(wc -l <"$T/requests" | tr -d ' ')
ev() { printf '%s\n' "\$@"; }
end() { printf '\n__HTTP_STATUS__ %s\n' "\$1"; exit "\${2:-0}"; }
case "\$SCENARIO:\$n" in
  ok:1) ev '{"seq":0,"event":"accepted","id":"abc","host":"files-host"}' '{"seq":1,"event":"log","line":"Step 1/7: Commit"}' \
           '{"event":"heartbeat"}' '{"seq":2,"event":"log","line":"ok"}' \
           '{"seq":3,"event":"result","status":"succeeded","sha":"aaaaaaaaaa","previous":"bbbbbbbbbb","seconds":9}'; end 200 ;;
  rollback:1) ev '{"seq":0,"event":"accepted","id":"abc","host":"h"}' '{"seq":1,"event":"rollback","to":"bbbbbbbbbb"}' \
           '{"seq":2,"event":"result","status":"rolled_back","sha":"aaaaaaaaaa","seconds":70}'; end 200 ;;
  refused:1) ev '{"error":"environment is dev"}'; end 403 ;;
  drop*:1) ev '{"seq":0,"event":"accepted","id":"abc","host":"h"}' '{"seq":1,"event":"log","line":"Step 1/7: Commit"}'; end 200 18 ;;
  dropok:2) ev '<html>502 Bad Gateway</html>'; end 502 ;;
  dropok:3) ev '{"seq":2,"event":"log","line":"Step 2/7: Config"}' '{"seq":3,"event":"result","status":"succeeded","sha":"aaaaaaaaaa","seconds":30}'; end 200 ;;
  drop404:2) ev '{"error":"unknown deployment"}'; end 404 ;;
  dropforever:*) end 000 7 ;;
esac
SH
chmod +x "$T/bin/curl"

run() {   # scenario -> output, exit code in $T/code
  : >"$T/requests"; : >"$T/summary"
  env PATH="$T/bin:$PATH" SCENARIO="$1" SERVICE=file-management IMAGE_SHA=abc1234 GITHUB_SHA=aaaaaaaaaaaa \
    ACTIONS_ID_TOKEN_REQUEST_TOKEN=x ACTIONS_ID_TOKEN_REQUEST_URL='https://gh/token?v=1' GITHUB_STEP_SUMMARY="$T/summary" \
    API_URL=https://api/deploy RECONNECT_SECONDS=3 RECONNECT_INTERVAL=1 bash "$DEPLOY" 2>&1
  echo "exit=$?"
}

o="$(run ok)"
expect_contains "success: passes" "$o" "file-management: succeeded"
expect_contains "success: steps become log groups" "$o" "::group::Step 1/7: Commit"
expect_contains "success: the digest is pinned" "$o" "= $DIGEST"
expect_contains "success: run summary" "$(cat "$T/summary")" "previous \`bbbbbbb\`"
o="$(run rollback)"
expect_contains "rollback: warns" "$o" "::warning::file-management: deploy failed, rolling back to bbbbbbbbbb"
expect_contains "rollback: fails the job" "$o" "exit=1"
o="$(run refused)"
expect_contains "refused: prints the reason" "$o" '{"error":"environment is dev"}'
expect_contains "refused: never reconnects" "$o$(wc -l <"$T/requests")" "before the deploy started (HTTP 403)"
o="$(run dropok)"
expect_contains "dropped stream: reconnects" "$o" "Stream lost"
expect_contains "dropped stream: passes after reconnecting" "$o" "file-management: succeeded"
expect_eq "dropped stream: resumes after the last event it saw, retrying through a 502" \
  "$(sed -n '2,3p' "$T/requests" | tr '\n' ' ')" "https://api/deploy/abc/events?after=1 https://api/deploy/abc/events?after=1 "
o="$(run drop404)"
expect_contains "reconnect refused (404): fails" "$o" "reconnecting was refused (HTTP 404)"
o="$(run dropforever)"
expect_contains "never comes back: fails at the deadline" "$o" "no result after 3 s"
finish
