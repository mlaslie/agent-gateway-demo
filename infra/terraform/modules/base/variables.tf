# See the root variables.tf for descriptions; the root resolves project numbers and the organization.

variable "project_id" { type = string }
variable "project_number" { type = string }
variable "gateway_project_id" { type = string }
variable "gateway_project_number" { type = string }
variable "organization_id" { type = string }
variable "region" { type = string }
variable "prefix" { type = string }
variable "labels" { type = map(string) }
variable "required_apis" { type = list(string) }

variable "names" {
  type = object({
    ui_sa                = string
    targets_sa           = string
    artifact_repo        = string
    staging_bucket       = string
    egress_gateway       = string
    ingress_gateway      = string
    egress_iap_extension = string
    egress_iap_policy    = string
    model_armor_template = string
    model_armor_ext      = string
  })
}

variable "iap_enforcement" { type = string }
variable "model_armor_filters" { type = list(string) }
variable "model_armor_block_marker" { type = string }
variable "model_armor_static_agents" { type = list(string) }
variable "presenters" { type = list(string) }
variable "shared_project_grants" { type = bool }

variable "platform_endpoints" {
  type = map(object({
    service_id   = string
    display_name = string
  }))
}

variable "platform_endpoints_existing" { type = map(string) }
variable "platform_description" { type = string }
variable "recordings_bucket" { type = string }
variable "staging_force_destroy" { type = bool }
