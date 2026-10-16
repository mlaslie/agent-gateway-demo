# VPC, the /28 attachment subnet (Private Google Access on) and the network attachment the VPC-enabled
# gateway's PSC interface connects to (TESTBED step 1).

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = var.names.network
  auto_create_subnetworks = false
  description             = "${var.prefix} demo: Private Networking mode (VPC-enabled Agent Gateway)"

  lifecycle {
    precondition {
      condition     = var.region != var.base_region
      error_message = "private_networking.region must differ from environment.region: a project can have only one egress gateway per region serving agents (P0 F40)."
    }
  }

  depends_on = [google_project_service.net]
}

resource "google_compute_subnetwork" "att" {
  project                  = var.project_id
  region                   = var.region
  name                     = var.names.subnet
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.subnet_range
  private_ip_google_access = true
}

# ForceNew: name, region, connection_preference. The provider's delete retries the 412 "still has connected
# endpoints" for up to 20 min (about 2.5 min after the gateway delete was observed, P0 §7).
resource "google_compute_network_attachment" "att" {
  project               = var.project_id
  region                = var.region
  name                  = var.names.attachment
  description           = "${var.prefix} demo: Agent Gateway PSC interface"
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [google_compute_subnetwork.att.self_link]
}
