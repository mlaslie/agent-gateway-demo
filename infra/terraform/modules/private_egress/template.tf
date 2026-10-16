# Agent connectivity template (TESTBED step 5). Frozen while a gateway references it (only `description`
# can change, P0 T2 #6-#8), so the name carries a hash of the content and create_before_destroy is on: a
# content change creates the new template, the gateway swaps to it in place (about 11 s, T2 #5), then the old
# one is deleted. `./agdemo tf plan|apply|up --allow-template-swap` lets the plan-safety check pass that swap.
#
# Inside the template the attachment and the target network use the project ID (a number fails validation,
# P0 T2.3); the gateway references the template by project NUMBER (modules/regional).

locals {
  template_content = {
    access_path      = "AGENT_TO_ANYWHERE"
    deployment_model = "CENTRALIZED"
    attachment       = "projects/${var.project_id}/regions/${var.region}/networkAttachments/${var.names.attachment}"
    network          = "projects/${var.project_id}/global/networks/${var.names.network}"
    domains          = var.dns_domains
    vpc_egress       = var.vpc_egress
  }
  template_id = "${var.names.template_prefix}-${substr(sha1(jsonencode(local.template_content)), 0, 6)}"
}

resource "google_network_services_agent_connectivity_template" "tmpl" {
  project                        = var.project_id
  location                       = var.region
  agent_connectivity_template_id = local.template_id
  access_path                    = local.template_content.access_path
  deployment_model               = local.template_content.deployment_model
  description                    = "${var.prefix} demo: Private Networking mode (${var.names.network})"
  labels                         = var.labels

  egress_network_config {
    # Literal project-ID forms (the hashed content); the depends_on below orders them after the network pieces.
    network_attachment = local.template_content.attachment
    vpc_egress         = local.template_content.vpc_egress

    dns_peering_config {
      domains        = local.template_content.domains
      target_network = local.template_content.network
    }
  }

  lifecycle {
    create_before_destroy = true
  }

  # The attachment and the network the content names, and the zone the gateway peers into.
  depends_on = [
    google_compute_network_attachment.att,
    google_compute_network.vpc,
    google_dns_record_set.run_app,
    google_compute_global_forwarding_rule.psc,
  ]
}
