terraform {
  # 1.7: for_each on import blocks (used by the T2 migration of the live demo).
  required_version = ">= 1.7"

  required_providers {
    google = {
      source = "hashicorp/google"
      # 8.7.0: deployment_model / agent_compute on the connectivity template (T4). Everything in base is GA.
      version = ">= 8.7.0, < 9.0.0"
    }
  }
}
