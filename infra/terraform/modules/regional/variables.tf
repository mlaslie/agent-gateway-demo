# A second-region instance of what the Private Networking mode needs from the base (docs/TERRAFORM_PLAN.md
# §7c, phase T4): the VPC-enabled egress gateway, its IAP authz extension + REQUEST_AUTHZ policy, the 25
# platform registry entries with their egressor grant, and a staging bucket for that region's engines.
# Not here: service accounts, APIs, the Artifact Registry repo, presenters' grants (all project-wide, in the
# base), the ingress gateway (the mode has no ingress scenario) and Model Armor (the mode doesn't use it).
#
# The resource blocks mirror modules/base (gateways.tf, authz.tf, registry.tf) with the same arguments and
# lifecycle rules; the base itself is left as is so the live us-east4 import addresses (T2) don't move.
# ui/backend/tests/test_private_networking.py checks the two copies stay in step.

variable "project_id" { type = string }
variable "project_number" { type = string }
variable "gateway_project_id" { type = string }
variable "gateway_project_number" { type = string }
variable "organization_id" { type = string }
variable "region" { type = string }
variable "prefix" { type = string }
variable "labels" { type = map(string) }

variable "names" {
  type = object({
    egress_gateway       = string
    egress_iap_extension = string
    egress_iap_policy    = string
    staging_bucket       = string
  })
}

variable "iap_enforcement" { type = string }

variable "platform_endpoints" {
  type = map(object({
    service_id   = string
    display_name = string
  }))
}

variable "platform_endpoints_existing" { type = map(string) }
variable "platform_description" { type = string }
variable "staging_force_destroy" { type = bool }

variable "connectivity_template" {
  description = "agent_connectivity_template for the egress gateway (project NUMBER form). The gateway is CREATED with it (the verified path, P0 T1); bare -> template by PATCH fails (T2 #1)."
  type        = string
}
