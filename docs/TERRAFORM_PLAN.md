# Terraform foundation plan (base + private egress)

**Status:** T1 built (2026-10-16; see §7a). **T2: the read-only part is done** (2026-10-16; see §7d): the `tf import` generator, a migration-mode plan-safety check and a read-only plan against the live demo with only imports and additive creates. The live steps (legacy cleanup, state bucket, recordings copy, apply, UI redeploy) are in [TERRAFORM_MIGRATION_RUNBOOK.md](TERRAFORM_MIGRATION_RUNBOOK.md) and haven't been run. **T3 built** (2026-10-16; see §7e): `up`, `down`, `reset --all`, `check-clean`, the destroy guard; unit-tested on fakes, plan-only and read-only checks, never run live. **T4 built** (2026-10-16; see §7f): the Private Networking mode (second region, VPC-enabled gateway), region-aware themes, the `privnet` theme and the UI's Network switch; plan-only, never applied. T5 not started. Nothing in GCP was changed while writing it: the only calls were read-only lists and GETs against the demo project, plus a read of the provider schema and source.

**Goal:** deploy the demo's whole foundation with Terraform, for both the public (non-VPC) setup and the private-egress (VPC) test bed. Clean up in two steps: first a **reset** through the API/CLI that removes everything that isn't base, then `terraform destroy` for the foundation.

Placeholders used below: project `acme-prod`, project number `123456789012`, region `us-east4`, prefix `agdemo`.

Related docs:
- [PRIVATE_EGRESS_TESTBED.md](PRIVATE_EGRESS_TESTBED.md): the gotchas and the manual recipe.
- Policy Studio `docs/P0_FINDINGS.md`: the live results, errors and timings this plan relies on.
- Policy Studio `docs/PRIVATE_NETWORKING_PLAN.md`. Its §11 row "connectivity_template: no provider resource known" is out of date: the resource has existed since 8.4.0 (see §1).
- [BACKLOG.md](BACKLOG.md), "#17 research": the earlier tooling matrix for policy operations.

---

## 0. Summary

- **Provider:** use `hashicorp/google` **>= 8.7.0, < 9.0** (8.7.0 came out on 2026-10-13). Every foundation resource is GA in that provider, so `google-beta` isn't needed. The one possible exception is `google_project_service_identity` (beta only); see open question Q7.
- **Not in the provider:**
  - binding an existing engine to a gateway;
  - registering an agent in a Gemini Enterprise app.

  Neither belongs in the foundation, so both stay in the CLI.
- **Split:**
  - **Terraform owns everything that exists before the first theme deploy and doesn't change during a demo:**
    - APIs, service accounts, project IAM, the Artifact Registry repo and the staging bucket;
    - both gateways, the IAP and Model Armor authz extensions, the IAP `REQUEST_AUTHZ` policy and the Model Armor template;
    - the 25 platform registry entries and their egressor grant;
    - the Model Armor and networking roles for the service agents;
    - with the toggle on, the whole private-egress network and the connectivity template.
  - **The CLI owns:**
    - everything built from code: images, theme Cloud Run services, theme registry entries, Agent Runtime engines, the UI and Gemini Enterprise registration;
    - every live toggle: gateway bindings, IAP grants, Model Armor policies, enforce/dry run.
- **Layout:** one root, `infra/terraform/`, with two modules, `base` and `private_egress` (`private_egress_enabled` toggle, `count`). The state is in a GCS bucket that the CLI creates and Terraform never manages. Each environment gets its own state prefix through `-backend-config` (no workspaces). Variables are generated from `config/demo.yaml`, and outputs are merged into `config/state.json`.
- **Lifecycle:**
  - `./agdemo up` = `terraform apply`, then the CLI deploys.
  - `./agdemo down` = global reset, then CLI teardown, then a cleanliness check, then `terraform destroy`.
  - A destroy-time guard resource aborts a bare `terraform destroy` while anything still depends on the gateways.
- **Migration:**
  - `import` blocks for the live gateways, extensions, policy, template, service accounts, repo, bucket and platform registry entries. The gate is a plan with no delete or replace.
  - Private egress on the live `agdemo-egress` **can't** be done by Terraform:
    - the provider marks `network_config.egress.network_attachment` ForceNew, so adding the inline step plans a replace of the gateway;
    - the direct bare → template PATCH fails with INTERNAL.

    So it's a one-time CLI migration (`./agdemo private migrate-gateway`). Terraform then adopts the result with zero diff.
- **Effort:** about 8–12 working days in 5 phases (§7).

---

## 1. Provider support (evidence)

