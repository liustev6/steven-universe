variable "address" {
  description = "OpenBao address (reachable from the Mac through the Tailscale subnet route)."
  type        = string
  default     = "https://bao.lan.liustev6.ca:8200"
}

variable "auth_service_issuer" {
  description = "auth-service's issuer; OpenBao trusts tokens signed by the keys it publishes."
  type        = string
  default     = "https://auth.lan.liustev6.ca"
}

variable "admin_user" {
  description = "Your userpass login (its password is set once by hand, never by OpenTofu)."
  type        = string
  default     = "steven"
}

variable "state_passphrase" {
  description = "Encrypts the OpenTofu state. Set TF_VAR_state_passphrase; keep it in your password manager."
  type        = string
  sensitive   = true
}
