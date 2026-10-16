# Staging bucket for this region's Agent Runtime engines (the vertexai SDK stages the pickles here). The
# images stay in the base region's Artifact Registry repo (Cloud Run pulls across regions).
resource "google_storage_bucket" "staging" {
  project                     = var.project_id
  name                        = var.names.staging_bucket
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  labels                      = var.labels
  force_destroy               = var.staging_force_destroy
}
