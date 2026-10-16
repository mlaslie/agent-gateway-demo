provider "google" {
  project = var.project_id
  region  = var.region

  # bootstrap writes exactly var.labels; the goog-terraform-provisioned label would diff on every import (T2).
  add_terraform_attribution_label = false
}

locals {
  gateway_project = var.gateway_project_id != "" ? var.gateway_project_id : var.project_id
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
  organization_id        = var.organization_id != "" ? var.organization_id : data.google_project.this.org_id
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

  platform_endpoints          = var.platform_endpoints
  platform_endpoints_existing = var.platform_endpoints_existing
  platform_description        = var.platform_description

  recordings_bucket     = var.recordings_bucket
  staging_force_destroy = var.staging_force_destroy
}

# Recordings (plan §7b, Q3): the bucket is created by `./agdemo tf apply` through the API, like the state
# bucket, so no `terraform destroy` can delete it. Only the UI SA's object access is in Terraform (base).

# T4 placeholder: module "base_private_networking" / module "private_egress" (us-west1). Not built.
