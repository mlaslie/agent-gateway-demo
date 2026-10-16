# Private zone run.app. -> the PSC endpoint: apex and wildcard A records (both run.app URL forms resolve to it).
# The gateway resolves run.app through DNS peering into this VPC (the template's dns_peering_config).

resource "google_dns_managed_zone" "run_app" {
  project     = var.project_id
  name        = var.names.dns_zone
  dns_name    = "run.app."
  description = "${var.prefix} demo: run.app -> PSC endpoint for Google APIs (Private Networking mode)"
  visibility  = "private"
  labels      = var.labels

  private_visibility_config {
    networks {
      network_url = google_compute_network.vpc.id
    }
  }

  depends_on = [google_project_service.net]
}

resource "google_dns_record_set" "run_app" {
  for_each = toset(["run.app.", "*.run.app."])

  project      = var.project_id
  managed_zone = google_dns_managed_zone.run_app.name
  name         = each.value
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.psc.address]
}
