# OpenTofu configuration for the secrets-manager (OpenBao). Spec: docs/specs/secrets-manager.md (S10–S12).
#
# Manages configuration only (secrets engine, auth methods, roles, policies). Secret VALUES are
# never managed here: anything OpenTofu manages ends up in its state. Write values with
# `bao kv put kv/services/<svc>/<name> value=-`.

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    vault = {
      # HashiCorp's Vault provider; OpenBao speaks the same API.
      source  = "hashicorp/vault"
      version = "~> 5.0"
    }
  }

  # State and saved plans are encrypted (OpenTofu state encryption). The passphrase comes from
  # TF_VAR_state_passphrase and lives in your password manager; without it the state can't be read.
  # `enforced = true` refuses to ever write an unencrypted state or plan.
  encryption {
    key_provider "pbkdf2" "passphrase" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "default" {
      keys = key_provider.pbkdf2.passphrase
    }
    state {
      method   = method.aes_gcm.default
      enforced = true
    }
    plan {
      method   = method.aes_gcm.default
      enforced = true
    }
  }
}

provider "vault" {
  address = var.address
  # The token comes from VAULT_TOKEN (README: the root token for the first apply, then your admin
  # login). Use it directly instead of minting a child token per run.
  skip_child_token = true
}
