#!/usr/bin/env bash
# All CI script tests (run by build.yml's `checks` job; locally: .github/scripts/tests/run.sh).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
rc=0
for t in "$HERE"/test_*.sh; do echo "== $(basename "$t")"; bash "$t" || rc=1; echo; done
exit $rc
