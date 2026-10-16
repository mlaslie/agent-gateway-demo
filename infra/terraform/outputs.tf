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

# T4: per-region sections, merged into state.json shared.regions (keyed by region). Empty when Private
# Networking is off. Each section has the base's key names for that region plus `mode` and the network pieces.
output "state_regions" {
  description = "Keys merged into state.json shared.regions."
  value = local.pn_on ? {
    (local.pn.region) = merge(module.pn_region[0].state_regional, {
      mode               = "private_networking"
      private_networking = module.private_egress[0].state
    })
  } : {}
}
