# bootstrap step 7: platform endpoints (default-deny allowlist). The Google APIs an Agent Runtime agent
# calls, each with roles/iap.egressor for every Agent Identity agent in the project. Theme entries and
# grants are live toggles (CLI / UI), never here.

locals {
  project_agents = "principalSet://agents.global.org-${var.organization_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${var.project_number}"

  platform_new = { for url, e in var.platform_endpoints : url => e if !contains(keys(var.platform_endpoints_existing), url) }
  platform_registry_ids = merge(
    { for url, s in google_agent_registry_service.platform : url => s.registry_resource },
    var.platform_endpoints_existing,
  )
}

resource "google_agent_registry_service" "platform" {
  for_each = local.platform_new

  project      = var.gateway_project_id
  location     = var.region
  service_id   = each.value.service_id
  display_name = each.value.display_name
  description  = var.platform_description

  endpoint_spec {
    type = "NO_SPEC"
  }

  interfaces {
    url              = each.key
    protocol_binding = "JSONRPC"
  }

  depends_on = [google_project_service.api]
}

resource "google_iap_agent_registry_endpoint_iam_member" "platform" {
  # Static keys (the URLs); the registry resource is known after create.
  for_each = var.platform_endpoints

  project     = var.gateway_project_number
  location    = var.region
  endpoint_id = basename(local.platform_registry_ids[each.key])
  role        = "roles/iap.egressor"
  member      = local.project_agents

  lifecycle {
    precondition {
      condition     = var.organization_id != ""
      error_message = "No organization id: set organization_id (the Agent Identity principalSet needs it)."
    }
  }
}
