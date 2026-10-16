# `./agdemo tf output-sync` merges `state_shared` into config/state.json `shared` (same keys bootstrap
# writes), keeping every other key (ui_url, themes, ...).
output "state_shared" {
  description = "Keys merged into state.json shared."
  value       = module.base.state_shared
}

output "environment" {
  description = "Which environment this state describes (checked by output-sync before merging)."
  value = {
    project_id         = var.project_id
    gateway_project_id = local.gateway_project
    region             = var.region
    prefix             = var.prefix
  }
}
