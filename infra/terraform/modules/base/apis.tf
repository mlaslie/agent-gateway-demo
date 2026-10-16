# bootstrap step 1. Never disabled on destroy: other teams' resources live in the project.
locals {
  api_projects = distinct([var.project_id, var.gateway_project_id])
  apis = {
    for pair in setproduct(local.api_projects, var.required_apis) : "${pair[0]}/${pair[1]}" => {
      project = pair[0]
      service = pair[1]
    }
  }
}

resource "google_project_service" "api" {
  for_each = local.apis

  project            = each.value.project
  service            = each.value.service
  disable_on_destroy = false
}
