# Policies generated from the service manifests (infra/services/<service>/deploy.yaml).
#
# Each manifest lists the secrets its deploy needs (`secrets[].from`, a path under kv/). From those
# declarations OpenTofu creates:
#   - "service-<name>": read-only, exactly that service's declared secrets. Shows who needs what;
#     used when services fetch their own secrets later (secrets-manager phase 3).
#   - "deployer": read-only, exactly the secrets some manifest declares (the union). Replaces the
#     hand-written "kv/data/services/*": a secret no manifest declares can't be read by deploys.
#
# Adding a secret to a manifest changes these policies on the next `tofu plan`; the value itself is
# still written by hand with `bao kv put` (never by OpenTofu).

locals {
  services_dir = "${path.module}/../../services"

  # { "file-management" = <parsed deploy.yaml>, ... } keyed by folder name
  manifests = {
    for f in fileset(local.services_dir, "*/deploy.yaml") :
    dirname(f) => yamldecode(file("${local.services_dir}/${f}"))
  }

  # { "file-management" = ["kv/data/services/file-management/rustfs_access_key", ...], ... }
  secret_paths = {
    for name, m in local.manifests :
    name => sort(distinct([for s in try(m.secrets, []) : "kv/data/${s.from}"]))
  }

  all_secret_paths = sort(distinct(flatten(values(local.secret_paths))))
}

# A manifest's `service` must match its folder, and every secret path must stay under kv/services/
# or kv/shared/ (the layout in docs/specs/secrets-manager.md).
check "manifests" {
  assert {
    condition     = alltrue([for name, m in local.manifests : m.service == name])
    error_message = "infra/services/<folder>/deploy.yaml: `service` must equal the folder name."
  }
  assert {
    condition     = alltrue([for p in local.all_secret_paths : can(regex("^kv/data/(services/[a-z0-9-]+|shared)/[a-z0-9_]+$", p))])
    error_message = "secrets[].from must be services/<service>/<name> or shared/<name> (lowercase, digits, _ and -)."
  }
}

resource "vault_policy" "service" {
  for_each = local.secret_paths

  name = "service-${each.key}"
  policy = join("\n", concat(
    ["# Generated from infra/services/${each.key}/deploy.yaml: this service's declared secrets."],
    [for p in each.value : "path \"${p}\" { capabilities = [\"read\"] }"],
  ))
}
