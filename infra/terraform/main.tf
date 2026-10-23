provider "google" {
  project = var.project_id
  region  = var.region

  # bootstrap writes exactly var.labels; the goog-terraform-provisioned label would diff on every import (T2).
  add_terraform_attribution_label = false
}

locals {
  gateway_project = var.gateway_project_id != "" ? var.gateway_project_id : var.project_id
  organization_id = var.organization_id != "" ? var.organization_id : data.google_project.this.org_id
  pn              = var.private_networking
  pn_on           = var.private_networking.enabled
}

data "google_project" "this" {
  project_id = var.project_id
}

data "google_project" "gateway" {
  project_id = local.gateway_project
}

# The destroyable foundation (`down` = reset --all, CLI teardown, then terraform destroy).
module "base" {
  source = "./modules/base"

  project_id             = var.project_id
  project_number         = data.google_project.this.number
  gateway_project_id     = local.gateway_project
  gateway_project_number = data.google_project.gateway.number
  organization_id        = local.organization_id
  region                 = var.region
  prefix                 = var.prefix
  labels                 = var.labels
  required_apis          = var.required_apis
  names                  = var.names

  iap_enforcement           = var.iap_enforcement
  model_armor_filters       = var.model_armor_filters
  model_armor_block_marker  = var.model_armor_block_marker
  model_armor_static_agents = var.model_armor_static_agents
  presenters                = var.presenters
  shared_project_grants     = var.shared_project_grants
  auth_strategy             = var.auth_strategy

  platform_endpoints          = var.platform_endpoints
  platform_endpoints_existing = var.platform_endpoints_existing
  platform_description        = var.platform_description

  recordings_bucket     = var.recordings_bucket
  staging_force_destroy = var.staging_force_destroy
}

# Recordings (plan §7b, Q3): the bucket is created by `./agdemo tf apply` through the API, like the state
# bucket, so no `terraform destroy` can delete it. Only the UI SA's object access is in Terraform (base).

# Private Networking mode (plan §7c, phase T4), only when private_networking.enabled: the VPC side
# (private_egress) and a second-region instance of what the mode needs from the base (regional), with the
# VPC-enabled gateway created WITH the template. The base (and its us-east4 import addresses) is unchanged.
module "private_egress" {
  count  = local.pn_on ? 1 : 0
  source = "./modules/private_egress"

  project_id     = local.gateway_project
  project_number = data.google_project.gateway.number
  region         = local.pn.region
  base_region    = var.region
  prefix         = var.prefix
  labels         = var.labels
  names = {
    network             = local.pn.names.network
    subnet              = local.pn.names.subnet
    attachment          = local.pn.names.attachment
    psc_address         = local.pn.names.psc_address
    psc_forwarding_rule = local.pn.names.psc_forwarding_rule
    dns_zone            = local.pn.names.dns_zone
    template_prefix     = local.pn.names.template_prefix
  }
  subnet_range               = local.pn.subnet_range
  psc_ip                     = local.pn.psc_ip
  dns_domains                = local.pn.dns_domains
  vpc_egress                 = local.pn.vpc_egress
  manage_service_agent_roles = local.pn.manage_service_agent_roles
  service_agent_after        = module.base.egress_gateway_id
}

module "pn_region" {
  count  = local.pn_on ? 1 : 0
  source = "./modules/regional"

  project_id             = var.project_id
  project_number         = data.google_project.this.number
  gateway_project_id     = local.gateway_project
  gateway_project_number = data.google_project.gateway.number
  organization_id        = local.organization_id
  region                 = local.pn.region
  prefix                 = var.prefix
  labels                 = var.labels
  names = {
    egress_gateway       = local.pn.names.egress_gateway
    egress_iap_extension = local.pn.names.egress_iap_extension
    egress_iap_policy    = local.pn.names.egress_iap_policy
    staging_bucket       = local.pn.names.staging_bucket
  }
  iap_enforcement             = var.iap_enforcement
  platform_endpoints          = local.pn.platform_endpoints
  platform_endpoints_existing = local.pn.platform_endpoints_existing
  platform_description        = var.platform_description
  staging_force_destroy       = var.staging_force_destroy
  connectivity_template       = module.private_egress[0].template_ref

  # The APIs, service accounts and project grants come from the base.
  depends_on = [module.base]
}
