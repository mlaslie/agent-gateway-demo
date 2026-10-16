# Merged into state.json shared.regions.<region> (the keys the CLI and UI read for a theme in this region;
# same names and formats as the base's state_shared).
output "state_regional" {
  value = {
    region                = var.region
    egress_gateway        = google_network_services_agent_gateway.egress.id
    egress_iap_extension  = google_network_services_authz_extension.egress_iap.id
    egress_iap_policy     = google_network_security_authz_policy.egress_iap.id
    staging_bucket        = "gs://${google_storage_bucket.staging.name}"
    platform_registry_ids = local.platform_registry_ids
    gateway_service_agents = sort(compact([
      try(google_network_services_agent_gateway.egress.agent_gateway_card[0].service_extensions_service_account, "")
    ]))
  }
}

output "egress_gateway_id" {
  value = google_network_services_agent_gateway.egress.id
}
