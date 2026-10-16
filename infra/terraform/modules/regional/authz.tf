# IAP request authorization on the VPC-enabled gateway: roles/iap.egressor on each registry entry decides
# every destination (same as modules/base authz.tf, including the ignore_changes Policy Studio needs).

locals {
  iap_dry_run = var.iap_enforcement == "DRY_RUN"
}

resource "google_network_services_authz_extension" "egress_iap" {
  project   = var.gateway_project_id
  location  = var.region
  name      = var.names.egress_iap_extension
  service   = "iap.googleapis.com"
  fail_open = local.iap_dry_run
  timeout   = "1s"
  labels    = var.labels
  metadata  = merge({ iapPolicyVersion = "V1" }, local.iap_dry_run ? { iamEnforcementMode = "DRY_RUN" } : {})

  lifecycle {
    ignore_changes = [fail_open, metadata]
  }
}

resource "google_network_security_authz_policy" "egress_iap" {
  project        = var.gateway_project_id
  location       = var.region
  name           = var.names.egress_iap_policy
  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"
  labels         = var.labels

  target {
    resources = [google_network_services_agent_gateway.egress.id]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.egress_iap.id]
    }
  }

  lifecycle {
    ignore_changes = [custom_provider]
  }
}
