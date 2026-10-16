# Destroy guard (docs/TERRAFORM_PLAN.md §4.4, phase T3).
#
# It depends on every resource in module.base (and, with Private Networking on, in module.private_egress and
# module.pn_region), so `terraform destroy` destroys it FIRST. Its destroy-time
# provisioner runs `./agdemo check-clean --strict` for this environment (every region it covers); a non-zero exit fails the destroy
# before anything else is deleted (nothing else can start: everything waits for the guard). `./agdemo down`
# runs the same check before calling destroy, so a down that reached destroy passes it.
#
# It does NOT protect against:
#   - `terraform destroy -target=...` / `terraform apply -target=...` on other addresses (the guard isn't
#     in the targeted graph), `terraform state rm`, or deleting this block from the configuration;
#   - a tainted guard (destroy provisioners don't run for tainted resources);
#   - a machine without ./agdemo (uv) or the environment's config: the check then fails, which aborts the
#     destroy (fail closed). Run it where the repo and AGDEMO_CONFIG (absolute path) describe the environment.
# A deliberate bypass (broken CLI, environment already gone) is `terraform state rm terraform_data.teardown_guard`.
resource "terraform_data" "teardown_guard" {
  input = {
    # Relative to the Terraform working directory (infra/terraform, also with -chdir).
    agdemo = var.agdemo_cli
    # check-clean refuses (exit 2) when config/demo.yaml describes another environment.
    environment = "${var.project_id}/${var.region}/${var.prefix}"
    gateways    = concat([module.base.state_shared.egress_gateway, module.base.state_shared.ingress_gateway], module.pn_region[*].egress_gateway_id)
    extensions  = [module.base.state_shared.egress_iap_extension, module.base.state_shared.model_armor_extension]
  }

  depends_on = [module.base, module.private_egress, module.pn_region]

  provisioner "local-exec" {
    when    = destroy
    command = "${self.input.agdemo} check-clean --strict --expect ${self.input.environment}"
  }
}
