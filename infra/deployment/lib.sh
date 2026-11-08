# Output helpers shared by deploy-target.sh and the service hooks (infra/services/*/hooks/).
# Sourced, not executed. Uses $COLOR (1 = colors) and keeps the current step for error messages.

if [ "${COLOR:-0}" = 1 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi

STEP=0; TOTAL=${TOTAL:-0}; STEP_NAME=""
step() { STEP=$((STEP + 1)); STEP_NAME="$*"; printf '\n%sStep %d/%d: %s%s\n' "$YELLOW" "$STEP" "$TOTAL" "$*" "$NC"; }
ok()   { printf '%s✓ %s%s\n' "$GREEN" "$*" "$NC"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '%s! %s%s\n' "$YELLOW" "$*" "$NC"; }
die()  { printf '\n%s✗ %s%s\n' "$RED" "$*" "$NC" >&2; exit 1; }

# Run a noisy command silently; show its output only if it fails.
quiet() {
    local log rc; log="$(mktemp)"
    if "$@" >"$log" 2>&1; then rm -f "$log"; return 0; fi
    rc=$?; cat "$log" >&2; rm -f "$log"; return "$rc"
}

# Any unexpected failure: say which step it was (once), then stop.
trap '[ -n "${FAILED:-}" ] || { FAILED=1; printf "\n%s✗ Deployment failed in step %s/%s (%s). Steps after it did not run.%s\n" "$RED" "$STEP" "$TOTAL" "$STEP_NAME" "$NC" >&2; }' ERR
