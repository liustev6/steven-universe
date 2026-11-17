#!/usr/bin/env bash
# setup.sh: the one command after pve01 boots (and before a planned shutdown). Run it by hand, as
# root on pve01:  ./setup.sh            after a boot: wait, unseal OpenBao, resume agents, check
#                 ./setup.sh shutdown   before hardware work: pause agents, shut pve01 down cleanly
#                 ./setup.sh reboot     the same, but restart (e.g. into a new kernel)
#
# Installed as /root/setup.sh from infra/hosts/pve01/setup.sh (reviewed in git; reinstall it the same
# way when it changes). Spec: docs/specs/boot.md ("What needs a person", "Shutting down for hardware").
#
# The unseal shares are typed here, hidden, and go straight to OpenBao's unseal API in a request body
# on stdin: never on a command line, in shell history, in a file or in a log. Nothing else here is
# secret.
set -uo pipefail

BAO=https://bao.lan.liustev6.ca:8200
STATUS=https://api.lan.liustev6.ca/v1/status        # the LAN address: no dependency on Cloudflare's tunnel
AGENT_CT=107

if [ -t 1 ]; then G='\033[0;32m'; R='\033[0;31m'; Y='\033[0;33m'; B='\033[1m'; N='\033[0m'; else G=; R=; Y=; B=; N=; fi
step() { echo -e "\n${B}== $*${N}"; }
ok()   { echo -e "  ${G}✓${N} $*"; }
warn() { echo -e "  ${Y}!${N} $*"; }
fail() { echo -e "  ${R}✗${N} $*"; }

json() {   # json <python expression on `d`>: reads JSON on stdin
    python3 -c "import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(1)
print($1)"
}

# The kernel pve01 boots next: GRUB's default is the newest installed one (the kernel isn't pinned:
# docs/lxc-gpu-passthrough-setup.md, step 5). Installed kernels are /boot/vmlinuz-*; /lib/modules
# also has folders for kernels whose headers alone are installed.
next_kernel() { ls /boot/vmlinuz-* | sed 's#.*/vmlinuz-##' | sort -V | tail -1; }

# ---- shutdown ---------------------------------------------------------------------------------------
if [ "${1:-}" = "shutdown" ] || [ "${1:-}" = "reboot" ]; then
    ACTION="$1"
    step "Planned $ACTION of pve01"
    K="$(next_kernel)"
    if command -v dkms >/dev/null && ! dkms status -m nvidia -k "$K" 2>/dev/null | grep -q installed; then
        fail "the GPU driver isn't built for $K, the kernel pve01 boots next: the GPU would be gone after the reboot"
        echo "     fix first: docs/lxc-gpu-passthrough-setup.md, \"Updating later\" (or type 'yes' to shut down anyway)"
    else
        ok "the GPU driver is built for $K, the kernel pve01 boots next"
    fi
    read -r -p "  $ACTION pve01 and every guest now? Type 'yes': " answer
    [ "$answer" = yes ] || { echo "  cancelled"; exit 1; }
    if pct exec "$AGENT_CT" -- /usr/local/bin/agent stop >/dev/null 2>&1; then
        ok "agents paused (the kill switch: no session is cut mid-turn; ./setup.sh resumes them)"
    else
        warn "couldn't pause agents (agent-service down?); shutting down anyway"
    fi
    ok "$ACTION: Proxmox stops the guests in reverse boot order"
    if [ "$ACTION" = reboot ]; then exec reboot; else exec shutdown -h now; fi
fi
[ -z "${1:-}" ] || { echo "usage: $0 [shutdown|reboot]"; exit 2; }

# ---- 1. guests ---------------------------------------------------------------------------------------
step "1/5 Guests (they start by themselves, in boot order; VM 200 last)"
for i in $(seq 1 90); do
    stopped="$(pct list | awk 'NR>1 && $2!="running"{print "CT " $1}'; qm list | awk 'NR>1 && $3!="running"{print "VM " $1}')"
    [ -z "$stopped" ] && break
    [ "$i" = 1 ] && echo "  waiting for: $(echo $stopped)"
    sleep 5
done
if [ -n "$stopped" ]; then fail "still not running after 7.5 min: $(echo $stopped)"; else ok "every container and VM 200 running"; fi

# ---- GPU ----------------------------------------------------------------------------------------------
step "GPU (NVIDIA's driver, for the voice containers)"
if gpu="$(nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv,noheader 2>/dev/null)"; then
    ok "$gpu (kernel $(uname -r))"
else
    fail "nvidia-smi doesn't answer: is the module built for $(uname -r)? (dkms status; docs/lxc-gpu-passthrough-setup.md)"