**How this was checked:**
- **Schema:** `terraform providers schema -json` against the cached `hashicorp/google` 8.6.0 binary (an earlier session's scratchpad provider cache).
- **Version history:** the provider CHANGELOGs (`CHANGELOG.md`, `CHANGELOG_v7.md`, `_v6`, `_v5`, `_v4` on `main`), plus `google-beta`'s `CHANGELOG.md`.
- **Update behaviour:** the v8.7.0 Go source for 8 resources. ForceNew fields, update masks, retry predicates and timeouts were read there.

Latest release: **8.7.0 (October 13, 2026)**; 8.8.0 is unreleased.

### 1.1 Table

| Need | Resource | GA since | Notes (update / replace behaviour) |
|---|---|---|---|
| APIs | `google_project_service` | old | Set `disable_on_destroy = false`, because other teams' resources live in the project |
| Service accounts | `google_service_account` | old | `create_ignore_already_exists` exists, but use `import` instead (§5) so destroy ownership is explicit |
| Project IAM | `google_project_iam_member` | old | **Only `_member`** (additive). Never `_binding` or `_policy`: the project has other owners |
| Artifact Registry | `google_artifact_registry_repository` | 4.29.0 | Delete removes the images |
| Staging bucket | `google_storage_bucket` | old | `force_destroy` decides whether `down` can delete the bucket while engine pickles and `recordings/` are still in it (Q3) |
| VPC, subnet | `google_compute_network`, `google_compute_subnetwork` | old | `/28`, `private_ip_google_access = true` |
| Network attachment | `google_compute_network_attachment` | 4.81.0; GA 5.36.0 | ForceNew: `name`, `region`, `connection_preference`. **Delete retries `IsNetworkAttachmentConnectedEndpointsError` (412) within a 20 min timeout.** That covers the ~2.5 min wait after the gateway is deleted |
| PSC endpoint for Google APIs | `google_compute_global_address` (`purpose = "PRIVATE_SERVICE_CONNECT"`, `address_type = "INTERNAL"`, `network`) + `google_compute_global_forwarding_rule` (`target = "all-apis"`, `load_balancing_scheme = ""`, `network`) | old | Forwarding-rule name: 1–20 lowercase letters/digits with **no hyphen** (F45), e.g. `agdemopsc`. Consider `no_automate_dns_zone` (it controls the auto-created `p.googleapis.com` zone) |
| Private DNS | `google_dns_managed_zone` (`dns_name = "run.app."`, private, the VPC) + 2× `google_dns_record_set` (`run.app.`, `*.run.app.` A → the PSC IP) | old | — |
| **Agent Gateway** | `google_network_services_agent_gateway` | Beta 7.21.0, GA 7.37.0 | See §1.2 |
| **Agent connectivity template** | `google_network_services_agent_connectivity_template` | **8.4.0**. 8.5.0: `domains`, `tls_config`. **8.7.0: `deployment_model`, `agent_compute`** | See §1.3. P0 sent `deploymentModel: CENTRALIZED`, so pin **>= 8.7.0** to set it explicitly |
| Authz extension | `google_network_services_authz_extension` | 6.14.0 (`forward_attributes` 8.1.0) | All fields updatable in place (mask includes `failOpen`, `metadata`, `service`, `timeout`) |
| Authz policy | `google_network_security_authz_policy` | 6.14.0 (`policy_profile` 7.20.0) | ForceNew: `policy_profile`. `customProvider`, `target` and `labels` are updated in place |
| Model Armor template | `google_model_armor_template` | 6.43.0 | ForceNew: `template_id`, `location`. `filter_config` and `template_metadata` updated in place |
| Agent Registry entry | `google_agent_registry_service` | **7.39.0** (`name` 7.40.0) | Updatable: `interfaces`, specs, `display_name`, `description`. `registry_resource` (computed) feeds the IAP IAM resources. Data sources `google_agent_registry_{agent,mcp_server,endpoint}` since 7.39.0 |
| IAP IAM on registry | `google_iap_agent_registry_iam_member` (7.39.0); `google_iap_agent_registry_{agent,mcp_server,endpoint}_iam_member` (**7.40.0**) | — | Additive, with `condition {}` support. Use only for the **base** platform-endpoint grant (§2) |
| Agent Registry binding (auth provider) | `google_agent_registry_binding` (7.39.0), `google_agent_identity_auth_provider` (7.42.0) | — | Not used by the demo today |
| Cloud Run v2 | `google_cloud_run_v2_service` | 4.46.0 | Supported, but kept in the CLI (§2.3) |
| **Agent Runtime engine** | `google_vertex_ai_reasoning_engine` | 7.6.0. `identity_type` and `container_spec` 7.28.0; `psc_interface_config` 7.17.0; **`deployment_spec.agent_gateway_config` 8.1.0** | See §1.4. Kept in the CLI |
| Engine ↔ gateway binding on an engine Terraform doesn't own | none | — | **Unsupported.** Fallback: `terraform_data` + local-exec PATCH (`agdemo_core/terraform.py` and Policy Studio `render.py` already emit this). It's a live toggle, so it isn't in the foundation at all |
| Gemini Enterprise agent registration | none. `google_discovery_engine_assistant` and `_search_engine` exist, but nothing registers an ADK/A2A agent in an app | — | **Unsupported.** Stays `./agdemo publish-ge` (agents-cli) |
| Gateway service agent identity (`gcp-sa-agentgateway`) | `google_project_service_identity` is **google-beta only** (not in the GA 8.6.0 schema) | — | See Q7. In the demo project the agent already exists with `roles/agentgateway.serviceAgent` |

`google-beta` adds nothing relevant beyond GA. Its 8.x "New Resource" entries for agents are Gemini data-analytics agents and settings, plus the same connectivity template.

### 1.2 Agent Gateway: update vs replace

Evidence: `resource_network_services_agent_gateway.go` at v8.7.0.

- **ForceNew (replace):**
  - `google_managed` / `self_managed` (and `governed_access_path`);
  - `project`;
  - **`network_config.egress.network_attachment`**.
- **Updated in place by PATCH:** `description`, `registries`, `labels`, `agent_connectivity_template`, `network_config` (other parts), `protocols` (deprecated since 7.37.0; leave it unset).
  - The mask contains only the fields that changed.
  - So a change to `agent_connectivity_template` alone sends `updateMask=agentConnectivityTemplate`. On a gateway that has no template that's the **bare → template PATCH, which fails with code 13 and drops the labels** (P0 T2 #1).
- **`agent_connectivity_template`** has `DiffSuppressFunc: ProjectNumberDiffSuppress`, so the number form and the id form compare equal. Still write the **number** form: the API rejects the id form on create (P0 T1).
- **Create and delete retry `IsAgentGatewayInUseError`** (`"is already being used by resource"`), with 30 min timeouts. So `terraform destroy` on a gateway that engines still use doesn't fail fast; it **retries for up to 30 minutes, then fails**. That's why there's a guard (§4.4).
- **`deletion_policy`** is available on almost every resource since 7.33.0: `DELETE` (default), `PREVENT` or `ABANDON`.

**Consequences:**
1. A gateway created by Terraform **with** the template from the start is fine (P0 T1 create path).
2. Moving a **bare** gateway to private egress through Terraform is unsafe either way:
   - adding `network_config` plans a **replace** (ForceNew attachment);
   - setting only `agent_connectivity_template` sends the failing PATCH.

   So it's a one-time CLI migration, protected by a precondition (§5.3).
3. Template → template swaps are in place (11 s, P0 T2 #5).

### 1.3 Connectivity template: frozen while in use

- **Provider ForceNew:** `access_path`, `agent_connectivity_template_id`, `egress_network_config.network_attachment`, `tls_config` (and its `additional_roots`, `trust_config`).
- **Updatable as far as the provider is concerned:** `egress_network_config` (DNS, `vpc_egress`), `deployment_model`, `labels`, `description`.
- **But the API refuses** any update except `description` while a gateway references the template (code 9 `FAILED_PRECONDITION`), and refuses delete too (400 "already being used").

**Pattern:**
- Name the template from a hash of its content, e.g. `agdemo-tmpl-${substr(sha1(jsonencode(local.tmpl)), 0, 6)}`.
- Use `lifecycle { create_before_destroy = true }`.

Any content change then:
1. creates a new template;
2. updates the gateway in place (template swap, 11 s);
3. deletes the old one.

Labels and description changes stay in place.

### 1.4 Agent Runtime engine

- The resource is the whole engine: `package_spec` (pickle URIs), or `source_code_spec`, or `container_spec`, plus `identity_type`, `env`, `agent_gateway_config` and `psc_interface_config`.
- Update uses `updateMask=spec` (the whole spec), so any spec diff redeploys the engine.

The demo builds engines with the vertexai SDK (`AdkApp`, pickled to the staging bucket), and the UI PATCHes `agentGatewayConfig` live. Managing engines in Terraform would need all of these:
- `lifecycle.ignore_changes = [spec[0].deployment_spec[0].agent_gateway_config]`;
- the pickles produced outside Terraform anyway;
- an import with a byte-exact spec.

That's all cost and no benefit, so engines stay in the CLI. If this is ever revisited, `ignore_changes` on `agent_gateway_config` is mandatory, or Terraform unbinds the theme on every apply.

---

## 2. Split: Terraform vs CLI

**Rule:** Terraform owns what must exist before the first `deploy-theme` and never changes during a demo. Anything the UI, Policy Studio or a code build changes is outside Terraform. Otherwise every `apply` would undo the presenter's state ("Terraform fights the UI").

### 2.1 Terraform (`base` module)

| Resource | Today created by | Notes |
|---|---|---|
| APIs (`REQUIRED_APIS` in `agdemo_core/gcp/project.py`, 21 services) | bootstrap step 1 | `disable_on_destroy = false` |
| SAs `agdemo-ui`, `agdemo-targets` + their project roles; presenters' `aiplatform.user` | step 2 | `_member` only |
| AR repo `agdemo`, staging bucket `<project>-agdemo-staging` | step 3 | UBLA on |
| Gateways `agdemo-egress` (AGENT_TO_ANYWHERE, `registries` = the regional registry), `agdemo-ingress` (CLIENT_TO_AGENT) | step 4 | Labels `app`, `demo-prefix` (the live gateways carry both). Egress in `var.gateway_project_id` |
| IAP authz extension `agdemo-egress-iap-authz` + `REQUEST_AUTHZ` policy `agdemo-egress-iap-policy` | step 5 | `ignore_changes = [fail_open, metadata]` on the extension (Policy Studio's Enforce/Dry run toggle flips them, ISSUES #13). Q1 |
| Model Armor template `agdemo-shield` + extension `agdemo-ma-authz` (in each gateway project) | step 6 | The extension's `metadata.model_armor_settings` is a JSON **string**: build it with `jsonencode` and match the live bytes on import |
| Model Armor roles (`modelarmor.calloutUser`, `modelarmor.user`, `serviceusage.serviceUsageConsumer`) for `service-<N>@gcp-sa-dep`, `service-<N>@gcp-sa-aiplatform-re` and **each gateway's** `agent_gateway_card[0].service_extensions_service_account` | step 6 | Use static `for_each` keys (`egress`, `ingress`) with values from the gateway, so unknown values at plan time don't break `for_each` |
| 25 platform entries `agdemo-plat-*` (`google_agent_registry_service`, `endpoint_spec NO_SPEC`) + `google_iap_agent_registry_endpoint_iam_member` `roles/iap.egressor` for the project Agent Identity principalSet | step 7 | This is base: under default deny the Runtime needs it before any theme. The URL list comes from `common.platform_endpoints(cfg)` through the generated tfvars. URLs already registered by someone else (bootstrap's `url_owner` branch) are read with the `google_agent_registry_endpoint` data source, not created |
| Guard resource (§4.4) | new | — |

### 2.2 Terraform (`private_egress` module, `count = var.private_egress_enabled ? 1 : 0`)

| Resource | Notes |
|---|---|
| VPC `agdemo-vpc` (custom mode), subnet `agdemo-att` (/28, PGA) | `10.70.0.0/28` default, variable |
| Network attachment `agdemo-att` (ACCEPT_AUTOMATIC) | **The gateway's region** (F9). Its id uses the project **id** |
| Global address `agdemo-psc-ip` + forwarding rule `agdemopsc` (all-apis) | PSC IP variable, default `10.70.250.10` |
| Private zone `agdemo-run-app` (`run.app.`) + apex and wildcard A records | — |
| `gcp-sa-agentgateway` roles: `roles/compute.networkAdmin` + `roles/dns.peer` | Verified sufficient in P0 (F11). The custom role (`compute.networkAttachments.get/update`, `compute.regionOperations.get`) is an untested option (Q6). **The gateway `depends_on` these**, so they're removed only after the gateway is gone |
| Connectivity template `agdemo-tmpl-<hash>` | `access_path = AGENT_TO_ANYWHERE`, `deployment_model = CENTRALIZED`, `egress_network_config { network_attachment = <attachment id, project ID form>, vpc_egress = PRIVATE_RANGES_ONLY, dns_peering_config { domains = ["run.app."], target_network = <network id, project ID form> } }` |
| Egress gateway `agent_connectivity_template` | Set in `base` from the module output, in the number form `projects/${data.google_project.this.number}/locations/${var.region}/agentConnectivityTemplates/${id}`. Enabled only when `var.egress_template_attach` is true (§5.3) |

Outputs: `network`, `attachment`, `psc_ip`, `template`, plus `private_egress_enabled` for the CLI.

### 2.3 CLI (unchanged ownership)

| Thing | Why not Terraform |
|---|---|
| Images (Cloud Build) | Code builds |
| Theme Cloud Run services `agdemo-<theme>-<component>` | They need built images; their URLs feed the registry entries and the engine env. `deploy-theme` already handles it idempotently |
| **Private MCP server** (internal ingress, `--ingress=internal`) | Same as the theme services: a theme component with `ingress: internal` (Q5). Deployed only when `private_egress_enabled` |
| Theme registry entries (MCP tool specs read from the code; A2A cards fetched from the running service) | They depend on deployed code; deleted per theme |
| Agent Runtime engines | §1.4. Engines pin `google-auth==2.60.0`, which avoids the 401 behind a gateway (F44, ISSUES #16: a google-auth bug). The `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false` env var this row used to describe was removed on 2026-10-22 |
| UI Cloud Run `agdemo-ui` + IAP | It needs the image and state.json as env vars, and `ui deploy` is re-run after every theme deploy |
| GE registration (`publish-ge`) | No provider support |
| **Live toggles:** gateway bindings, IAP grants on theme entries (including the private-MCP grant), Model Armor `CONTENT_AUTHZ` policies, enforce/dry run | They change during demos. Never in Terraform |

**Out of scope (owned by the policy-studio repo's `deploy.sh`):**
- the `policy-studio` SA, the custom role `policyStudioIapReader`, the `policy-studio` AR repo and the Cloud Run service.

**Legacy resources Terraform won't adopt** (deleted by a one-off CLI cleanup; Q4):
- `agdemo-ingress-iap-authz` / `-policy`;
- `agdemo-denied-caller` / `agdemo-allowed-caller`.

### 2.4 State, variables, outputs

**State backend:** GCS, with a bucket Terraform doesn't manage. This avoids the chicken-and-egg problem, and `down` can never delete its own state.
- `./agdemo tf init` creates `gs://<project>-agdemo-tfstate` with gcloud if it's missing (idempotent): location = region, UBLA, **object versioning on**, labels.
- It then runs `terraform init -backend-config=bucket=… -backend-config=prefix=agdemo/<project>/<region>`.
- The prefix keys each environment, so tfvars plus a state prefix replace workspaces. That's clearer and can't silently point at the wrong state.
- The alternative, a separate `infra/terraform/state` root with local state, was rejected: it adds a second state to keep.

**Variables:** `./agdemo tf vars` writes `infra/terraform/generated.auto.tfvars.json` (git-ignored) from `config/demo.yaml` through `load_config()`. Sources:
- project, gateway project, region, prefix and labels;
- gateway names, IAP enforcement (initial value only) and the Model Armor template id and filters;
- `ui.admin_access`;
- the platform endpoint URL list;
- `private_egress.{enabled, cidr, psc_ip, template_attach}`, a new demo.yaml block.

There's one source of truth, and Terraform never reads YAML itself.

**Outputs:** `./agdemo up` runs `terraform output -json` and merges it into `state.json shared` **with the same keys bootstrap writes today**:
- `ui_sa`, `targets_sa`, `artifact_repo`, `staging_bucket`;
- `egress_gateway`, `ingress_gateway`, `ingress_gateway_url`, `gateway_service_agents`;
- `egress_iap_extension`, `egress_iap_policy`;
- `model_armor_template`, `model_armor_extension`;
- `platform_registry_ids`.

New keys: `private_egress_*`. The UI and the policy handlers don't change.

---

## 3. Layout

```
infra/terraform/
  versions.tf          terraform >= 1.7 (for_each import blocks); google >= 8.7.0, < 9.0; http ~> 3.4
  backend.tf           backend "gcs" {}   (partial config from ./agdemo tf init)
  main.tf              data.google_project, data.google_client_config; module "base"; module "private_egress"
  variables.tf / outputs.tf
  guards.tf            terraform_data.teardown_guard (§4.4); data "http" gateway read for the precondition (§5.3)
  imports.tf           generated once by ./agdemo tf import (§5), deleted after the migration
  modules/base/        apis.tf  iam.tf  storage.tf  gateways.tf  authz.tf  model_armor.tf  registry.tf
  modules/private_egress/  network.tf  psc.tf  dns.tf  service_agent.tf  template.tf
```

- **One root, two modules, not two roots.** The egress gateway (base) references the template (private). Separate states would need `terraform_remote_state` and a manual apply order, and destroy order could go wrong. The toggle is a `count`.
- The cross-project egress gateway (`gateway_project_id`) is handled with the `project` attribute on each resource. No second provider alias is needed.

---

## 4. Lifecycle

### 4.1 `./agdemo up`

1. `preflight` (existing).
2. `tf init`: state bucket, tfvars, `terraform init`.
3. `terraform plan -out` → the plan-safety check (§6: no delete or replace unless `--allow-replace`) → `terraform apply`.
   - APIs come first (`depends_on`).
   - Gateways and private networking run in parallel. The gateways take about 3.5 min, with the template already attached.
   - Then the IAP policy (about 3 min), the Model Armor extension, the registry entries and the IAM.
4. Merge the outputs into `state.json`.
5. `deploy-theme <t>` for each `themes.enabled`. Run them in parallel per theme, or keep them sequential to start with. Per theme: images, then Cloud Run (with the private MCP server when enabled), then the registry, then the engines (with the env var when private).
6. `ui deploy`. Optionally `publish-ge` (`--ge` flag).
7. `status` / Verify.

**Re-running `up`:** Terraform is a no-op, and `deploy-theme --skip-build` is idempotent.

### 4.2 `./agdemo reset --all` (new global reset; keeps all base and code)

1. For each theme in `state.json`: the existing `reset(theme, wait=True)`. It removes grants and bindings and waits for the engine PATCHes. One PATCH per engine, as in the reset-race fix.
2. Model Armor off: delete `agdemo-{egress,ingress}-ma-policy` (the existing handler).
3. Policy Studio leftovers: authz policies or extensions that target or copy our gateways and aren't in Terraform state. Example: the "give this gateway its own extension" copy.
   - List them, delete only prefix-owned ones, and report the rest.
4. IAP grants on **platform** entries that aren't the base principalSet: report only.

### 4.3 `./agdemo down`

1. **Confirm**, and check that nobody is presenting (working agreement). The UI's busy flag, or ask.
2. `reset --all` (§4.2).
3. CLI teardown of code-deployed things (extending the existing `teardown_theme` / `teardown_shared`):
   1. GE registrations: unregister the demo's agents from the GE app. That's new; `teardown` doesn't do it today (Q9).
   2. Engines (`force=true`), plus their project IAM principals. Wait for the delete LROs.
   3. Theme Cloud Run services and their registry entries (their IAP policies go with them), including the private MCP server.
   4. UI Cloud Run.
   5. Clear `state.json themes`.
4. `./agdemo tf check-clean` (read-only) must pass:
   - no engine in the region with an `agentGatewayConfig` pointing at our gateways (any owner);
   - no authz policy targeting our gateways other than the Terraform-managed IAP policy;
   - no `agdemo-*` registry services except the Terraform-managed `plat-*` entries;
   - no `agdemo-*` Cloud Run services.
5. `terraform destroy`. Terraform's graph gives this order:
   1. guard (runs the check again);
   2. IAM members, then registry platform entries;
   3. IAP policy, then extensions; Model Armor template;
   4. **gateways** (about 5 min with a template);
   5. **template**, after the gateway stops referencing it;
   6. **attachment**: the provider retries the 412 for up to 20 min; about 2.5 min was observed;
   7. the `gcp-sa-agentgateway` roles (via the gateway's `depends_on`); records, then zone; forwarding rule, then address; subnet, then VPC;
   8. SAs, repo, bucket.

   APIs stay enabled.
6. Clear `state.json shared`. The state bucket is **kept**.

**Timing estimates:**
- `down`: reset 3–5 min (detach about 2.5 min); engine deletes 2–5 min; Cloud Run under 1 min each; destroy about 10–15 min. **About 20–30 min total.**
- `up` from scratch: Terraform about 8–12 min; images 5–8 min; Cloud Run about 1 min × 8; engines 4.5–7 min each × 4 (sequential today). **About 40–55 min, or about 25–30 min if engines deploy in parallel.**

### 4.4 Running `terraform destroy` without a reset

`terraform_data.teardown_guard`:
- `input` references both gateway ids, the extension ids, and the path of `./agdemo`;
- it has a `when = destroy` local-exec running `./agdemo tf check-clean --strict`.

Because the guard **depends on** the gateways, Terraform destroys it **first**. (As built, it depends on all of `module.base`, §7e: depending only on the gateways would let the IAM, registry and other deletes run in parallel with the guard.) A non-zero exit aborts the destroy before anything is deleted. The message names what's still bound or attached and says "run ./agdemo down (or reset --all)".

Caveats:
- `-target` or `terraform state rm` can bypass the guard.
- Destroy provisioners don't run if the guard is tainted.
- An aborted destroy still removes the root outputs from state (Terraform drops them at the start of every destroy). `down` then reads the environment from the guard's recorded `input.environment` instead (§7i); `./agdemo tf apply` (no resource changes) writes the outputs back.
- Without the guard, the provider's in-use retry would keep the gateway delete going for up to 30 min, then fail with an unclear error.

`deletion_policy = "PREVENT"` on the gateways was considered and rejected: it would need an extra apply to flip it before every legitimate `down`.

**Idempotency:**
- Every CLI delete treats 404 as done.
- `check-clean` is read-only.
- A half-finished `destroy` can be re-run; the guard re-checks first.
- A half-finished `up` can be re-run.

---

## 5. Migrating the live demo into Terraform

**Rule:** nothing is recreated. Any plan with `delete`, or with `create` on an imported address, stops the migration.

### 5.1 Steps

1. **Snapshot** (read-only):
   - `./agdemo status`;
   - GETs of every resource below, saved to `config/generated/tf-migration/` (git-ignored);
   - the IAM policies of the project and of each platform endpoint.
2. **Legacy cleanup** (Q4, after asking): delete `agdemo-ingress-iap-policy`, then `agdemo-ingress-iap-authz`, and the two legacy SAs.
3. **`./agdemo tf import`** writes `imports.tf` from `state.json` plus the live lists:

   | Address | Import id |
   |---|---|
   | `module.base.google_network_services_agent_gateway.gw["egress"]` / `["ingress"]` | `projects/acme-prod/locations/us-east4/agentGateways/agdemo-egress` (and `-ingress`) |
   | `…authz_extension.egress_iap`, `…authz_extension.model_armor["acme-prod"]` | `projects/acme-prod/locations/us-east4/authzExtensions/agdemo-egress-iap-authz` / `agdemo-ma-authz` |
   | `…authz_policy.egress_iap` | `projects/acme-prod/locations/us-east4/authzPolicies/agdemo-egress-iap-policy` |
   | `…model_armor_template.shield` | `projects/acme-prod/locations/us-east4/templates/agdemo-shield` |
   | `…google_service_account.sa["ui"]` / `["targets"]` | `projects/acme-prod/serviceAccounts/agdemo-ui@acme-prod.iam.gserviceaccount.com` |
   | `…artifact_registry_repository.agdemo` | `projects/acme-prod/locations/us-east4/repositories/agdemo` |
   | `…storage_bucket.staging` | `acme-prod-agdemo-staging` |
   | `…agent_registry_service.platform[<url>]` (`for_each` import, 25) | `projects/acme-prod/locations/us-east4/services/agdemo-plat-<x>` |

   **Not imported, because creating them is a no-op:** project services, `google_project_iam_member` and `google_iap_agent_registry_endpoint_iam_member`. These are additive. The plan shows them as "create", which is expected; the safety check allows creates of `_iam_member` and `google_project_service` only.

   **Not imported at all:**
   - Model Armor policies (UI);
   - theme registry entries, engines and Cloud Run (CLI);
   - policy-studio resources.
4. **Iterate** until `terraform plan` shows **25+N to import, 0 to change, 0 to destroy**, plus only the allowed IAM and API creates. Diffs to expect and match in the config:
   - gateway labels (`app`, `demo-prefix`);
   - the Model Armor extension's `model_armor_settings` string, byte for byte;
   - extension `timeout` and `fail_open`;
   - the platform entries' `display_name` and `description` (bootstrap uses `"<prefix> <host>"` and a fixed description);
   - Model Armor template filter levels and the custom block message;
   - the IAP policy's labels. Policy Studio's earlier PATCH bug once cleared them; adding them back is a harmless in-place change.
5. Apply in a quiet window (it touches nothing live). Commit the config; delete `imports.tf`.
6. `./agdemo bootstrap` becomes a thin alias that prints "use ./agdemo up". Keep the old code path behind `--legacy` for one release.

### 5.2 Private egress on the live `agdemo-egress` (in place)

Can Terraform do the two-step (bare → inline `networkConfig` → template)? **No, and it shouldn't try.**
- **Step 1 (add an inline `network_config`):** the provider marks `network_config.egress.network_attachment` ForceNew. The plan would be a **replace** of the gateway: delete it (blocked or retried while engines are bound), then recreate it. The IAP policy target and every binding would be lost.
- **Step 2 (inline → template), and the direct bare → template:** Terraform would send `updateMask=agentConnectivityTemplate` (plus `networkConfig` only if that also changed). Bare → template fails with code 13 and drops the labels (P0 T2).
- A `terraform_data` + local-exec running the two PATCHes would work. But it's one-time, order-sensitive and needs a reset first, so it belongs in the CLI.

**Procedure:**
1. Apply with `private_egress.enabled: true` and `template_attach: false`. This creates the VPC, subnet, attachment, PSC endpoint, DNS, service agent roles and template. **The gateway isn't touched.**
2. `./agdemo reset --all`, at a time when the user isn't presenting. Whether in-place PATCHes disrupt bound traffic is untested, so nothing is bound.
3. `./agdemo private migrate-gateway`:
   1. PATCH `?updateMask=networkConfig,labels` with the inline egress attachment, `dnsPeeringConfig {domains: [run.app.], targetProject: <project id>, targetNetwork: <network id>}` and the current labels. Wait about 3.5 min.
   2. PATCH `?updateMask=agentConnectivityTemplate,networkConfig,labels` with the template in the **number** form and `networkConfig` omitted. Wait about 3 min.
   3. Re-read and verify: template set, `networkConfig` null, labels present.
   4. Record `private_egress_migrated: true` in `state.json`.
4. Set `template_attach: true` and plan. **The expected plan is 0 to change** (DiffSuppress compares number and id equal), so apply is a no-op.
5. Redeploy the engines with the env var: `deploy-theme <t> --only engine --update-engine` pushes the new env. Then deploy the private MCP server and register it (both run.app URL forms).
6. Test, as in TESTBED step 10: 403 → grant → about 2 min → 200, with `serverIp` = the PSC IP.

### 5.3 Guard against Terraform doing the bad PATCH

A `lifecycle.precondition` on the egress gateway:

```hcl
data "http" "egress_gw" {
  url             = "https://networkservices.googleapis.com/v1/${local.egress_gateway_id}"
  request_headers = { Authorization = "Bearer ${data.google_client_config.this.access_token}" }
}
# precondition (only when var.egress_template_attach is true):
#   the live gateway already has agentConnectivityTemplate, or it is being created (404),
#   else: "agdemo-egress has no template: run ./agdemo private migrate-gateway first"
```

The provider has no gateway data source, so this reads it with the `http` provider (read-only) and `try()` around the JSON. Fresh projects (404 at plan time) go straight to "create with template", which is the verified path.

**Turning private egress off later:**
- template → none is a PATCH with `agentConnectivityTemplate` cleared. That's **unverified**.
- Until it's tested, `private_egress.enabled: false` on a migrated gateway is refused by a second precondition, which says to use `down`/`up` or a CLI command after testing.

---

## 6. Testing

- **CI** (GitHub Actions, no GCP credentials):
  - `terraform fmt -check`;
  - `terraform init -backend=false`;
  - `terraform validate` with `private_egress_enabled` true and false;
  - `tflint` (optional).
  - Provider cached; Terraform 1.7+ (1.15 is local).
- **Python unit tests** (`ui/backend/tests` style, mocked REST):
  - the tfvars generator (demo.yaml → JSON);
  - the outputs → state.json merge (same keys as bootstrap);
  - the `imports.tf` generator;
  - `check-clean` (each blocker case);
  - `migrate-gateway` (mask, labels re-sent, number vs id);
  - the env-var injection in `deploy-theme`.
- **Plan-safety check** (`agdemo_core/tf_safety.py`): reads `terraform show -json plan`. It fails on any `delete`, `["delete","create"]` or `["create","delete"]` action, except template `create_before_destroy` swaps when `--allow-template-swap` is passed. Tested on fixture plan JSONs. `up` runs it every time; the migration requires it.
- **Read-only plan against the live demo** (local, user credentials) is the migration gate (§5.1 step 4).
- **End-to-end in a throwaway region:**
  - Same project, region `us-west1` (as P0). Not `us-central1` (another team's gateway) and not `us-east4` (the live demo; the one-egress-gateway-per-project+region rule, F40).
  - Prefix `agdtest`, its own state prefix.
  - Sequence:
    1. `up` with private egress;
    2. retail scenarios + private MCP 403/200 + `serverIp` check;
    3. `down`;
    4. a verify-clean listing (every resource type, no `agdtest` matches; service agent IAM back to the pre-test state);
    5. a **second** `up`/`down` for idempotency.
  - Negative tests:
    - `terraform destroy` with an engine still bound → guard aborts and nothing is deleted;
    - `apply` with `template_attach: true` on a bare gateway → the precondition fails.
  - It's also the start of the HANDOFF "portability in a fresh project" item. A run in a truly fresh project is the follow-up that settles Q7.
- **Cost:**
  - E2E run of about 1.5–2 h: under about USD 10. P0 was under USD 5 with 5 engines; Agent Gateway pricing wasn't checked.
  - Standing private egress: about USD 8/month (the PSC endpoint) + USD 0.20/month per zone.
  - The base has no new standing cost; the UI's always-on Cloud Run is unchanged.

---

## 7. Phases and effort

| Phase | Content | Size |
|---|---|---|
| T1 **(built)** | Scaffold: `versions`, backend + `tf init` (state bucket), tfvars generator, `base` module for a **fresh** environment, outputs → state.json, CI validate, unit tests | 2–3 d |
| T2 **(read-only part built; live steps in the runbook)** | Import the live demo: legacy cleanup (after asking), `tf import` generator, plan-safety check, iterate to a zero-diff plan, apply, deprecate `bootstrap` | 1–2 d |
| T3 **(built; live runs pending)** | Lifecycle: `up`, `reset --all`, `down`, `check-clean`, guard resource, teardown additions (GE unregister, Policy Studio leftovers) | 2 d |
| T4 **(built; live runs pending)** | Per §7c: `private_egress` + a second-region `regional` module, region-aware themes, env var in `deploy-theme`, private MCP component (internal ingress, both URLs registered), the `privnet` theme, status/UI showing private egress. No `migrate-gateway` (dropped in §7b) | 2–3 d |
| T5 | E2E up/down ×2 in us-west1 + negative tests, then the in-place migration of `agdemo-egress` (§5.2) in a quiet window; docs (SETUP, TROUBLESHOOTING, README, TESTBED pointer) | 1–2 d |

**Total about 8–12 days.** T1 and T2 deliver value without T4. T4's live migration must wait for T5's throwaway run.

## 7a. T1 as built (2026-10-16)

**What exists:**
- `infra/terraform/`: `versions.tf` (Terraform >= 1.7, google >= 8.7.0, < 9.0; lock file committed, 8.7.0), `backend.tf` (partial `gcs`), `main.tf`, `variables.tf`, `outputs.tf`, and `modules/base/` (`apis.tf`, `iam.tf`, `storage.tf`, `gateways.tf`, `authz.tf`, `model_armor.tf`, `registry.tf`, `outputs.tf`).
- `agdemo_core/tf_foundation.py`: the tfvars generator, the outputs → state.json merge, the CLI-owned buckets, and the terraform wrapper.
- `agdemo_core/tf_safety.py`: the plan-safety check (§6). `python -m agdemo_core.tf_safety plan.json` or `./agdemo tf check-plan`.
- CLI: `./agdemo tf vars | init [--local-state PATH] | plan | apply | output-sync [--state PATH] | check-plan`. `bootstrap` is unchanged.
- Tests: `ui/backend/tests/test_tf_foundation.py` (tfvars, merge, plan-safety on fixture plans, `terraform fmt`, no real identifiers in the Terraform files).

**Read-only plan, fresh environment** (same project, us-east1, prefix `agdtest`, local scratch state, `shared_project_grants: false`): **99 to add, 0 to change, 0 to destroy**; plan-safety OK. With `shared_project_grants: true` it's 106 (+1 presenter grant, +6 static Model Armor service-agent grants). No apply was run.

| Resource | Count (99 plan) |
|---|---|
| `google_project_service` | 21 |
| `google_service_account` | 2 |
| `google_project_iam_member` | 17 (UI SA 9, targets SA 2, Model Armor roles × 2 gateway service agents 6) |
| `google_artifact_registry_repository`, `google_storage_bucket` (staging), `google_storage_bucket_iam_member` (recordings) | 1 each |
| `google_network_services_agent_gateway` | 2 |
| `google_network_services_authz_extension` (IAP, Model Armor) | 2 |
| `google_network_security_authz_policy` (REQUEST_AUTHZ) | 1 |
| `google_model_armor_template` | 1 |
| `google_agent_registry_service` (platform) + `google_iap_agent_registry_endpoint_iam_member` | 25 + 25 |

**Deviations from the plan:**
1. **Recordings bucket** (§7b): created by the CLI (`tf apply`, through the API, like the state bucket), never by Terraform, so no `destroy` can delete it. Terraform only grants the UI SA `roles/storage.objectUser` on it. New `state.json shared.recordings_bucket`; `ui deploy` uses it when present, else `staging/recordings` as before. Moving the existing recordings is T2.
2. **Plan-safety check** is built now (the table had it in T2). `tf plan` and `tf apply` run it.
3. **State prefix** is `<resource_prefix>/<project>/<region>` (not a fixed `agdemo/…`), so two prefixes in one project and region can't share a state. The bucket stays `<project>-agdemo-tfstate`.
4. **Names come from Python.** The `names` and `platform_endpoints` variables are computed with bootstrap's own helpers (`cfg.name`, `common.platform_service_id`, …), so naming has one source.
5. **URLs registered by someone else** (bootstrap's `url_owner` branch): the CLI lists the registry (read-only) and passes `platform_endpoints_existing` (url → registry resource). They're granted, not created. No data source is used.
6. **`ignore_changes`**: `[fail_open, metadata]` on the IAP extension and `[custom_provider]` on the IAP policy (§8 Q1, both halves).
7. **Provider `add_terraform_attribution_label = false`**, so the labels match bootstrap exactly (no `goog-terraform-provisioned` diff at import).
8. **New config `terraform.shared_project_grants`** (default true). Set it to false for a second environment in a project that already has one: then presenters' `aiplatform.user` and the static Model Armor service-agent grants aren't managed, so that environment's destroy can't strip them from the first.
9. **Apply guard:** `tf apply` refuses when the egress gateway exists in GCP but isn't in the state (a bootstrap-created environment, i.e. the live demo before T2). `tf plan` warns.
10. **The Model Armor extension's `model_armor_settings`** is built as a literal string with bootstrap's `json.dumps` separators (`", "`, `": "`), not `jsonencode`, for a byte-exact T2 import.
11. **CI:** the repo has no workflow. `terraform fmt -check` runs inside pytest; `terraform validate` was run by hand.
12. **Private Networking (T4):** a `private_networking` variable that must stay disabled (validation), plus marked placeholders. Nothing else.

**Bootstrap behaviour Terraform doesn't express:**
- **Best-effort Model Armor grants.** bootstrap logs and continues when a service agent can't be granted. Terraform fails the apply. In a truly fresh project, `gcp-sa-aiplatform-re` (and possibly `gcp-sa-dep`) may not exist yet. Mitigations: `depends_on` the gateways is in place; `model_armor_static_agents` can be trimmed. Settle with Q7.
- **`gateways.create: false`** (reuse existing gateways): not supported. Use import (T2).
- **"registryResource not ready, re-run":** Terraform reads `registry_resource` after create. If the API returned it empty, the egressor grant would fail; re-run apply. Not observed (no apply yet).
- **`_member` grants aren't reference-counted.** Two environments in one project can share a principal: the ingress gateway's service agent is the project's `gcp-sa-aiplatform-re`. Destroying one environment then removes a role the other still uses, even with `shared_project_grants: false`. For T3: `down` (or `check-clean`) should re-check the remaining environment's Model Armor roles.

---

## 7b. Decisions (user, 2026-10-16)

- **Q3, recordings:** keep them so they survive `down`. Move `recordings/` to a separate bucket that is **not** destroyed with the foundation. It's created outside the destroyable module, like the state bucket, or marked `prevent_destroy` and left out of `down`. The UI reads recordings from that bucket.
- **Q4, legacy cleanup:** yes. Delete the legacy `agdemo-ingress-iap-*` resources and the two unused caller service accounts before importing.
- **Q11, Terraform 1.7+:** yes, it's acceptable.
- **Q5a, private MCP:** Helpdesk theme, "Payroll MCP" (internal-only Cloud Run).
- **Q5b, gateway approach:** a **dedicated Private Networking mode in a second region (us-west1)**. `agdemo-egress` is **not** changed, so the one-time migration in §5 (`private migrate-gateway`) is dropped. See §7c.

## 7c. Private Networking mode (user decision, 2026-10-16)

**Why a second region:** a project can have only one egress gateway per region serving agents, and an Agent Runtime agent must be in its gateway's region. The VPC-enabled gateway and its agent therefore live in a **second region, us-west1**. us-central1 holds an unrelated gateway.

| Mode | Region | Gateway | Content |
|---|---|---|---|
| Internet (today) | us-east4 | `agdemo-egress` (no template, unchanged) | Existing themes, public Cloud Run destinations |
| Private Networking (new) | us-west1 | `agdemo-pn-egress`, **created with the connectivity template from the start** (verified path, no migration) | New theme "Private networking": a dedicated orchestrator, **Payroll MCP** (Cloud Run, ingress internal) and **one public MCP** for side-by-side contrast |

**Terraform:** `base` gains a second-region instance. The `private_egress` module is always on in us-west1. Contents:
- VPC, /28 subnet (PGA), attachment;
- PSC endpoint for Google APIs and the `run.app.` zone;
- service-agent roles and the template;
- `agdemo-pn-egress` referencing the template by project number;
- that region's IAP extension and policy, Model Armor template and extension;
- the 25 platform registry entries for us-west1, with their grant.

It's still created by `up` and destroyed by `down`, with the same guard and order. The us-east4 base is unchanged.

**CLI:**
- **The theme carries its region.** `deploy-theme`, policies, gateway logs and Verify follow the theme's region instead of the global config region.
- **Deploys:** Payroll MCP (internal ingress) and the public MCP. The theme's orchestrator is deployed like any other (google-auth pinned, ISSUES #16).
- **Registry and grants:** the theme's registry entries are created in us-west1. Grants stay live toggles.

**Demo UI:**
- a **mode switch** (Internet / Private Networking) that selects the theme set and region;
- a VPC diagram view (agent → VPC-enabled gateway → attachment / PSC endpoint / DNS → Payroll MCP, with the public MCP alongside);
- an "Under the hood" panel showing the pre-built network pieces and the template;
- gateway log entries highlight a private `serverIp`.

**Demo steps in Private Networking mode** (toggles and tests):
1. Payroll MCP is internal-only: a direct call is rejected with 404.
2. Through the gateway: there's a route and both servers are registered, but no grant, so 403. A route and a registry entry aren't permission. (The former separate "registered" step was dropped on 2026-10-20: it showed the same state as this one.)
3. Grant Payroll (optionally read-only tools): 200, and the gateway log shows the private 10.x IP. With read-only, the write tool stays 403.
4. Contrast: the public MCP follows the same policy. It is also reachable from the internet directly, but through the gateway it takes the private path too (run.app is DNS-peered; verified live, §7h).
5. Optional: the diagram explains a "direct VPC access" agent (PSC interface) that bypasses the gateway. It's explained only, not deployed.

**Effort:** about 3–4 days on top of T1–T5 (region-aware themes, the mode switch, the new theme and scenarios, the VPC diagram, log highlight). T4 changes accordingly: no gateway migration, so the second-region module plus the theme.

**Standing cost:** PSC endpoint about USD 8/month, plus the extra orchestrator's idle cost and the second gateway. Gateway pricing isn't checked.

## 7d. T2 read-only part (2026-10-16)

Live steps: [TERRAFORM_MIGRATION_RUNBOOK.md](TERRAFORM_MIGRATION_RUNBOOK.md) (a legacy cleanup, b `tf init` on GCS, c plan + compare, d recordings copy, e apply + 0/0/0 plan, f UI redeploy, g status/Verify, h rollback). No GCP writes were made: snapshots, plans and checks only, with a local scratch state.

**What exists:**
- `agdemo_core/tf_import.py` + `./agdemo tf import [--dry-run]`. It writes `infra/terraform/imports.tf`, which is **generated and git-ignored**: it holds the real project id, and it's needed only until the first apply (the runbook deletes it after). Inputs: demo.yaml names (the same helpers as the tfvars), `state.json shared` (cross-checked; a mismatch is a warning, the config name wins) and read-only live lists. Only resources that exist live get an import; missing ones are reported and left to create. The legacy resources are never imported and are reported while they exist.
- **Imports (35):** 10 blocks (both gateways, the IAP extension and policy, the Model Armor template, the Model Armor extension per gateway project, both SAs, the repo, the staging bucket) plus one `for_each` import over the 25 platform entries (`module.base.google_agent_registry_service.platform[<url>]`, from `local.agdemo_platform_imports`). The real addresses differ from the §5.1 table in two names: `…artifact_registry_repository.repo` and `…model_armor["<project>"]`.
- **Migration-mode plan-safety** (`tf_safety.check(migration=True)`, `--migration`; automatic in `tf plan`/`tf apply` while `imports.tf` exists): on top of no delete/replace, it refuses every create except `google_project_service` and the `*_iam_member` types, so a missing import can't recreate a live resource.
- **Apply guard:** `tf apply` accepts a live egress gateway that isn't in state when `imports.tf` imports it.
- `./agdemo tf recordings-bucket`: the recordings-bucket create that `tf apply` does, on its own, so the runbook can copy recordings before the apply. `tf plan --json PATH` saves the plan JSON.
- **`bootstrap` deprecation (offline-safe part):** it prints a deprecation notice, and refuses (exit 2) once `state.json shared.tf_environment` exists, i.e. after `output-sync` recorded that Terraform owns the environment; `--legacy` runs it anyway. The thin `./agdemo up` alias waits for T3, because `up` doesn't exist yet.
- Tests: `ui/backend/tests/test_tf_import.py` (generator, legacy exclusion, missing resources, cross-project, HCL rendering and `terraform fmt`, git-ignore, addresses exist in the module, migration mode, bootstrap refusal).

**Module fixes found by the live plan:**
1. **The ingress gateway has no `agent_gateway_card`** (CLIENT_TO_AGENT). T1 indexed `[0]` for both gateways, so the plan failed. Now only the egress gateway's service-extensions SA is a Model Armor grantee (the ingress path uses the static `gcp-sa-aiplatform-re` agent, as bootstrap does), and the output uses `try()`. A fresh environment's plan has 3 fewer grants than §7a's 99.
2. **`protocols`:** bootstrap sent `["MCP"]`; Terraform leaves the deprecated field unset, so the plan would PATCH it to `[]` on both live gateways. Now `ignore_changes = [protocols]`.

**Final read-only plan** (local state, generated imports, legacy still present and excluded): **35 to import, 68 to add, 8 to change, 0 to destroy, 0 replaces; plan-safety (migration mode) OK.** Saved in `config/generated/tf-migration/` (git-ignored): `plan.txt`, `plan.json`, `plan-actions.txt`, `snapshot/`.
- **68 creates, all additive and already live** (checked against the project policy, the enabled services and each platform entry's IAP policy), except one: 21 APIs; 21 project grants (UI SA 9, targets SA 2, presenter 1, Model Armor 3 roles × 3 agents); 25 `roles/iap.egressor` on the platform entries; **new:** the UI SA's `roles/storage.objectUser` on the recordings bucket.
- **8 in-place updates, none of which calls the API:** `labels`/`terraform_labels` on every imported labelled resource (an import leaves `labels` empty in state; `effective_labels`, which drives the update mask, is unchanged, and the provider skips the PATCH when the mask is empty), `timeouts` on the gateways and `force_destroy` on the staging bucket (both Terraform-only). They can't be removed without `ignore_changes = [labels]`, which would stop Terraform managing labels.
- `output-sync` after the apply changes only `recordings_bucket` (new) and `tf_environment` in `state.json shared`; every other key already equals the Terraform outputs.

**Legacy safety findings** (runbook step a): `agdemo-ingress-iap-policy` is attached to `agdemo-ingress` but CLIENT_TO_AGENT doesn't evaluate REQUEST_AUTHZ (§10 of ARCHITECTURE.md), and `agdemo-ingress-iap-authz` is referenced only by it. The two caller SAs have only Google-managed keys, project `roles/aiplatform.user`, and their own `serviceAccountTokenCreator` IAM; nothing runs as them and no IAP policy names them; their last use was the 2026-10-08 ingress test. The only code references are `teardown`'s cleanup by name. All four are safe to delete.

## 7e. T3 as built (2026-10-16)

No GCP writes were made: unit tests on fakes, plan-only Terraform runs with a local scratch state, and read-only lists/GETs (`check-clean` and `reset --all --dry-run` against the live demo).

**What exists:**
- `agdemo_core/infra/check_clean.py` + `./agdemo check-clean [--strict] [--expect P/R/X] [--json]`. `collect()` is read-only; `classify()` is pure. Levels:
  - **BLOCK** (exit 1 by default): engine bindings to this environment's gateways (any owner, id or number form), our engines, prefix Cloud Run services other than the UI, prefix registry entries other than the platform ones, any grant on them, grants beyond the platform egressor on platform entries, our principals in the region-wide registry policy, CONTENT_AUTHZ and other authz policies on our gateways, non-foundation prefix extensions and Model Armor templates.
  - **WARN** (exit 1 with `--strict`): the UI service, Gemini Enterprise registrations of our engines, foundation drift (IAP extension not in the demo.yaml enforcement mode, IAP policy pointed at another extension), legacy resources, prefix Cloud Run services without the label, a stale or foreign state.json, and every check that couldn't be read (strict can't pass blind).
  - **INFO** (never fails): grants that aren't ours in the region-wide policy, dangling GE registrations of deleted engines.
  - "Ours" = named `<prefix>-*` and, where the type has labels, `demo-prefix=<prefix>`; engines in state.json count too.
- `agdemo_core/infra/reset_all.py` + `./agdemo reset --all [--dry-run] [--yes]`: theme resets (all themes in state.json, gateway detaches started in parallel, one PATCH per engine), Model Armor off, then leftovers planned from a fresh snapshot (`plan_leftovers`, pure) and executed in a fixed order: policies → IAP policy repoint (labels re-sent) → IAP enforcement restore → extension copies (only once unreferenced) → IAP grant pruning → unbinding our stray engines; then one wait for every engine update (30 min timeout). Not ours: reported. Idempotent; a clean environment plans nothing.
- `agdemo_core/infra/lifecycle.py` + `./agdemo up` / `./agdemo down` (sequencing over an `ops` object; `LiveUpOps` / `LiveDownOps`), the write-command safety checks, the typed confirmation, `plan_teardown` (pure) and the shared-grant handling.
- `infra/terraform/guards.tf`: `terraform_data.teardown_guard`, `depends_on = [module.base]`, destroy-time `local-exec` running `${var.agdemo_cli} check-clean --strict --expect <project>/<region>/<prefix>` (default `../../agdemo`, relative to `infra/terraform`). New variable `agdemo_cli`.
- GE helpers in `publish_ge.py` (`list_agents`, `unregister`): the Discovery Engine `v1alpha …/assistants/default_assistant/agents` list and `DELETE`, the API agents-cli's publish uses (agents-cli has no unregister); matched on the ADK registration's reasoning engine id.
- `status.reset_theme` / `wait_engines`: `./agdemo reset <theme>` now detaches with one PATCH per engine (`remove_many`, as the UI's reset does) and its wait loop ends on failed PATCHes and a timeout.
- Tests: `ui/backend/tests/test_lifecycle.py` (33): every check-clean category and level, strict vs default, `--expect`; `plan_leftovers` (actions, order, reports, idempotency, no platform pruning without the principalSet); `prune`; `reset --all` sequencing, dry run, error handling; `wait_engines`; one detach per theme; up/down refusals; the confirmation; shared-grant addresses; `plan_teardown`; up/down ordering and every early stop; the guard file and the exact guard command through the CLI; migration mode allows the guard.

**Read-only results:**
- Fresh environment plan (us-east1, prefix `agdtest`, local scratch state): **97 to add** (§7d's 96 + the guard), 0 change, 0 destroy; plan-safety OK. The guard plans with `input.environment = <project>/us-east1/agdtest`, unknown gateway ids, `depends_on = ["module.base"]` and the destroy provisioner.
- Live migration plan (generated imports, local scratch state): **69 to add, 8 to change, 0 to destroy, 35 to import**, plan-safety (migration mode) OK; the only difference from T2's saved `plan-actions.txt` is `create terraform_data.teardown_guard`. The runbook's expected counts are updated.
- `check-clean` on the live demo: 25 blocking (4 engines, 8 Cloud Run, 8 registry entries, a helpdesk egress binding and 4 helpdesk grants: the live helpdesk state at the time), 8 warnings (UI, 2 GE registrations, 4 legacy resources, state.json); every check readable. `reset --all --dry-run` planned the 4 helpdesk policy removals, no Model Armor change, and reported the legacy ingress policy and extension.
- `terraform fmt -check`, `terraform validate`: OK. `ui/backend/tests`: 156 passed; `runtimes/tests`: 9 passed.

**Decisions and deviations:**
1. **Shared grants on `down` (the §7a note): not destroyed.** Before the destroy, `down` runs `terraform state rm` on the presenters' `aiplatform.user` and the static service agents' Model Armor grants (the `shared_project_grants` set), and on the egress gateway agent's grants only when another gateway in the project uses the same agent (`locations/-` list; read failures count as shared). They stay in IAM and the next `up` re-adopts them with no-op additive creates. After the destroy a read-only re-check reports whether the static agents still hold the 3 roles; it never writes. Rejected: destroy then re-add (a window where the other environment's Model Armor callouts fail, and "what does the live demo still need" isn't knowable from this state). Read live: each AGENT_TO_ANYWHERE gateway in the project has its own service-extensions agent, so the egress agent's grants are normally destroyed with the gateway.
2. **Guard depends on all of `module.base`**, not only the gateways (§4.4), so nothing is deleted while it runs; `--expect` makes it fail (exit 2) when the config on the machine isn't the environment being destroyed. It fails closed without `./agdemo`/uv. Bypasses: `-target`, `terraform state rm`, removing the block, a tainted guard; the deliberate one is `terraform state rm terraform_data.teardown_guard`.
3. **`check-clean` is top-level** (`./agdemo check-clean`, not `tf check-clean`), with the BLOCK/WARN/INFO levels above; `--strict` is what `down` and the guard run.
4. **`reset --all` goes beyond §4.2 in three places:** grants on platform entries other than the project principalSet's egressor are removed (§4.2 said report only; they're grants on prefix resources); the IAP extension's enforcement mode and the IAP policy's provider are restored when Policy Studio changed them; our engines bound to our gateways but not in state.json are unbound. Legacy resources are never touched by `reset --all` (`down` deletes them), and nothing that isn't both prefix-named and labelled is deleted.
5. **Waiting:** one wait for all engines at the end of `reset --all`, not per theme (the detaches run in parallel; same result, faster).
6. **`up` deploys engines one at a time.** In-process parallelism is unsafe (`deploy_spec.staged` changes `os.environ`, the cwd and `sys.modules`), and so are parallel processes: the vertexai SDK stages every engine's pickle under the same default `gcs_dir_name` (`agent_engine`) in the staging bucket, and `update_state` has no cross-process lock. Images are built once for all themes. A per-engine `gcs_dir_name` plus subprocesses plus a state.json file lock would allow it; that needs a live test.
7. **`down` deletes** every Cloud Run service named `<prefix>-*` and labelled (themes, a private MCP server, the UI), every non-platform `<prefix>-*` registry entry (including Policy Studio-made ones), the legacy resources (policy, extension, the two caller SAs and their project grants), and non-foundation prefix Model Armor templates. Engines and Cloud Run services are deleted in parallel.
8. **Presenting check:** a warning plus the typed `<project>/<prefix>` confirmation (`--yes` needs a matching `--prefix`); the UI has no busy flag to read.
9. **Write-command safety:** `up` refuses a live egress gateway without `tf_environment` (the bootstrap-created live demo), a state.json of another environment, and a gateway that the Terraform state doesn't hold. `down` needs `tf_environment` matching the config, an existing state bucket (it never creates one), a Terraform `environment` output matching the config, the egress gateway in state, and no `imports.tf`. After the T2 migration the live demo **is** a valid `down` target (runbook updated).
10. **`bootstrap` is a pointer** (exit 2, names `up` and the runbook); `--legacy` runs the old path. `teardown --all` refuses a Terraform-managed environment unless `--legacy`.
11. **One state.json per checkout.** `agdemo_core` always uses `config/state.json`, so a second environment (the T5 us-west1 run) needs its own checkout; `check-clean` ignores (and warns about) a state.json that describes another environment, and `up`/`down` refuse it.
12. **Migration mode** allows `terraform_data` creates (Terraform-only).
13. **`up`'s verify** is a CLI read-only check (gateways, IAP policy, template, platform count, every engine exists and is unbound, components deployed, UI URL), not the UI's probe-based Verify.

**Needs a live run (T5):** everything that writes: `reset --all` for real (repoint PATCH, enforcement restore, IAM pruning, stray unbind, the wait), `down` end to end (GE `DELETE`, the Cloud Run v2 REST delete, engine deletes, `state rm`, the destroy with the guard passing, the re-check), the guard **aborting** a bare `terraform destroy` with nothing deleted (ordering included), `up` end to end and its timing, and a second `up`/`down` for idempotency.

## 7f. T4 as built (2026-10-16)

No GCP writes were made: unit tests on fakes, plan-only Terraform runs with a local scratch state, and the UI in Demo mode.

**What exists:**
- **Terraform.**
  - `modules/private_egress` (`count` = `private_networking.enabled`), all in the gateway project:
    - `compute` + `dns` APIs (never disabled);
    - VPC `<prefix>-pn-vpc`, subnet `<prefix>-pn-att` (/28, Private Google Access), network attachment `<prefix>-pn-att` (ACCEPT_AUTOMATIC);
    - global address `<prefix>-pn-psc-ip` (PRIVATE_SERVICE_CONNECT) and forwarding rule `<prefix without hyphens>pnpsc` (`all-apis`; validated `^[a-z][a-z0-9]{0,19}$`);
    - private zone `<prefix>-pn-run-app` (`run.app.`) with apex and wildcard A records to the PSC IP;
    - `gcp-sa-agentgateway` `roles/compute.networkAdmin` + `roles/dns.peer` (`_member`);
    - template `<prefix>-pn-tmpl-<sha1(content)[0:6]>`, `create_before_destroy`, `CENTRALIZED`, `PRIVATE_RANGES_ONLY`, DNS peering `run.app.`; attachment and network by project **ID**.
  - Output `template_ref` (project **NUMBER** form), with `depends_on` the service-agent grants. The gateway reads it, so it's created after the grants and destroyed before them.
  - `modules/regional` (second-region instance of the base pieces the mode needs):
    - `<prefix>-pn-egress`, created **with** `agent_connectivity_template` (precondition: number form), `ignore_changes = [protocols]`;
    - `<prefix>-pn-egress-iap-authz` + `<prefix>-pn-egress-iap-policy` (REQUEST_AUTHZ, the base's `ignore_changes`);
    - the region's 25 platform entries + egressor grant;
    - a staging bucket `<project>-<prefix>-staging-<region>` for that region's engines.
  - Root: `module.private_egress`, `module.pn_region` (`depends_on = [module.base]`), output `state_regions`, the guard's `depends_on` and `input.gateways` cover both. Base: one new module output, `egress_gateway_id`.
- **Python.**
  - `DemoConfig.private_networking` (enabled, region, subnet_range, psc_ip, dns_domains, vpc_egress, egress_name, manage_service_agent_roles, themes) and regional views: `cfg.for_region(r)`, `cfg.region_views()`, `cfg.home_region`. A view changes `region`, the gateway, registry, IAP names and the staging bucket; images, the UI, the state and recordings stay in the base region.
  - Themes: `region:`, `network_mode: internet | private_networking`, component `ingress: all | internal`, scenario test `via: agent | direct`. `themes.theme_config(cfg, theme)` gives the theme's view; Internet themes get the base config unchanged.
  - `tf vars` writes `private_networking` (names from `common.pn_names`, the region's platform endpoints); `{"enabled": false}` when off, exactly as before. `output-sync` merges `state_regions` into `state.json shared.regions.<region>` (replaced as a whole; removed when empty).
  - `deploy-theme`, policy handlers, gateway logs, registry view, Terraform export, status, Verify, `reset`, `teardown <theme>`, `reset --all`, `check-clean` and `down` follow the theme's region. Engine URLs follow the region in the engine name.
  - `agdemo_core/bypass.py` (the direct call), `agdemo_core/network_view.py` (the "Under the hood" data), `gateway_logs.is_private_ip` (10/8, 172.16/12, 192.168/16).
- **Theme `privnet`** (us-west1): Payroll Agent; Payroll MCP (`ingress: internal`; `get_payslip` read-only, `update_bank_details` write); Benefits MCP (public). Policies: gateway attach, Payroll all / read-only by name / read-only by hint, Benefits allow. Five scenarios for the §7c steps.
- **UI.**
  - Network switch (Internet / Private Networking) that filters the theme picker; the env chip shows the theme's region.
  - VPC node (attachment, PSC endpoint, DNS) between the gateway and Payroll MCP, with Benefits on the direct gateway path.
  - New edge state `unreachable` (404) and a "private path" badge.
  - "Under the hood: private network" card (`GET /api/themes/{id}/network`).
  - Gateway log entries with a private `serverIp` marked "private path".
  - Model Armor is hidden in this mode. The in-browser mock supports all of it.
- **Tests:** `ui/backend/tests/test_private_networking.py` (42).

**Plan-only results:**
- **Fresh environment** (prefix `agdtest`, base us-east1, PN us-west1, local scratch state): **164 to add, 0 change, 0 destroy**, plan-safety OK.
  - 97 are T3's (§7e).
  - 67 are Private Networking: `private_egress` 13 (2 APIs, network, subnet, attachment, address, forwarding rule, zone, 2 records, 2 grants, template) and `pn_region` 54 (gateway, extension, policy, bucket, 25 + 25 registry).
  - The gateway plans with `agent_connectivity_template = projects/<number>/locations/us-west1/agentConnectivityTemplates/agdtest-pn-tmpl-<hash>`; the template with the project-ID attachment and network.
  - The same environment with PN disabled: 97 (= T3), and its tfvars are byte-identical to T3's.
- **Live migration plan, PN disabled** (T3's generated imports, local scratch state): **69 to add, 8 to change, 0 to destroy, 35 to import**. The 104 resource actions are identical to T3's `live-actions.txt`. The tfvars generated from the live config are identical to T3's. The only plan difference is a new root output, `state_regions = {}`.
- `terraform fmt -check -recursive` and `terraform validate` pass.

**Decisions and deviations:**
1. **Base not refactored.** Moving base resources into a shared regional submodule would change the live addresses the T2 imports target (`module.base.google_network_services_agent_gateway.gw["egress"]` and so on). `modules/regional` therefore repeats the base's gateway, authz and registry blocks with the same arguments and lifecycle rules, and a test checks the two stay in step. Unifying them later needs `moved` blocks after the T2 apply.
2. **Q7 (service identity):** no google-beta and no local-exec. The `gcp-sa-agentgateway` grants reference the base egress gateway id in a precondition, so they're created after it: creating the first Agent Gateway provisions the project's gateway service agents. If a fresh-project run still fails with "service account does not exist", re-run apply. T5's fresh-project run settles it.
3. **Model Armor:** not in this mode (minimal; no demo step needs it). No template, extension or grants in the PN region; the UI hides the checkbox; `reset --all` skips it there.
4. **No ingress gateway** in the PN region; Private Networking themes have no `ingress:user` edge.
5. **IAP names in the PN region:** `<prefix>-pn-egress-iap-*` (next to `<prefix>-pn-egress`), so log entries name the right policy.
6. **Bypass probe:** a scenario test with `via: direct` is run by the UI backend. Live: an HTTPS `initialize` + `tools/call` to every URL form; 404 → `unreachable`. Demo: simulated. The 404 of an unbound agent (step 1b) comes from the orchestrator's normal probe; a 404 is mapped to `unreachable` only for `ingress: internal` targets, so Internet themes still show `error`.
7. **Steps 2 and 3** are the same GCP state (registration happens at deploy). They're separate scenarios for the talk track; registration isn't a toggle.
8. **The guard's `depends_on`** now lists all three modules. The T3 test assertion was updated to match.
9. **Shared grants on `down`:** the `gcp-sa-agentgateway` grants are kept (`state rm`) only when another VPC-enabled gateway in the project that isn't ours exists (unreadable = shared). `manage_service_agent_roles` defaults to `terraform.shared_project_grants`.
10. **The UI lists `private_networking.themes`** even when the mode is off (marked not deployed, so Demo only). `up` deploys them only when the mode is on (`cfg.enabled_themes()`).
11. **`--allow-template-swap`** is on `tf plan`, `tf apply` and `up`, so a template content change (a create-before-destroy swap) passes the plan-safety check.

**Needs a live run (T5):**
- Apply in us-west1, timings, and the two-region `down` with the guard.
- Q7 in a fresh project.
- Binding by PATCH: an engine bound by PATCH (the demo's live toggle) to a template gateway. P0 only verified create-time binding, and F44 wasn't isolated (since traced to a google-auth bug, ISSUES #16; binding by PATCH verified live 2026-10-17 and 2026-10-22).
- Cross-region resources: Cloud Run in us-west1 pulling images from the base region's repo; the regional staging bucket with the vertexai SDK.
- Steps 1–5 live: the 404 from the backend and from the unbound engine, 403, the grant → 200 delay, and the real `serverIp` in the log.
- Both URL forms accepted by the registry and IAP.
- Cloud Run `--ingress=internal` with `public_targets` false (id-token auth over the PSC path).
- `reset --all` and `check-clean` against real us-west1 resources.

## 7g. T2 live migration: done (2026-10-17)

Runbook steps a–g ran on the live demo (us-east4) in a quiet window, after a helpdesk reset.
- **Legacy cleanup:** the ingress IAP policy and extension and the two caller SAs (and their project grants) were deleted and verified gone.
- **Terraform state:** in its GCS bucket, versioning on.
- **Apply:** **35 imported, 69 added, 8 changed, 0 destroyed.** The follow-up plan shows **No changes**.
- **state.json:** only `recordings_bucket` and `tf_environment` were added.
- **Recordings:** 20 objects / 50,676 bytes copied to the recordings bucket, with identical counts and sizes. The UI was redeployed and `AGDEMO_RECORDINGS_URI` points at the new bucket. The old copies are still in staging.
- **`status`:** the shared table matches the pre-migration baseline exactly, and all engines are unbound.
- **Runbook fix:** `--out` / `--json` paths are resolved from `infra/terraform`, so pass absolute paths.

## 7h. Private Networking mode live (2026-10-17)

- **`tf apply` with `private_networking.enabled`:** **67 added, 1 changed** (the guard's region list), **0 destroyed**.
  - `agdemo-pn-egress` was created with the connectivity template from the start, in 3m45s.
  - The attachment, PSC endpoint (`agdemopnpsc`) and template `agdemo-pn-tmpl-*` were created in seconds.
- **`deploy-theme privnet`:**
  - Payroll MCP: ingress internal, both run.app URLs registered in us-west1;
  - Benefits MCP: public;
  - orchestrator: Agent Identity, google-auth pinned (ISSUES #16).
- **Demo steps, run live through the UI backend API (the same path as the UI toggles):**
  1. **Direct from the internet:** Payroll **404**, Benefits **200**.
  2. **Unbound agent:** Payroll is **unreachable** (public path, 404), Benefits direct.
  3. **Bind to `agdemo-pn-egress`:** a PATCH on a running engine, **about 4m20s**. **This verifies post-create binding to a template gateway,** which P0 never tested.
  4. **Bound, no grants:** all four tools **403** "Egress request is not authorized". No 401s.
  5. **Grant Payroll read-only (by name) + Benefits:** after **about 1m45s**, `get_payslip` **200**, `update_bank_details` **403**, Benefits both **200**.
  6. **Gateway log:** Payroll calls have `httpRequest.serverIp` **10.70.250.10:443**, the PSC endpoint, which proves the private path.
  7. **Reset** back to wide open: unbound, verified.
- **Finding (follow-up):** **Benefits calls through the gateway also go to the PSC IP.** The template's DNS peering for `run.app.` resolves *every* `*.run.app` name in the VPC.
  - Benefits is "public" only in that it also answers internet callers directly.
  - The theme/UI copy should say so, or Benefits needs a non-run.app hostname for a true internet-path contrast.
- **T5 (throwaway `up`/`down`) is not run yet.** It needs a separate checkout, because state.json and `infra/terraform/.terraform` are per checkout. Commands are in HANDOFF §8. (Run on 2026-10-17: §7i.)

## 7i. T5 results: throwaway up / down (2026-10-17)

The test environment was in the live demo's project, in a separate checkout (git worktree, branch `t5-test`):
- prefix `agdtest`, region us-east1, Private Networking off;
- `terraform.shared_project_grants: false`;
- `helpdesk` theme only, `--no-ui`.

Before anything was written, I snapshotted the live demo and the project IAM policy (read-only lists in us-east1, us-east4, us-west1 and us-central1). Every plan was checked for names other than `agdtest-*` before applying.

**Timings**

| Step | Run 1 | Run 2 |
|---|---|---|
| `up` total | **18m29s** | **13m10s** |
| ↳ Terraform plan | 97 to add, 0 change, 0 destroy; plan-safety OK | same |
| ↳ apply | about 7 min | 5m53s |
| ↳ egress gateway create | 2m45s | 2m33s |
| ↳ ingress gateway create | 31s | 11s |
| ↳ IAP policy create | 3m14s | 3m6s |
| ↳ Images (Cloud Build, both in parallel) | n/a | 53s |
| ↳ Cloud Run × 4 + registry | n/a | about 40s |
| ↳ Engines (sequential) | n/a | 2m36s, 2m56s |
| `down` total | **9m34s** | **10m20s** |
| ↳ `reset --all` (nothing applied) | 19s | 14s |
| ↳ CLI teardown (engines, Cloud Run, registry) | 23s | 23s |
| ↳ `terraform destroy` | 8m28s | 9m6s |
| ↳ IAP policy delete | 2m34s | 3m15s |
| ↳ egress gateway delete | 5m8s | 5m12s |
| ↳ ingress gateway delete | 11s | 11s |

Run 1's log had no timestamps, so its per-step figures after the apply aren't available. Engines were much faster than §4.3's estimate of 4.5–7 min each. `Destroy complete! 97 destroyed` counts the 21 `google_project_service` entries, which are removed from state only (`disable_on_destroy = false`).

**Helpdesk, live, against `agdtest`** (local UI backend, `--mode live`, through the same API as the UI toggles):
1. **Bind `gw-egress`:** applied after **5m18s**. The engine PATCH took about 4m50s, then the enforcement wait.
2. **Probe:** all 8 orchestrator edges **403** "Egress request is not authorized", with no 401s. The `hr-assistant` edges stay direct 200, because that engine is unbound.
3. **Grant `allow-kb`:** `kb-agent` **200** after **1m36s**. Every other edge stays 403.
4. **Reset:** the grant was removed in about 40s and the engine unbound in **2m46s**. The state shows nothing applied.

**Destroy guard** (after the reset, with engines and Cloud Run still deployed):
- `check-clean --strict` found 10 blocking items: 2 engines, 4 Cloud Run services, 4 registry entries.
- A bare `terraform -chdir=infra/terraform destroy` (no `-target`, `yes` typed at its prompt) planned 97 destroys. It ran only `terraform_data.teardown_guard`'s destroy, which failed the check, and **aborted after 28s: "Nothing was deleted."**
- `terraform state list` was identical before and after (99 entries).
- `./agdemo tf plan` showed **0 / 0 / 0**, so the guard wasn't tainted.

**Failure found, and fixed on `t5-test`:**
- **Symptom:** after the guard-aborted destroy, `./agdemo down` refused with "the Terraform state describes nothing (no outputs)".
- **Cause:** Terraform removes the root outputs from state at the start of every destroy. Its plan says "Changes to Outputs: - environment", and the aborted run wrote a new state serial with `outputs: {}`, though no resource changed. `down` identifies the environment from the `environment` output. The same thing would also stop a re-run of `down` after a half-finished destroy, which §4.4 promises works.
- **Fix:**
  - `tf_foundation.environment_output()` falls back to the guard's recorded `input.environment` (`<project>/<region>/<prefix>`; the gateway project comes from its gateway ids) when the state has no outputs.
  - A missing, malformed or different guard still refuses.
  - The guard's abort message now says the outputs were removed and that `./agdemo tf apply` (no resource changes) restores them.
  - §4.4 records the caveat.
  - Two new tests in `test_lifecycle.py`; `ui/backend/tests`: 201 passed.
- **Proven live:** run 1's `down` ran on exactly that output-less state and passed.

**`down` and verify (both runs):**
- The guard ran `check-clean --strict --expect <project>/us-east1/agdtest` and passed (**CLEAN**). The destroy then deleted the gateways, policy, extensions, Model Armor template, 25 platform entries, SAs, repo and staging bucket.
- `./agdemo check-clean --strict`: 0 / 0 / 0.
- Read-only lists in us-east1 show no gateways, authz extensions or policies, engines, Cloud Run services, Model Armor templates, Artifact Registry repos or `agdtest` SAs. `config/state.json` has empty `shared` and `themes`.
- **Kept by design:** the state bucket (prefix `agdtest/…`) and `<project>-agdtest-recordings`.
- **Live demo unchanged:** the us-east4, us-west1 and us-central1 lists are identical to the pre-T5 snapshot (`agdemo-egress`, `agdemo-ingress`, `agdemo-pn-egress`, their extensions, policies, engines and services; the unrelated us-central1 gateway).
- **Project IAM policy identical to the pre-T5 snapshot** after each `down`. The 3 Model Armor grants Terraform made for the new egress gateway's own service agent were removed with it, and nothing the live demo holds was touched. This confirms §7e decision 1: each AGENT_TO_ANYWHERE gateway has its own agent.

**Repeatability:** run 2 (`up` then `down`) planned the same 97 creates. It needed no fixes, left the same clean inventory, and left IAM identical to the pre-T5 snapshot.

**Not covered by T5:**
- Q7 (a truly fresh project: the gateway service agents already existed here).
- Private Networking `up`/`down` in a throwaway environment.
- `ui deploy` and `--ge`.
- `reset --all` with real leftovers to clean: both runs found nothing to remove.

## 8. Open questions

1. **Enforce / dry run ownership:** should Terraform own `iap_enforcement` from demo.yaml, or `ignore_changes` on the extension's `fail_open` and `metadata` so Policy Studio's toggle sticks? (Recommended: ignore.) The same question applies to the IAP policy's `custom_provider`, which Policy Studio's "own extension" flow repoints.
2. **Policy Studio resources** (SA, custom role, repo, Cloud Run): leave them with `deploy.sh`? (Recommended: yes.)
3. **Staging bucket on `down`:** `force_destroy = true` loses `recordings/`. Move the recordings to a bucket kept outside Terraform, or keep the staging bucket out of `destroy` (`deletion_policy = "ABANDON"`)?
4. **Legacy resources:** OK to delete `agdemo-ingress-iap-authz`/`-policy` and the two caller SAs before importing?
5. **Private MCP server:** which theme gets it, and what's it called? Does the TESTBED "option a" (update `agdemo-egress` in place) stand, given it needs a reset window of about 7 min?
6. **`gcp-sa-agentgateway` roles:** `roles/compute.networkAdmin` (verified) or a custom role (untested)?
7. **Fresh projects:** does `gcp-sa-agentgateway` exist before the first gateway, or does the first template gateway create fail because its roles can't be granted yet? Options:
   - google-beta `google_project_service_identity` (which service name is unverified);
   - a `terraform_data` running `gcloud beta services identity create`;
   - create the ingress gateway first.

   To be settled by a fresh-project run.
8. **UI Cloud Run in Terraform?** (Recommended: no; it depends on state.json and the image.)
9. **Should `down` unregister the demo agents from the GE app** (today nothing removes them)? Is the GE app itself always pre-existing and out of scope? **T3: yes, `down` unregisters registrations of its own engines; the GE app itself is out of scope.**
10. **State bucket:** the name `<project>-agdemo-tfstate`, kept forever. Or one shared bucket for both repos?
11. **Minimum Terraform version** 1.7 (`for_each` imports). Fine for presenters on other machines?
12. **APIs on `down`:** keep them enabled. (Recommended: yes; the project is shared.) **T3: kept.**
