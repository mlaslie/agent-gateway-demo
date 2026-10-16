# The base enables the demo's 21 APIs; the VPC pieces also need Compute Engine and Cloud DNS. Never disabled
# on destroy (other teams' resources live in the project).
resource "google_project_service" "net" {
  for_each = toset(["compute.googleapis.com", "dns.googleapis.com"])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
