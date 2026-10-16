# gcp-sa-agentgateway needs to use the attachment and peer DNS into the VPC (P0 F11: networkAdmin + dns.peer
# was sufficient; fewer roles untested, plan §8 Q6). Additive _member grants only.
#
# Plan §8 Q7 (does the service agent exist in a fresh project before the first gateway?): no google-beta
# google_project_service_identity (its service name is unverified) and no local-exec. The grants wait for the
# base egress gateway (var.service_agent_after, referenced in a precondition so Terraform orders them after
# it): creating that gateway provisions the project's Agent Gateway service agents. If a fresh-project run
# (T5) still fails with "service account ... does not exist", re-run apply. The VPC-enabled gateway
# (modules/regional) depends on these grants through the template output, so on destroy it goes first.

locals {
  agentgateway_sa = "serviceAccount:service-${var.project_number}@gcp-sa-agentgateway.iam.gserviceaccount.com"
}

resource "google_project_iam_member" "agentgateway" {
  for_each = var.manage_service_agent_roles ? toset(var.service_agent_roles) : toset([])

  project = var.project_id
  role    = each.value
  member  = local.agentgateway_sa

  lifecycle {
    precondition {
      condition     = var.service_agent_after != ""
      error_message = "The base egress gateway must exist before the gcp-sa-agentgateway grants (plan §8 Q7)."
    }
  }
}
