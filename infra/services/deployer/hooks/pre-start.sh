# deployer pre-start hook. Sourced by infra/deployment/deploy-target.sh after the image is pulled
# and before the container is (re)started. Available: the lib.sh helpers and $SRC $SVC_DIR $CFG $SHORT.
#
# What only the deployer needs:
#   - /var/lib/deployer: its only writable place (the repository clone, audit.log)
#   - its host-only credentials, owned by the container user
#   - the host allowlist, from infra/services/deployer/hosts.yaml: validated, D13 hosts refused,
#     diff shown, then installed. Only this (hand) deploy ever changes it.

step "deployer: state directory, credentials, host allowlist"

install -d -m 700 -o 10001 -g 10001 /var/lib/deployer
ok "/var/lib/deployer (clone, audit.log)"

for f in "$CFG/credentials/client_secret" "$CFG/credentials/ssh_key"; do
    [ "$(stat -c %u "$f")" = 10001 ] || die "$f must be owned by uid 10001 (the container user)."
done
ok "credentials owned by the container user"

# ---- Host allowlist (infra/services/deployer/hosts.yaml) ------------------------------------------
NEW="$SRC/infra/services/deployer/hosts.yaml"
CHECK="$(python3 - "$NEW" <<'PY'
import re, sys, yaml
hosts = (yaml.safe_load(open(sys.argv[1])) or {}).get("hosts") or {}
never = {"auth-host", "bao-host", "deployer-host"}          # the roots of trust (deployer spec D13)
for name, h in hosts.items():
    if name in never:
        sys.exit(f"{name} must never be in the deployer's allowlist (D13)")
    if not re.match(r"^[A-Za-z0-9.:-]+$", str(h.get("address", ""))):
        sys.exit(f"{name}: invalid address")
    if not re.match(r"^ssh-ed25519 [A-Za-z0-9+/=]+$", str(h.get("host_key", ""))):
        sys.exit(f"{name}: host_key must be a pinned ssh-ed25519 key")
print(" ".join(hosts) or "(none)")
PY
)" || die "hosts.yaml at $SHORT is invalid: $CHECK. The installed allowlist was not changed."
install -m 644 -o 10001 -g 10001 "$NEW" "$CFG/hosts.yaml.new"
if [ -f "$CFG/hosts.yaml" ] && cmp -s "$CFG/hosts.yaml" "$CFG/hosts.yaml.new"; then
    rm -f "$CFG/hosts.yaml.new"
    ok "allowlist unchanged: $CHECK"
else
    if [ -f "$CFG/hosts.yaml" ]; then
        info "allowlist changes (- old, + new):"
        diff -u "$CFG/hosts.yaml" "$CFG/hosts.yaml.new" | tail -n +3 | sed 's/^/    /' || true
    fi
    mv "$CFG/hosts.yaml.new" "$CFG/hosts.yaml"      # atomic: the deployer reads old or new, never half
    ok "allowlist installed: $CHECK"
fi
