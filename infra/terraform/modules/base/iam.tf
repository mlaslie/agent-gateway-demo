# bootstrap step 2: service accounts and project roles. Additive *_member only: the project has other owners.

locals {
  service_accounts = {
    ui      = { account_id = var.names.ui_sa, display_name = "agdemo demo UI backend" }
    targets = { account_id = var.names.targets_sa, display_name = "agdemo Cloud Run demo targets" }
  }

  # auth_strategy (docs/PERMISSIONS.md, Auth strategy): standard = Google predefined roles; minimum = the
  # least-privilege set tested live (§0), with two project custom roles. Role ids can't contain "-".
  minimum     = var.auth_strategy == "minimum"
  role_prefix = replace(var.prefix, "-", "_")
  custom_roles = local.minimum ? {
    registry_egress = {
      id          = "${local.role_prefix}RegistryEgressAdmin"
      title       = "${var.prefix} demo UI: egress grants on Agent Registry entries"
      permissions = ["iap.webServices.getIamPolicy", "iap.webServices.setIamPolicy", "iap.webServiceVersions.getIamPolicy", "iap.webServiceVersions.setIamPolicy"]
    }
    gateway_policy = {
      id    = "${local.role_prefix}GatewayPolicyEditor"
      title = "${var.prefix} demo UI: Model Armor authz policies, gateway and extension reads"
      permissions = [
        "networksecurity.authzPolicies.create", "networksecurity.authzPolicies.delete", "networksecurity.authzPolicies.get",
        "networksecurity.authzPolicies.list", "networksecurity.operations.get", "networkservices.authzExtensions.get",
        "networkservices.authzExtensions.list", "networkservices.authzExtensions.use", "networkservices.agentGateways.get",
        "networkservices.agentGateways.list", "networkservices.operations.get",
      ]
    }
  } : {}

  # UI backend: reads/applies policies and calls the orchestrator.
  ui_roles = local.minimum ? [
    "roles/aiplatform.expressUser",
    "roles/agentregistry.viewer",
    "roles/logging.viewer",
    "roles/serviceusage.serviceUsageConsumer",
    "projects/${var.project_id}/roles/${local.role_prefix}RegistryEgressAdmin",
    "projects/${var.project_id}/roles/${local.role_prefix}GatewayPolicyEditor",
    ] : [
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
  targets_roles = local.minimum ? ["roles/aiplatform.expressUser"] : ["roles/aiplatform.user", "roles/logging.logWriter"]

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

resource "google_project_iam_custom_role" "demo" {
  for_each = local.custom_roles

  project     = var.project_id
  role_id     = each.value.id
  title       = each.value.title
  permissions = each.value.permissions
  stage       = "GA"
}

resource "google_project_iam_member" "sa" {
  for_each = local.sa_grants

  project = var.project_id
  role    = each.value.role
  member  = google_service_account.sa[each.value.sa].member

  depends_on = [google_project_iam_custom_role.demo]
}

# Presenters call Agent Runtime directly and through Gemini Enterprise (with their own credentials).
resource "google_project_iam_member" "presenters" {
  for_each = var.shared_project_grants ? toset(var.presenters) : toset([])

  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = each.value

  depends_on = [google_project_service.api]
}
