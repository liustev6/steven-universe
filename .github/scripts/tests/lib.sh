# Shared helpers for the CI script tests (bash 3.2 compatible: they also run on macOS).
FAILURES=0
expect_eq() {   # name, got, want
  if [ "$2" = "$3" ]; then echo "PASS  $1"
  else echo "FAIL  $1"; echo "      got:  $2"; echo "      want: $3"; FAILURES=$((FAILURES + 1)); fi
}
expect_contains() {   # name, haystack, needle
  case "$2" in *"$3"*) echo "PASS  $1" ;;
  *) echo "FAIL  $1 (missing: $3)"; printf '%s\n' "$2" | sed 's/^/      | /' | tail -15; FAILURES=$((FAILURES + 1)) ;; esac
}
finish() { echo; [ "$FAILURES" -eq 0 ] && echo "ALL PASSED" || { echo "$FAILURES FAILED"; exit 1; }; }
