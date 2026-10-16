# bootstrap step 3: Artifact Registry repo and the Agent Runtime staging bucket.

resource "google_artifact_registry_repository" "repo" {
  project       = var.project_id
  location      = var.region
  repository_id = var.names.artifact_repo
  format        = "DOCKER"
  description   = "agdemo images"
  labels        = var.labels

  depends_on = [google_project_service.api]
}

resource "google_storage_bucket" "staging" {
  project                     = var.project_id
  name                        = var.names.staging_bucket
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  labels                      = var.labels
  # Engine pickles only; recordings live in the surviving recordings bucket (plan §7b).
  force_destroy = var.staging_force_destroy

  depends_on = [google_project_service.api]
}

# The recordings bucket itself is created by the CLI (never destroyed by Terraform); the UI backend
# reads and writes recordings there.
resource "google_storage_bucket_iam_member" "recordings_ui" {
  count = var.recordings_bucket != "" ? 1 : 0

  bucket = var.recordings_bucket
  role   = "roles/storage.objectUser"
  member = google_service_account.sa["ui"].member
}
