# Values come from config/demo.yaml through `./agdemo tf vars`, which writes generated.auto.tfvars.json
# (git-ignored). Terraform never reads the YAML itself.

variable "project_id" {
  description = "Project for the Runtime agents, ingress gateway, Model Armor, service accounts, repo and buckets."
  type        = string
}

variable "gateway_project_id" {
  description = "Project for the egress gateway and Agent Registry. Empty = project_id."
  type        = string
  default     = ""
}

variable "region" {
  description = "Region of the gateways, Agent Registry, Model Armor, Artifact Registry and the staging bucket."
  type        = string
}

variable "prefix" {
  description = "resource_prefix from demo.yaml (lowercase, <= 10 chars)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,9}$", var.prefix))
    error_message = "prefix must match ^[a-z][a-z0-9-]{0,9}$."
  }
}

variable "labels" {
  description = "Labels for every labelled resource (environment.labels + demo-prefix)."
  type        = map(string)
}

variable "organization_id" {
  description = "Organization id for the Agent Identity principalSet. Empty = data.google_project.org_id; a project with no organization uses the proj-<PROJECT_NUMBER> trust domain."
  type        = string
  default     = ""
}

variable "required_apis" {
  description = "Services to enable (agdemo_core.gcp.project.REQUIRED_APIS)."
  type        = list(string)
}

variable "names" {
  description = "Resource names, computed by agdemo_core (same helpers bootstrap uses)."
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

variable "iap_enforcement" {
  description = "Initial IAP enforcement (ENFORCE | DRY_RUN). Later changes belong to Policy Studio (ignore_changes)."
  type        = string
  default     = "ENFORCE"

  validation {
    condition     = contains(["ENFORCE", "DRY_RUN"], var.iap_enforcement)
    error_message = "iap_enforcement must be ENFORCE or DRY_RUN."
  }
}

variable "model_armor_filters" {
  description = "model_armor.filters from demo.yaml."
  type        = list(string)
}

variable "model_armor_block_marker" {
  description = "Text in the custom block message (agdemo_core.gcp.model_armor.BLOCK_MESSAGE_MARKER)."
  type        = string
}

variable "model_armor_static_agents" {
  description = "Project service agents (by gcp-sa-* suffix) that get the Model Armor callout roles."
  type        = list(string)
  default     = ["gcp-sa-dep", "gcp-sa-aiplatform-re"]
}

variable "shared_project_grants" {
  description = "Manage project-wide grants shared with other environments in the project (presenters, static Model Armor service agents). false for a second environment, so its destroy can't remove them."
  type        = bool
  default     = true
}

variable "presenters" {
  description = "ui.admin_access members (user:/group:/serviceAccount:) that get roles/aiplatform.user."
  type        = list(string)
  default     = []
}

variable "platform_endpoints" {
  description = "url => {service_id, display_name}: Google APIs registered as Agent Registry endpoints."
  type = map(object({
    service_id   = string
    display_name = string
  }))
}

variable "platform_endpoints_existing" {
  description = "url => registry_resource for URLs already registered by a service we don't own (granted, not created)."
  type        = map(string)
  default     = {}
}

variable "platform_description" {
  description = "Description on the platform endpoint entries."
  type        = string
  default     = "agdemo platform endpoint (default-deny allowlist)"
}

variable "recordings_bucket" {
  description = "Recordings bucket created by the CLI (survives `down`; never in Terraform state). Empty = none."
  type        = string
  default     = ""
}

variable "staging_force_destroy" {
  description = "Let `down` delete the staging bucket with the engine pickles still in it."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------------------------------
# Private Networking mode (phase T4, docs/TERRAFORM_PLAN.md §7c): a VPC-enabled egress gateway in a second
# region (modules/private_egress + modules/regional). `./agdemo tf vars` fills it from demo.yaml
# private_networking; names and the region's platform endpoints are computed in Python (agdemo_core).
# Disabled = no resource and no change to the base plan.
# ---------------------------------------------------------------------------------------------------
variable "private_networking" {
  description = "Private Networking mode (second region): VPC, attachment, PSC endpoint, run.app zone, template, VPC-enabled gateway."
  type = object({
    enabled                    = bool
    region                     = optional(string, "us-west1")
    subnet_range               = optional(string, "10.70.0.0/28")
    psc_ip                     = optional(string, "10.70.250.10")
    dns_domains                = optional(list(string), ["run.app."])
    vpc_egress                 = optional(string, "PRIVATE_RANGES_ONLY")
    manage_service_agent_roles = optional(bool, true)
    names = optional(object({
      network              = string
      subnet               = string
      attachment           = string
      psc_address          = string
      psc_forwarding_rule  = string
      dns_zone             = string
      template_prefix      = string
      egress_gateway       = string
      egress_iap_extension = string
      egress_iap_policy    = string
      staging_bucket       = string
    }))
    platform_endpoints = optional(map(object({
      service_id   = string
      display_name = string
    })), {})
    platform_endpoints_existing = optional(map(string), {})
  })
  default = { enabled = false }

  validation {
    condition     = !var.private_networking.enabled || var.private_networking.names != null
    error_message = "private_networking.names is required when enabled (./agdemo tf vars writes it)."
  }

  validation {
    condition     = !var.private_networking.enabled || length(var.private_networking.platform_endpoints) > 0
    error_message = "private_networking.platform_endpoints is required when enabled (./agdemo tf vars writes it)."
  }

  validation {
    condition     = contains(["PRIVATE_RANGES_ONLY"], var.private_networking.vpc_egress)
    error_message = "private_networking.vpc_egress: only PRIVATE_RANGES_ONLY is verified (ALL_TRAFFIC needs Cloud NAT, untested)."
  }
}

variable "agdemo_cli" {
  description = "Path of ./agdemo for the destroy guard (guards.tf): relative to infra/terraform, or absolute."
  type        = string
  default     = "../../agdemo"
}