fi

# ---- 2. OpenBao --------------------------------------------------------------------------------------
step "2/5 OpenBao (manual by design: its key lives only in memory)"
seal() { curl -s --max-time 5 "$BAO/v1/sys/seal-status"; }
for i in $(seq 1 60); do seal | json 'd["sealed"]' >/dev/null && break; sleep 3; done
if ! seal | json 'd["sealed"]' >/dev/null; then
    fail "OpenBao doesn't answer at $BAO (CT 104?); skipping the unseal"
elif [ "$(seal | json 'd["sealed"]')" = False ]; then
    ok "already unsealed"
else
    need="$(seal | json 'd["t"]')"
    echo "  sealed: $need shares needed (typing is hidden; Ctrl-C to skip)"
    while [ "$(seal | json 'd["sealed"]')" = True ]; do
        read -rs -p "  share $(( $(seal | json 'd["progress"]') + 1 )) of $need: " share; echo
        [ -n "$share" ] || continue
        reply="$(python3 -c 'import json,sys; print(json.dumps({"key": sys.stdin.read().strip()}))' <<<"$share" \
                 | curl -s --max-time 10 -X PUT --data @- "$BAO/v1/sys/unseal")"
        unset share
        if ! printf '%s' "$reply" | json 'd["sealed"]' >/dev/null; then
            fail "refused: $(printf '%s' "$reply" | json '"; ".join(d.get("errors", ["?"]))' 2>/dev/null || echo "no answer")"
        fi
    done
    ok "unsealed"
fi

# ---- 3. agents ---------------------------------------------------------------------------------------
step "3/5 Agents"
page() { curl -s --max-time 15 "$STATUS"; }
for i in $(seq 1 30); do page | json 'd["services"]["agent-service"]["details"]["stopped"]' >/dev/null && break; sleep 5; done
case "$(page | json 'd["services"]["agent-service"]["details"]["stopped"]' 2>/dev/null)" in
    True)  if pct exec "$AGENT_CT" -- /usr/local/bin/agent start >/dev/null 2>&1; then
               ok "resumed (the kill switch was on)"
               sleep 6                                  # the status page caches for 5 s: let it see the change
           else fail "couldn't resume: run 'agent start' on CT $AGENT_CT"; fi ;;
    False) ok "running (the kill switch is off)" ;;
    *)     warn "agent-service doesn't answer yet; check its line below" ;;
esac

# ---- 4. everything -----------------------------------------------------------------------------------
step "4/5 Every service (the lab's status page)"
SUMMARY="$(cat <<'PY'
import json, sys
G, R, N = ("\033[0;32m", "\033[0;31m", "\033[0m") if sys.stdout.isatty() else ("", "", "")
try:
    p = json.load(sys.stdin)
except Exception:
    print(f"  {R}✗{N} the status page does not answer (api-service, CT 106)")
    sys.exit(1)
for name, s in sorted(p["services"].items()):
    d = s.get("details") or {}
    note = s.get("error") or ""
    if name == "openbao" and d.get("sealed"):
        note = "sealed"
    if name == "sandbox-runner":
        warm = sum(1 for x in d.get("pods", []) if x["state"] == "warm")
        note = f"{warm} warm pod(s)"
    if name == "agent-service" and d.get("login"):
        login = d["login"]
        note = f"login: signed in={login['signed_in']}, saved to OpenBao={login['saved_to_openbao']}"
        if d.get("stopped"):
            note = "stopped (the kill switch is on); " + note
    mark = f"{G}✓" if s["ok"] else f"{R}✗"
    print(f"  {mark}{N} {name:<16} {note}")
print("  everything ok" if p["ok"] else "  NOT everything is ok: see above")
PY
)"
page | python3 -c "$SUMMARY"

# ---- 5. the login's copy in OpenBao ------------------------------------------------------------------
step "5/5 agent-service's login, saved to OpenBao again (it catches up within 10 minutes of the unseal)"
for i in $(seq 1 67); do
    saved="$(page | json 'd["services"]["agent-service"]["details"]["login"]["saved_to_openbao"]' 2>/dev/null)"
    [ "$saved" = True ] && break
    [ "$i" = 1 ] && echo "  waiting (Ctrl-C is fine: it catches up on its own)…"
    sleep 10
done
if [ "$saved" = True ]; then ok "saved"; else warn "not yet: check later with: curl -s $STATUS"; fi
echo -e "\n${B}Done.${N} Anything not ok above: docs/specs/boot.md, \"Things that can delay recovery\"."
