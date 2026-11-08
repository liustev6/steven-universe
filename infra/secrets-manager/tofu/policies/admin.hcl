# admin: you. Manage secrets, policies, auth methods and mounts (what OpenTofu applies), take
# Raft snapshots for backups. Not root: no unseal, rekey or root-token generation (those need shares).

# Secrets (values and their versions/metadata)
path "kv/*" { capabilities = ["create", "read", "update", "delete", "list"] }

# What OpenTofu manages
path "sys/policies/acl/*" { capabilities = ["create", "read", "update", "delete", "list"] }
path "sys/policies/acl"   { capabilities = ["list"] }
path "sys/mounts"         { capabilities = ["read"] }
path "sys/mounts/*"       { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "sys/auth"           { capabilities = ["read"] }
path "sys/auth/*"         { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }
path "auth/*"             { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }

# Seeing the audit configuration (devices themselves are declared in openbao.hcl)
path "sys/audit" { capabilities = ["read", "sudo"] }

# Backups
path "sys/storage/raft/snapshot" { capabilities = ["read"] }

# Your own token
path "auth/token/lookup-self" { capabilities = ["read"] }
path "auth/token/renew-self"  { capabilities = ["update"] }
path "auth/token/revoke-self" { capabilities = ["update"] }

# Revoking other tokens (e.g. the initial root token after setup)
path "auth/token/revoke" { capabilities = ["update"] }
