# auth-service pre-start hook. Sourced by infra/deployment/deploy-target.sh after the image is
# built and before the container is (re)started. Available: the lib.sh helpers (step/ok/info/die/
# quiet) and $SRC $SVC_DIR $CFG $SHA $SHORT $IMAGE.
#
# What only auth-service needs:
#   - its config folders and the admin CLI on the host
#   - the policy: validated with the new image's own checker, diff shown, then installed
#     (a running auth-service reloads it by itself; an invalid policy changes nothing)
#   - the first signing key, on the very first deploy only (later rotations are deliberate,
#     by hand: new-key, wait for JWKS caches, promote-key)

step "auth-service: admin CLI, policy, signing key"

install -d -m 700 -o 10001 -g 10001 "$CFG/keys" "$CFG/clients"
install -m 755 "$SVC_DIR/bin/auth-service-admin" /usr/local/bin/auth-service-admin
ok "auth-service-admin -> /usr/local/bin"

# ---- Policy (infra/services/auth-service/policy.yaml) ------------------------------------------
install -m 600 -o 10001 -g 10001 "$SRC/infra/services/auth-service/policy.yaml" "$CFG/policy.yaml.new"
if ! CHECK="$(auth-service-admin check-policy --file "$CFG/policy.yaml.new" 2>&1)"; then
    rm -f "$CFG/policy.yaml.new"
    printf '%s\n' "$CHECK" | grep -v ' Container ' >&2 || true
    die "policy.yaml at $SHORT is invalid; the live policy was not changed."
fi
ok "policy valid $(printf '%s\n' "$CHECK" | grep -o '([0-9]* audiences, [0-9]* clients)' || true)"
if [ -f "$CFG/policy.yaml" ] && cmp -s "$CFG/policy.yaml" "$CFG/policy.yaml.new"; then
    rm -f "$CFG/policy.yaml.new"
    ok "policy unchanged"
elif [ -f "$CFG/policy.yaml" ]; then
    info "policy changes (- old, + new):"
    diff -u "$CFG/policy.yaml" "$CFG/policy.yaml.new" | tail -n +3 | sed 's/^/    /' || true
    mv "$CFG/policy.yaml.new" "$CFG/policy.yaml"   # atomic: readers see old or new, never half
    ok "policy updated"
else
    mv "$CFG/policy.yaml.new" "$CFG/policy.yaml"
    ok "policy installed (first deploy)"
fi

# ---- Signing key ----------------------------------------------------------------------------------
if [ -s "$CFG/keys/active" ]; then
    ok "signing key $(cat "$CFG/keys/active") (unchanged)"
else
    KID="$(auth-service-admin new-key 2>/dev/null | awk '{print $2}')"
    quiet auth-service-admin promote-key "$KID"
    ok "created and activated the first signing key $KID"
fi
