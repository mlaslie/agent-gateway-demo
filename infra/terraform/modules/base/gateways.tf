# bootstrap step 4: egress (Agent-to-Anywhere, bound to the regional registry) and ingress (Client-to-Agent).
# `protocols` is deprecated since google 7.37.0 and left unset.

locals {
  gateways = {
    egress = {
      project     = var.gateway_project_id
      name        = var.names.egress_gateway
      access_path = "AGENT_TO_ANYWHERE"
      registries  = ["//agentregistry.googleapis.com/projects/${var.gateway_project_id}/locations/${var.region}"]
    }
    ingress = {
      project     = var.project_id
      name        = var.names.ingress_gateway
      access_path = "CLIENT_TO_AGENT"
      registries  = null
    }
  }
}

resource "google_network_services_agent_gateway" "gw" {
  for_each = local.gateways

  project     = each.value.project
  location    = var.region
  name        = each.value.name
  description = "${var.prefix} demo ${each.key} gateway"
  registries  = each.value.registries
  labels      = var.labels

  google_managed {
    governed_access_path = each.value.access_path
  }

  # T4 placeholder: agent_connectivity_template for the Private Networking gateway (us-west1 only).

  timeouts {
    create = "40m"
    delete = "40m"
  }

  depends_on = [google_project_service.api]
}
