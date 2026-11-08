# deployer: read every service's managed secrets at deploy time, nothing else (no list, no write).
path "kv/data/services/*" { capabilities = ["read"] }
path "kv/data/shared/*"   { capabilities = ["read"] }
