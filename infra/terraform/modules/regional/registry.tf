# Platform endpoints for this region's registry (default-deny allowlist): the Google APIs an Agent Runtime
# agent in this region calls, each with roles/iap.egressor for every Agent Identity agent in the project.
# Same as modules/base registry.tf; the URL list (with this region's regional hosts) comes from Python.

locals {
  # Agent Identity trust domain: org-<ORG_ID> in an organization, proj-<PROJECT_NUMBER> without one.
  trust_domain   = var.organization_id != "" ? "agents.global.org-${var.organization_id}.system.id.goog" : "agents.global.proj-${var.project_number}.system.id.goog"
  project_agents = "principalSet://${local.trust_domain}/attribute.platformContainer/aiplatform/projects/${var.project_number}"

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
}

resource "google_iap_agent_registry_endpoint_iam_member" "platform" {
  for_each = var.platform_endpoints

  project     = var.gateway_project_number
  location    = var.region
  endpoint_id = basename(local.platform_registry_ids[each.key])
  role        = "roles/iap.egressor"
  member      = local.project_agents

}
