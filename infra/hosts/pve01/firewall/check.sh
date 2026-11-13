#!/usr/bin/env bash
# Checks VM 200's Proxmox firewall (sandbox-runner R5, second wall), from the Mac. Each probe is a TCP
# connect (3 s timeout) from one host to another. Run before and after applying 200.fw: before, the
# "closed" rows are open; after, every row must PASS.
set -uo pipefail
VM=192.168.50.95
FAILURES=0
probe() {   # from-host, target, port -> open | closed
  ssh -o BatchMode=yes "$1" "python3 -c 'import socket,sys; s=socket.socket(); s.settimeout(3); print(\"open\" if s.connect_ex((sys.argv[1], int(sys.argv[2]))) == 0 else \"closed\")' $2 $3" 2>/dev/null || echo "error"
}
check() {   # description, want, from-host, target, port
  got="$(probe "$3" "$4" "$5")"
  if [ "$got" = "$2" ]; then echo "PASS  $1: $got"; else echo "FAIL  $1: $got (want $2)"; FAILURES=$((FAILURES + 1)); fi
}
echo "== out of VM 200 (as root there): only DNS, auth-service and the internet"
check "VM -> pve01 web UI (8006)"            closed sandbox-host 192.168.50.101 8006
check "VM -> pve01 SSH (22)"                 closed sandbox-host 192.168.50.101 22
check "VM -> pve01 over Tailscale (22)"      closed sandbox-host 100.84.191.13 22
check "VM -> router admin (443)"             closed sandbox-host 192.168.50.1 443
check "VM -> router admin (80)"              closed sandbox-host 192.168.50.1 80
check "VM -> OpenBao (8200)"                 closed sandbox-host 192.168.50.206 8200
check "VM -> file-management (443)"          closed sandbox-host 192.168.50.66 443
check "VM -> RustFS (9000)"                  closed sandbox-host 192.168.50.123 9000
check "VM -> api-service (443)"              closed sandbox-host 192.168.50.192 443
check "VM -> deployer (443)"                 closed sandbox-host 192.168.50.50 443
check "VM -> deployer SSH (22)"              closed sandbox-host 192.168.50.50 22
check "VM -> auth-service (443)"             open   sandbox-host 192.168.50.235 443
check "VM -> router DNS (53)"                open   sandbox-host 192.168.50.1 53
check "VM -> GHCR (443, internet)"           open   sandbox-host ghcr.io 443
check "VM -> Debian (80, internet)"          open   sandbox-host deb.debian.org 80
echo "== into VM 200: pve01, the deployer (SSH), api-service (the runner's API only)"
check "pve01 -> VM SSH (22)"                 open   pve01 $VM 22
check "pve01 -> VM runner API (443)"         open   pve01 $VM 443
check "deployer -> VM SSH (22)"              open   deployer-host $VM 22
check "file-management host -> VM SSH (22)"  closed files-host $VM 22
check "file-management host -> VM API (443)" closed files-host $VM 443
check "api-service host -> VM API (443)"     open   api-host $VM 443          # the status page (S.1)
check "api-service host -> VM SSH (22)"      closed api-host $VM 22
echo
[ "$FAILURES" -eq 0 ] && echo "ALL PASSED" || { echo "$FAILURES FAILED"; exit 1; }
