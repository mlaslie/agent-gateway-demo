# template_ref: what the VPC-enabled gateway sets in agent_connectivity_template, in the project NUMBER form
# (the API rejects the id form on create, P0 T1). It depends on the service-agent grants, so the gateway is
# created after them and destroyed before them.
output "template_ref" {
  value      = "projects/${var.project_number}/locations/${var.region}/agentConnectivityTemplates/${google_network_services_agent_connectivity_template.tmpl.agent_connectivity_template_id}"
  depends_on = [google_project_iam_member.agentgateway]
}

# Merged into state.json shared.regions.<region>.private_networking (the UI's "Under the hood" panel).
output "state" {
  value = {
    network             = google_compute_network.vpc.id
    subnet              = google_compute_subnetwork.att.id
    subnet_range        = google_compute_subnetwork.att.ip_cidr_range
    attachment          = google_compute_network_attachment.att.id
    psc_address         = google_compute_global_address.psc.id
    psc_ip              = google_compute_global_address.psc.address
    psc_forwarding_rule = google_compute_global_forwarding_rule.psc.id
    psc_target          = "all-apis"
    dns_zone            = google_dns_managed_zone.run_app.id
    dns_name            = google_dns_managed_zone.run_app.dns_name
    dns_records         = sort([for r in google_dns_record_set.run_app : r.name])
    template            = google_network_services_agent_connectivity_template.tmpl.id
    template_id         = google_network_services_agent_connectivity_template.tmpl.agent_connectivity_template_id
    template_ref        = "projects/${var.project_number}/locations/${var.region}/agentConnectivityTemplates/${google_network_services_agent_connectivity_template.tmpl.agent_connectivity_template_id}"
    deployment_model    = local.template_content.deployment_model
    vpc_egress          = local.template_content.vpc_egress
    dns_domains         = local.template_content.domains
    service_agent       = local.agentgateway_sa
    service_agent_roles = sort([for m in google_project_iam_member.agentgateway : m.role])
  }
}
