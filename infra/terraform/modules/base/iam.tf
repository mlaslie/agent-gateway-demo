# bootstrap step 2: service accounts and project roles. Additive *_member only: the project has other owners.

locals {
  service_accounts = {
    ui      = { account_id = var.names.ui_sa, display_name = "agdemo demo UI backend" }
    targets = { account_id = var.names.targets_sa, display_name = "agdemo Cloud Run demo targets" }
  }

  # UI backend: reads/applies policies and calls the orchestrator.
  ui_roles = [
    "roles/aiplatform.user",
    "roles/agentregistry.viewer",
    "roles/iap.admin",
    "roles/networkservices.viewer",
    "roles/networksecurity.editor",
    "roles/networkservices.serviceExtensionsAdmin",
    "roles/modelarmor.viewer",
    "roles/logging.viewer",
    "roles/serviceusage.serviceUsageConsumer",
  ]
  # Cloud Run demo targets: the A2A agents call Gemini on Vertex AI.
  targets_roles = ["roles/aiplatform.user", "roles/logging.logWriter"]

  sa_grants = merge(
    { for r in local.ui_roles : "ui ${r}" => { sa = "ui", role = r } },
    { for r in local.targets_roles : "targets ${r}" => { sa = "targets", role = r } },
  )
}

resource "google_service_account" "sa" {
  for_each = local.service_accounts

  project      = var.project_id
  account_id   = each.value.account_id
  display_name = each.value.display_name

  depends_on = [google_project_service.api]
}

resource "google_project_iam_member" "sa" {
  for_each = local.sa_grants

  project = var.project_id
  role    = each.value.role
  member  = google_service_account.sa[each.value.sa].member
}

# Presenters call Agent Runtime directly and through Gemini Enterprise (with their own credentials).
resource "google_project_iam_member" "presenters" {
  for_each = var.shared_project_grants ? toset(var.presenters) : toset([])

  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = each.value

  depends_on = [google_project_service.api]
}
