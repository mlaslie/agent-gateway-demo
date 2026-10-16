# Partial configuration. `./agdemo tf init` passes the rest:
#   -backend-config=bucket=<project>-agdemo-tfstate   (created by the CLI, never by Terraform; versioning on)
#   -backend-config=prefix=<resource_prefix>/<project>/<region>
# One state prefix per environment; no workspaces.
# `./agdemo tf init --local-state PATH` writes a git-ignored backend_override.tf (local backend) instead,
# for read-only plans against a scratch state.
terraform {
  backend "gcs" {}
}
