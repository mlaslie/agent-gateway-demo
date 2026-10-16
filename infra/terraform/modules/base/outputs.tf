# Same keys and value formats bootstrap writes to state.json `shared` (agdemo_core/infra/bootstrap.py).
output "state_shared" {
  value = {
    ui_sa          = google_service_account.sa["ui"].email
    targets_sa     = google_service_account.sa["targets"].email
    artifact_repo  = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}"
    staging_bucket = "gs://${google_storage_bucket.staging.name}"

    egress_gateway      = google_network_services_agent_gateway.gw["egress"].id
    ingress_gateway     = google_network_services_agent_gateway.gw["ingress"].id
    ingress_gateway_url = "https://${var.region}-aiplatform.googleapis.com"
    gateway_service_agents = sort(distinct(compact([
      for g in google_network_services_agent_gateway.gw : try(g.agent_gateway_card[0].service_extensions_service_account, "")
    ])))

    egress_iap_extension = google_network_services_authz_extension.egress_iap.id
    egress_iap_policy    = google_network_security_authz_policy.egress_iap.id

    model_armor_template  = local.ma_template_name
    model_armor_extension = google_network_services_authz_extension.model_armor[var.project_id].id

    platform_registry_ids = local.platform_registry_ids

    # New key (plan §7b): where the UI keeps recordings. Empty = the old staging/recordings location.
    recordings_bucket = var.recordings_bucket
  }
}

# T4: the Private Networking module orders its gcp-sa-agentgateway grants after this gateway (plan §8 Q7).
output "egress_gateway_id" {
  value = google_network_services_agent_gateway.gw["egress"].id
}
