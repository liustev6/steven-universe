# What exists in OpenBao, besides the audit log (declared in ../openbao.hcl) and secret values
# (written with `bao kv put`, never here).

# ---- Secrets engine ---------------------------------------------------------------------------
# KV version 2 at kv/: versioned values, one entry per secret, e.g.
#   kv/services/file-management/rustfs_secret_key   (field "value")
resource "vault_mount" "kv" {
  path        = "kv"
  type        = "kv"
  options     = { version = "2" }
  description = "Managed secrets: kv/services/<service>/<name>, kv/shared/<name>"
}

# ---- Policies -----------------------------------------------------------------------------------
resource "vault_policy" "admin" {
  name   = "admin"
  policy = file("${path.module}/policies/admin.hcl")
}

# Generated from the service manifests (services.tf): read-only, exactly the secrets some
# infra/services/*/deploy.yaml declares. No list, no write.
resource "vault_policy" "deployer" {
  name = "deployer"
  policy = join("\n", concat(
    ["# Generated from infra/services/*/deploy.yaml: every declared secret, read-only."],
    [for p in local.all_secret_paths : "path \"${p}\" { capabilities = [\"read\"] }"],
  ))
}

# ---- Your login ---------------------------------------------------------------------------------
# Username + password, for the CLI, the UI and OpenTofu itself. The user (and its password) is
# created once by hand (README), so the password never enters OpenTofu's state.
resource "vault_auth_backend" "userpass" {
  type        = "userpass"
  path        = "userpass"
  description = "Human admin logins"
}

# ---- Services: log in with an auth-service token ------------------------------------------------
# OpenBao verifies the token against auth-service's public keys (JWKS), like any resource server.
resource "vault_jwt_auth_backend" "auth_service" {
  path               = "auth-service"
  type               = "jwt"
  description        = "Services, with an auth-service access token (aud=secrets-manager)"
  jwks_url           = "${var.auth_service_issuer}/.well-known/jwks.json"
  bound_issuer       = var.auth_service_issuer
  jwt_supported_algs = ["ES256"]
}

# The deployer: a token from auth-service for aud=secrets-manager, sub=service:deployer, gets a
# 10-minute OpenBao token with the read-only "deployer" policy.
resource "vault_jwt_auth_backend_role" "deployer" {
  backend         = vault_jwt_auth_backend.auth_service.path
  role_name       = "deployer"
  role_type       = "jwt"
  bound_audiences = ["secrets-manager"]
  bound_subject   = "service:deployer"
  user_claim      = "sub"
  token_policies  = [vault_policy.deployer.name]
  token_ttl       = 600
  token_max_ttl   = 600
}
