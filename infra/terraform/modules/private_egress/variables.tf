# Private Networking mode (docs/TERRAFORM_PLAN.md §7c, phase T4): the VPC side of a VPC-enabled egress
# gateway. Everything lives in the gateway project (the attachment, the zone and the template must be in the
# gateway's project; the attachment also in its region). The root passes the values from
# `./agdemo tf vars` (config/demo.yaml private_networking).

variable "project_id" {
  description = "Gateway project (VPC, attachment, PSC endpoint, zone, template)."
  type        = string
}

variable "project_number" {
  description = "Gateway project number: the gcp-sa-agentgateway service agent is service-<number>@..."
  type        = string
}

variable "region" {
  description = "Private Networking region (the attachment and the template must be in the gateway's region)."
  type        = string
}

variable "base_region" {
  description = "The base (Internet mode) region: must differ (one egress gateway per project and region)."
  type        = string
}

variable "prefix" { type = string }
variable "labels" { type = map(string) }

variable "names" {
  description = "Resource names, computed by agdemo_core (config.pn_names)."
  type = object({
    network             = string
    subnet              = string
    attachment          = string
    psc_address         = string
    psc_forwarding_rule = string
    dns_zone            = string
    template_prefix     = string
  })

  validation {
    # F45: the PSC endpoint for Google APIs forwarding rule allows 1-20 lowercase letters/digits, no hyphen.
    condition     = can(regex("^[a-z][a-z0-9]{0,19}$", var.names.psc_forwarding_rule))
    error_message = "names.psc_forwarding_rule must be 1-20 lowercase letters/digits (no hyphen), starting with a letter."
  }
}

variable "subnet_range" {
  description = "Attachment subnet (/28 is enough: the gateway's PSC interface takes the first usable IP)."
  type        = string
}

variable "psc_ip" {
  description = "Unused private IP for the PSC endpoint for Google APIs; run.app resolves to it inside the VPC."
  type        = string
}

variable "dns_domains" {
  description = "Domains the gateway resolves through the VPC (DNS peering). run.app. is the one this mode needs."
  type        = list(string)
}

variable "vpc_egress" {
  description = "PRIVATE_RANGES_ONLY: only names that resolve to private IPs go into the VPC (verified, P0 F15)."
  type        = string
}

variable "manage_service_agent_roles" {
  description = "Grant gcp-sa-agentgateway roles/compute.networkAdmin + roles/dns.peer (project-wide, additive)."
  type        = bool
}

variable "service_agent_roles" {
  description = "Verified sufficient in P0 (F11). A narrower custom role is untested (plan §8 Q6)."
  type        = list(string)
  default     = ["roles/compute.networkAdmin", "roles/dns.peer"]
}

variable "service_agent_after" {
  description = "Plan §8 Q7: any value known only once the base egress gateway exists (its id). The service-agent grants wait for it, so in a fresh project the first gateway has provisioned gcp-sa-agentgateway first."
  type        = string
}
