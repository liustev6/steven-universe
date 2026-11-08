# OpenBao server config for the secrets-manager (CT 104). Spec: docs/specs/secrets-manager.md.
# Mounted read-only into the container at /openbao/config/openbao.hcl.

ui            = true
log_level     = "info"
api_addr      = "https://bao.lan.liustev6.ca:8200"
cluster_addr  = "https://bao.lan.liustev6.ca:8201"   # required by Raft even with one node

# mlock needs IPC_LOCK, which the container doesn't get (no capabilities). Recommended with Raft
# anyway; CT 104 has only 256 MB of swap, the one place memory could reach disk.
disable_mlock = true

# Integrated storage, one node. Encrypted at rest with the key that unsealing unlocks.
storage "raft" {
  path    = "/openbao/data"
  node_id = "bao-1"
}

# HTTPS only, with the Let's Encrypt certificate for bao.lan.liustev6.ca (copied by cert-hook.sh).
# tls_auto_reload picks up renewed certificates without a restart (a restart would seal OpenBao).
listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_cert_file   = "/openbao/tls/cert.pem"
  tls_key_file    = "/openbao/tls/key.pem"
  tls_min_version = "tls12"
  tls_auto_reload = true
}

# Audit log: every request, with secret values HMAC'd (never in plain text). Declared here because
# OpenBao doesn't allow creating audit devices through the API by default
# (unsafe_allow_api_audit_creation = false), so OpenTofu doesn't manage it.
audit "file" "file" {
  description = "Every request and response, values HMAC'd"
  options {
    file_path = "/openbao/logs/audit.log"
  }
}
