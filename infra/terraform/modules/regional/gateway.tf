# The VPC-enabled egress gateway (Agent-to-Anywhere, bound to this region's registry), created with the
# connectivity template from the start. A later template swap (content-hash name) is an in-place PATCH.

resource "google_network_services_agent_gateway" "egress" {
  project                     = var.gateway_project_id
  location                    = var.region
  name                        = var.names.egress_gateway
  description                 = "${var.prefix} demo egress gateway (Private Networking, VPC-enabled)"
  registries                  = ["//agentregistry.googleapis.com/projects/${var.gateway_project_id}/locations/${var.region}"]
  labels                      = var.labels
  agent_connectivity_template = var.connectivity_template

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  timeouts {
    create = "40m"
    delete = "40m"
  }

  lifecycle {
    # Same as modules/base: the deprecated field is left unset.
    ignore_changes = [protocols]

    precondition {
      condition     = startswith(var.connectivity_template, "projects/${var.gateway_project_number}/")
      error_message = "agent_connectivity_template must use the project NUMBER (the API rejects the id form, P0 T1)."
    }
  }
}
