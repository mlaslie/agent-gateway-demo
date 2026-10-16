# Terraform foundation plan (base + private egress)

**Status:** T1 built (2026-10-16; see §7a). T2–T5 not started. Nothing in GCP was changed while writing it: the only calls were read-only lists and GETs against the demo project, plus a read of the provider schema and source.

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
| Agent Runtime engines | §1.4. When private egress is on, `deploy-theme` adds `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false` to the engine env (F44), read from `state.json shared.private_egress_enabled` |
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

Because the guard **depends on** the gateways, Terraform destroys it **first**. A non-zero exit aborts the destroy before anything is deleted. The message names what's still bound or attached and says "run ./agdemo down (or reset --all)".

Caveats:
- `-target` or `terraform state rm` can bypass the guard.
- Destroy provisioners don't run if the guard is tainted.
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
| T2 | Import the live demo: legacy cleanup (after asking), `tf import` generator, plan-safety check, iterate to a zero-diff plan, apply, deprecate `bootstrap` | 1–2 d |
| T3 | Lifecycle: `up`, `reset --all`, `down`, `tf check-clean`, guard resource, teardown additions (GE unregister, Policy Studio leftovers) | 2 d |
| T4 | `private_egress` module, `migrate-gateway`, preconditions, env var in `deploy-theme`, private MCP component (internal ingress, both URLs registered), status/UI showing private egress | 2–3 d |
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
- **Deploys:** Payroll MCP (internal ingress) and the public MCP. The theme's orchestrator is deployed with the token-sharing env var.
- **Registry and grants:** the theme's registry entries are created in us-west1. Grants stay live toggles.

**Demo UI:**
- a **mode switch** (Internet / Private Networking) that selects the theme set and region;
- a VPC diagram view (agent → VPC-enabled gateway → attachment / PSC endpoint / DNS → Payroll MCP, with the public MCP alongside);
- an "Under the hood" panel showing the pre-built network pieces and the template;
- gateway log entries highlight a private `serverIp`.

**Demo steps in Private Networking mode** (toggles and tests):
1. Payroll MCP is internal-only: a direct call is rejected with 404.
2. Through the gateway (route exists, no permission): 403.
3. Registered in Agent Registry: still 403. Reachable and registered isn't the same as allowed.
4. Grant (optionally read-only tools): 200, and the gateway log shows the private 10.x IP.
5. Contrast: the public MCP follows the same policy over the internet path.
6. Optional: the diagram explains a "direct VPC access" agent (PSC interface) that bypasses the gateway. It's explained only, not deployed.

**Effort:** about 3–4 days on top of T1–T5 (region-aware themes, the mode switch, the new theme and scenarios, the VPC diagram, log highlight). T4 changes accordingly: no gateway migration, so the second-region module plus the theme.

**Standing cost:** PSC endpoint about USD 8/month, plus the extra orchestrator's idle cost and the second gateway. Gateway pricing isn't checked.

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
9. **Should `down` unregister the demo agents from the GE app** (today nothing removes them)? Is the GE app itself always pre-existing and out of scope?
10. **State bucket:** the name `<project>-agdemo-tfstate`, kept forever. Or one shared bucket for both repos?
11. **Minimum Terraform version** 1.7 (`for_each` imports). Fine for presenters on other machines?
12. **APIs on `down`:** keep them enabled. (Recommended: yes; the project is shared.)
