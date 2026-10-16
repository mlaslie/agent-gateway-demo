# PSC endpoint for Google APIs (all-apis bundle). Cloud Run ingress=internal accepts traffic that arrives
# through it from a same-project VPC (P0 F30 refuted for this pattern), so no load balancer is needed.

resource "google_compute_global_address" "psc" {
  project      = var.project_id
  name         = var.names.psc_address
  purpose      = "PRIVATE_SERVICE_CONNECT"
  address_type = "INTERNAL"
  address      = var.psc_ip
  network      = google_compute_network.vpc.id
  labels       = var.labels
}

# The name can't carry the "<prefix>-" scheme (F45: letters and digits only); ownership shows on the address
# (name + labels). No labels here: they aren't needed and the rule is identified by its address.
resource "google_compute_global_forwarding_rule" "psc" {
  project               = var.project_id
  name                  = var.names.psc_forwarding_rule
  target                = "all-apis"
  network               = google_compute_network.vpc.id
  ip_address            = google_compute_global_address.psc.id
  load_balancing_scheme = ""
}
