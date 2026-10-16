# bootstrap step 5: IAP request authorization on the egress gateway (roles/iap.egressor on each registry
# entry decides every destination). The ingress gateway gets no IAP policy: CLIENT_TO_AGENT enforces
# Model Armor only.

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
  # V1 = IAM allow policies (roles/iap.egressor + conditions).
  metadata = merge({ iapPolicyVersion = "V1" }, local.iap_dry_run ? { iamEnforcementMode = "DRY_RUN" } : {})

  lifecycle {
    # Policy Studio's Enforce / Dry run toggle flips these (plan §8 Q1): demo.yaml only sets the first value.
    ignore_changes = [fail_open, metadata]
  }

  depends_on = [google_project_service.api]
}

resource "google_network_security_authz_policy" "egress_iap" {
  project        = var.gateway_project_id
  location       = var.region
  name           = var.names.egress_iap_policy
  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"
  labels         = var.labels

  target {
    resources = [google_network_services_agent_gateway.gw["egress"].id]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.egress_iap.id]
    }
  }

  lifecycle {
    # Policy Studio's "give this gateway its own extension" repoints the provider (plan §8 Q1).
    ignore_changes = [custom_provider]
  }
}
