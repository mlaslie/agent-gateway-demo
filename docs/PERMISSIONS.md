# Permissions: the minimum IAM roles

> **Status (2026-10-23): tested live.** The deployer's 15 base roles (§2.2) are enough for the full lifecycle (`up`, a Live scenario through the deployed UI, `down`); Private Networking adds 2 (`compute.networkAdmin`, `dns.admin`). **The trims in §4 were also tested live and work:** a 14-role deployer (`aiplatform.expressUser`, a custom IAP role, Project IAM Admin limited by two conditions, no `logging.viewer`), a 6-grant UI service account and a 1-role targets service account. Results in §0. Still untested: the GE role, and `iam.serviceAccountUser` per service account.
>
> Originally (2026-10-21) this page was worked out from the code and from read-only checks. Every role below comes from reading what the demo calls: `infra/terraform/**`, `agdemo_core/**`, `ui/backend/**` and `runtimes/**`. Permission names were checked against the live role catalogue (`gcloud iam roles describe`, the IAM `roles.list` API) and with `testIamPermissions`. Nothing was created or granted. Each suggestion is marked **verified** (the permission names were checked) or **needs live test** (only a run with these roles will show whether it's enough). The test plan is in §6.

## Auth strategy: standard or minimum

The demo works with either of two role sets, both tested live end to end (§0). **`standard` is the default** and uses Google's predefined roles; nothing changes unless you choose `minimum`.

| | `standard` (default) | `minimum` (least privilege) |
|---|---|---|
| Deployer (you, or whoever runs `./agdemo up`) | 15 predefined roles (§2.2) | 14 roles: `aiplatform.expressUser` instead of `aiplatform.user`, a custom IAP role instead of `iap.admin`, no `logging.viewer`, and Project IAM Admin limited by two conditions to the roles the demo grants (§5.3) |
| `<prefix>-ui` (the UI on Cloud Run) | 9 predefined roles | 6: `aiplatform.expressUser`, `agentregistry.viewer`, `logging.viewer`, `serviceusage.serviceUsageConsumer` and two project custom roles Terraform creates (`<prefix>RegistryEgressAdmin`, `<prefix>GatewayPolicyEditor`). No staging-bucket grant |
| `<prefix>-targets` (Cloud Run demo agents) | `aiplatform.user`, `logging.logWriter` | `aiplatform.expressUser` |
| Private Networking | +`compute.networkAdmin`, `dns.admin` for the deployer | the same |

**Choose it** in `config/demo.yaml`:
```yaml
iam:
  auth_strategy: minimum        # standard (default) | minimum
```
or for one run: `./agdemo up --auth-strategy minimum` (also on `tf vars/init/plan/apply` and `ui deploy`). The flag overrides the file for that run only, so put the choice in the file to keep later runs consistent.

**The deployer's roles** are granted by a project owner, once, before `up`. The CLI prints the exact commands for your strategy:
```bash
./agdemo deployer-roles --member user:you@example.com                            # standard
./agdemo deployer-roles --member user:you@example.com --auth-strategy minimum    # minimum
```
It adds the Private Networking roles when `private_networking.enabled` is true (`--private-networking` / `--no-private-networking` to override). `--apply` runs the commands instead of printing them; it needs an owner (or Project IAM Admin and Role Administrator). For `minimum` it also creates the custom role `<prefix>DeployerIap` and writes the two condition files to `config/generated/`. A `minimum` deployer can only grant the `minimum` service-account roles, so use it with `iam.auth_strategy: minimum`. A `standard` deployer works with either.

**Switching an existing environment** changes which roles Terraform grants, so the plan removes the old service-account grants (and, from `minimum` back to `standard`, the custom roles). The plan-safety check refuses deletes, so allow these once:
```bash
./agdemo up --auth-strategy minimum --allow-role-changes      # or tf apply --allow-role-changes
```
`--allow-role-changes` lets the plan remove only project role grants and project custom roles; every other delete is still refused. Grant the deployer its new roles first when moving to `minimum` (the `minimum` deployer can't remove the old `standard` grants; do the switch as a `standard` deployer or an owner, then trim the deployer). `down` deletes the custom roles, and Google keeps a deleted custom role's id reserved for about 37 days; the next `up` re-creates them anyway (the Google provider undeletes a role with the same id and applies its permissions; checked live).

## 0. Live test result (2026-10-22)

The full lifecycle was run as a service account (`agdtest-deployer`) that held **only the 15 roles of §2.2**. It ran in a throwaway environment: prefix `agdtest`, us-east1, the helpdesk theme, Private Networking off, `shared_project_grants: false`, with the UI on Cloud Run. The account was impersonated for both gcloud and Application Default Credentials (Terraform and the Python SDK).

| Step | Result |
|---|---|
| `up`: Terraform foundation (97 resources: gateways, IAP and Model Armor extensions, authz policy, Model Armor template, 25 platform registry entries and their `iap.egressor` grants, the two service accounts and their 14 project roles, bucket, Artifact Registry repository, APIs) | Passed, no missing permission |
| `up`: image builds and the UI build | **Failed at first**, though every build itself succeeded. A blocking `gcloud builds submit` streams the build log, and for the default logs bucket gcloud allows that only for a project Viewer, Editor or Owner. **Fixed in code instead of granting `roles/viewer`**: builds are now submitted with `--async` and polled with `builds describe` (`gcloud.submit_build`), which `cloudbuild.builds.editor` covers |
| `deploy-theme helpdesk`: 4 Cloud Run services, 4 registry entries, 2 Agent Runtime engines with Agent Identity, their project roles | Passed |
| `ui deploy` (Cloud Run behind IAP, `httpsResourceAccessor` grant) and `verify` (9 checks) | Passed |
| Live scenario in the deployed UI, as `<prefix>-ui` with its Terraform roles: bind the egress gateway, probe (all 403), grant KB Agent (`iap.egressor`), probe (KB 200, HR Records 403), read gateway logs, Reset (revoke and unbind) | Passed |
| `down` (reset, engines, Cloud Run, registry entries, `terraform destroy` of 97 resources, shared-grant re-check) | Passed |

Project IAM was snapshotted before the test and compared afterwards: the only difference was the test account's own 15 bindings, which were then removed.

**Private Networking run (same day).** The same account with the 15 roles **plus `roles/compute.networkAdmin` and `roles/dns.admin`** (17 in all), and nothing else. Config: base region us-east1, `private_networking.region: europe-west4` (no gateway in the project there), theme `privnet`, `shared_project_grants: false`.

| Step | Result |
|---|---|
| `up`: Terraform foundation (162 resources: the base 97, plus the VPC, subnet, network attachment, PSC global address and `all-apis` forwarding rule, private `run.app.` zone and 2 records, the agent connectivity template, the VPC-enabled gateway with its IAP extension and policy, 25 regional registry entries and grants, a regional staging bucket) | Passed, no missing permission. No `servicedirectory` role was needed, and the connectivity template was created with these roles alone |
| `deploy-theme privnet`: Payroll MCP (`--ingress=internal`), Benefits MCP, both run.app URL forms registered, the orchestrator engine | Passed |
| `ui deploy` and `verify` (11 checks, including the VPC-enabled gateway and its template) | Passed |
| Live Private Networking scenario in the deployed UI: bypass (Payroll 404, Benefits 200), bind to the VPC-enabled gateway, grant Payroll's read-only tools, `get_payslip` through the gateway (200; the gateway log's upstream `serverIp` was the PSC endpoint `10.70.250.10:443`), Reset (revoke and unbind) | Passed |
| `down` (both regions, `terraform destroy` of 162 resources) | Passed |

Project IAM was again identical to its pre-test snapshot after the 17 bindings were removed.

Not covered: with `shared_project_grants: false`, Terraform didn't grant the Agent Gateway service agent (`gcp-sa-agentgateway`) its `compute.networkAdmin` and `dns.peer` roles; they were already in the project from the live demo. Granting them is `resourcemanager.projectIamAdmin`, which the deployer has, so this is expected to work but wasn't exercised.

**Trims run (2026-10-23).** The same throwaway Internet environment (us-east1, helpdesk, `shared_project_grants: false`), with every §4 trim applied:
- deployer: 14 roles instead of 15 (15 bindings: Project IAM Admin is bound twice). `aiplatform.expressUser` instead of `aiplatform.user`; a custom IAP role (`iap.webTypes/webServices/webServiceVersions.get/setIamPolicy`, §5.2 `agdemoDeployerIap` without the settings permissions) instead of `iap.admin`; no `logging.viewer`; `resourcemanager.projectIamAdmin` **with two conditions** (§5.3); the other 10 roles unchanged;
- `<prefix>-ui`: the 6 grants of §4 (`aiplatform.expressUser`, `agentregistry.viewer`, `logging.viewer`, `serviceusage.serviceUsageConsumer`, custom `RegistryEgressAdmin` and `GatewayPolicyEditor` as listed in §5.2), and no staging-bucket grant;
- `<prefix>-targets`: `aiplatform.expressUser` only.

| Step | Result |
|---|---|
| `up`: Terraform (93 resources; every grant goes through the conditional Project IAM Admin), images, `deploy-theme helpdesk` (2 engines created with `aiplatform.expressUser`, their 7 Agent Identity roles granted), `ui deploy` with `--iap` and the custom IAP role, `verify` (9 checks) | Passed, no missing permission |
| Live scenario as the trimmed UI SA: bind the egress gateway (`expressUser` covers the engine PATCH), Model Armor checkbox on (both `CONTENT_AUTHZ` policies created with the custom policy-editor role), grant KB Agent (custom egress-admin role), a Gemini question through the gateway (KB Agent called with 200 and answered), gateway logs read, Reset (grant revoked, Model Armor policies deleted, engine detached) | Passed |
| Targets SA without `logging.logWriter` | Container logs still reached Cloud Logging (Cloud Run collects stdout and stderr itself) |
| `down` (Agent Identity grants removed through the conditional Project IAM Admin, 93 resources destroyed, `check-clean --strict` CLEAN) | Passed |

Findings:
- **A `hasOnly()` list can hold at most 10 roles.** The single 17-role condition first proposed in §5.3 is rejected by IAM (`ListLengthCheck`). Two bindings of the same role with two conditions work; §5.3 now has them.
- **`gcloud ... --condition=expression=...` splits on the commas in a `hasOnly([...])` list.** Use `--condition-from-file`.
- `run deploy --iap` didn't need the IAP settings permissions, so `agdemoDeployerIap` needs only the 6 IAM-policy permissions.
- Project IAM was identical to its pre-test snapshot afterwards (including the policy version: no conditions left behind).

Not covered: `publish-ge` (GE role), and `iam.serviceAccountUser` granted per service account instead of on the project.

## 1. In plain language

Project Owner can run the demo, but it can do far more than the demo needs. This page lists what the demo actually needs, for three identities:

1. **The deployer.** This is the person (or service account) who runs `./agdemo up`, `deploy-theme`, `ui deploy`, `publish-ge`, `reset --all`, `check-clean` and `down`. These commands create the gateways, policies, registry entries, buckets, Cloud Run services and Agent Runtime engines. Terraform also grants roles to other identities, so the deployer has to be able to change project IAM.
2. **`<prefix>-ui`**, the service account the demo UI runs as. It applies the policies you toggle in Live mode: it binds and unbinds engines, grants and revokes IAP access on registry entries, and switches Model Armor on and off. It also reads logs and the registry, and stores recordings.
3. **`<prefix>-targets`**, the service account of the mock A2A agents and MCP servers on Cloud Run.

Some terms used below:
- **Predefined role:** a role Google maintains, such as `roles/run.admin`. Preferred here, because Google keeps it up to date.
- **Custom role:** a role you define in the project with an exact list of permissions. Suggested only where every predefined role that would work is far too broad.
- **Project IAM grant:** a role given on the whole project (`resourcemanager.projects.setIamPolicy`). Terraform makes several of these, so the deployer needs Project IAM Admin.
- **Impersonation:** running a command as a service account, using your own login to get its short-lived tokens. The least-privilege test in §6 uses it, so your own credentials stay as they are.

## 2. The deployer

### 2.1 What the deployer's commands call

| Command | What it does in GCP (and the permissions that need) |
|---|---|
| `up` → preflight | Reads the project, billing info and enabled APIs; `testIamPermissions`; lists gateways, the registry and engines in the region (`resourcemanager.projects.get`, `serviceusage.services.list`, `networkservices.agentGateways.list`, `agentregistry.services.list`, `aiplatform.reasoningEngines.list`) |
| `up` → `tf init` | Creates the state bucket `<project>-agdemo-tfstate` if it's missing, and reads and writes Terraform state in it (`storage.buckets.create/get`, `storage.objects.*`). Reads the organization id (`resourcemanager.projects.get`, `getAncestry`) |
| `up` → `tf apply`: module `base` | Turns on 21 APIs (`serviceusage.services.enable`). Creates 2 service accounts (`iam.serviceAccounts.create`). Makes 11 project grants for those SAs, the Model Armor grants for the service agents and the presenters' `aiplatform.user` (`resourcemanager.projects.setIamPolicy`). Creates the Artifact Registry repo (`artifactregistry.repositories.create`), the staging bucket (`storage.buckets.create`), the UI's `objectUser` grant on the recordings bucket (`storage.buckets.setIamPolicy`), 2 agent gateways (`networkservices.agentGateways.create`), the IAP and Model Armor authz extensions (`networkservices.authzExtensions.create`), the IAP `REQUEST_AUTHZ` policy (`networksecurity.authzPolicies.create`), the Model Armor template (`modelarmor.templates.create`), 25 platform registry entries (`agentregistry.services.create`) and `roles/iap.egressor` on each of them (IAP `setIamPolicy`) |
| `up` → `tf apply`: recordings bucket | Created through the API before the apply (`storage.buckets.create`) |
| `up` → images, `deploy-theme` | `gcloud builds submit` (`cloudbuild.builds.create`, plus the source upload to `gs://<project>_cloudbuild`, `storage.*`). `gcloud run deploy --service-account=<prefix>-targets --allow-unauthenticated` (`run.services.create/update`, `run.services.setIamPolicy`, `iam.serviceAccounts.actAs`). Agent Registry entries for each component (`agentregistry.services.create/update/delete`). The Agent Runtime engine through the `vertexai` SDK, with the code staged in the staging bucket (`aiplatform.reasoningEngines.create/get/list/update/delete`, `storage.objects.create`). **Project grants** of 7 roles for each engine's Agent Identity (`resourcemanager.projects.setIamPolicy`). With `cloud_run.public_targets: false`, also `run.invoker` for each target (`run.services.setIamPolicy`) |
| `up` → `ui deploy` | Cloud Build for the UI image. `gcloud beta run deploy --iap --service-account=<prefix>-ui` (`run.services.create/update`, `iam.serviceAccounts.actAs`, IAP settings on the service). `objectUser` for `<prefix>-ui` on the staging bucket (`storage.buckets.setIamPolicy`). `run.invoker` for the IAP service agent (`run.services.setIamPolicy`). `roles/iap.httpsResourceAccessor` for each viewer (`iap.webServices.setIamPolicy` on `iap_web/cloud_run-<region>/services/<prefix>-ui`) |
| `up` → verify | Reads gateways, the IAP policy, the Model Armor template and engines |
| `publish-ge` (optional) | `agents-cli publish gemini-enterprise`: creates or updates an agent in the GE app (`discoveryengine.agents.create/update/list/get`) |
| `reset --all` | Removes theme policies: IAP `setIamPolicy` on registry entries and the registry root, an engine PATCH to unbind (`aiplatform.reasoningEngines.update`), and deletes the Model Armor `CONTENT_AUTHZ` policies (`networksecurity.authzPolicies.delete`). It also undoes Policy Studio changes: it points the IAP policy back at its extension (`networksecurity.authzPolicies.update`), restores the enforcement mode (`networkservices.authzExtensions.update`) and deletes extra extensions (`networkservices.authzExtensions.delete`) |
| `check-clean` | Read-only lists: engines, Cloud Run services, registry services, IAP policies on each entry and on the registry root (`iap.*.getIamPolicy`), authz policies and extensions, Model Armor templates, GE agents (`discoveryengine.agents.list`), service accounts (`iam.serviceAccounts.list`) |
| `down` | `reset --all`. Then it removes the GE registrations (`discoveryengine.agents.delete`), deletes engines (`aiplatform.reasoningEngines.delete`), Cloud Run services (`run.services.delete`), registry entries and old leftovers, and removes the engines' Agent Identity from project IAM (`setIamPolicy`). Then `check-clean --strict`, `terraform state rm` and `terraform destroy`, which deletes everything in the apply rows above. APIs are never turned off |
| Private Networking (`private_networking.enabled`) | Module `private_egress`: turns on `compute` and `dns`, creates a VPC, subnet, network attachment, PSC global address and forwarding rule (`all-apis`) (`compute.networks/subnetworks/networkAttachments/globalAddresses/globalForwardingRules.create`). Creates a private `run.app.` zone and records (`dns.managedZones.create`, `dns.changes.create`). Creates the agent connectivity template, and grants `gcp-sa-agentgateway` `compute.networkAdmin` and `dns.peer` (project IAM). Module `regional`: a second egress gateway, IAP extension and policy, 25 platform entries with their grant, and a staging bucket, all in the PN region |

### 2.2 The deployer's roles

The **base set is 15 predefined roles**. The optional features add 1 role for GE and 2 for Private Networking. One more role may be needed for image builds (see the row after the table).

| # | Role | Why | Needed by | Status |
|---|---|---|---|---|
| 1 | `roles/resourcemanager.projectIamAdmin` | Project grants: Terraform's `google_project_iam_member` resources (the UI and targets SAs, the Model Armor service agents, the presenters, the PN service agent), `deploy-theme`'s Agent Identity roles, and `down`'s removal of each engine's principal. Also gives `resourcemanager.projects.get/getIamPolicy`, which preflight, Terraform's `data.google_project` and the organization lookup need | `up`, `deploy-theme`, `down`, `reset --all` (read) | verified; works with the two conditions of §5.3 (tested live, §0) |
| 2 | `roles/serviceusage.serviceUsageAdmin` | Turns on the APIs (`google_project_service`, `preflight --fix`). Also has `serviceusage.services.use`, which every call needs because the code sends the project as the quota project | `up`, all | verified |
| 3 | `roles/iam.serviceAccountAdmin` | Creates and deletes `<prefix>-ui` and `<prefix>-targets` (`google_service_account`); lists service accounts (`check-clean`); deletes old leftover SAs (`down`) | `up`, `down`, `check-clean` | verified |
| 4 | `roles/iam.serviceAccountUser` | `iam.serviceAccounts.actAs` on `<prefix>-ui` and `<prefix>-targets` for `gcloud run deploy --service-account`, and on the Cloud Build service account if Cloud Build asks for it | `deploy-theme`, `ui deploy` | tested live (§0) |
| 5 | `roles/storage.admin` | The state bucket and the recordings bucket (API create), the staging bucket(s) (Terraform), bucket IAM grants (Terraform and `ui deploy`), the Cloud Build source bucket, the engine code the `vertexai` SDK uploads, and Terraform state reads and writes | `up`, `deploy-theme`, `ui deploy`, `down` | verified |
| 6 | `roles/artifactregistry.admin` | `artifactregistry.repositories.create/delete` (Terraform). `repoAdmin` and `writer` can't create repositories | `up`, `down` | verified |
| 7 | `roles/cloudbuild.builds.editor` | `gcloud builds submit` for the image builds (`cloudbuild.builds.create/get`) | `deploy-theme`, `ui deploy` | verified |
| 8 | `roles/run.admin` | Deploys and deletes services; `--allow-unauthenticated` and `add-iam-policy-binding` need `run.services.setIamPolicy`, which `run.developer` doesn't have | `deploy-theme`, `ui deploy`, `down` | verified |
| 9 | `roles/aiplatform.user` | Creates, updates, PATCHes (unbinds), lists and deletes Agent Runtime engines (`aiplatform.reasoningEngines.*`) | `deploy-theme`, `reset --all`, `down`, `check-clean` | verified; `roles/aiplatform.expressUser` is enough (tested live, §0) |
| 10 | `roles/networkservices.editor` | Agent gateways (`networkservices.agentGateways.*`) and authz extensions (`networkservices.authzExtensions.*`, including `use` and `update`). This one role covers both, so `networkservices.admin` and `serviceExtensionsAdmin` aren't needed (`admin` adds only tag-binding and `setIamPolicy` permissions on other resource types) | `up`, `reset --all`, `down` | verified |
| 11 | `roles/networksecurity.editor` | Authz policies: create (Terraform), PATCH (`reset --all` repoint), delete (`reset --all`, `down`). `networksecurity.admin` adds only `setIamPolicy` | `up`, `reset --all`, `down` | verified |
| 12 | `roles/agentregistry.editor` | `agentregistry.services.create/update/delete/get/list`; reads of the agent, MCP server and endpoint entries the registry creates | `up`, `deploy-theme`, `down`, `check-clean` | verified |
| 13 | `roles/iap.admin` | IAP IAM policies on registry entries (Terraform's `google_iap_agent_registry_endpoint_iam_member`; `reset --all` pruning, also on the registry root; teardown). The UI's Cloud Run IAP: `--iap` and the `httpsResourceAccessor` grants | `up`, `ui deploy`, `reset --all`, `down`, `check-clean` | verified; the custom `agdemoDeployerIap` (6 IAM-policy permissions) is enough (tested live, §0) |
| 14 | `roles/modelarmor.admin` | `modelarmor.templates.create/update/delete/list/get` (Terraform, `check-clean`, `down`). 16 permissions, smaller than `modelarmor.editor` | `up`, `down`, `check-clean` | verified |
| 15 | `roles/logging.viewer` | `logging.logEntries.list`. It was for following the UI build's log in Cloud Logging (`CLOUD_LOGGING_ONLY`); builds are now submitted `--async` and polled, so nothing in the deployer's commands may need it any more | `ui deploy` (formerly) | **not needed** for the deployer (tested live without it, §0) |
| (16) | ~~`roles/viewer`~~ **not needed** | The live test hit it: a blocking `gcloud builds submit` streams the log, which needs project Viewer for the default logs bucket (and `--suppress-logs` didn't avoid the check). Builds are now submitted with `--async` and polled (`gcloud.submit_build`), which needs only `cloudbuild.builds.editor` | — | tested live (§0) |

**Optional features**

| Feature | Role | Why | Status |
|---|---|---|---|
| Gemini Enterprise (`publish-ge`; `down` and `check-clean` with `gemini_enterprise.app_id` set) | `roles/discoveryengine.agentAdmin` | `discoveryengine.agents.create/update/get/list/delete` on the GE app (15 permissions). If `agents-cli` also reads the app (`discoveryengine.engines.get`), use `roles/discoveryengine.editor` instead | needs live test |
| Private Networking | `roles/compute.networkAdmin` | VPC, subnet, network attachment, PSC address and forwarding rule (`compute.*.create/delete/use`). It's large (about 970 permissions), but no smaller predefined role covers network attachments and PSC forwarding rules together | tested live (§0) |
| Private Networking | `roles/dns.admin` | The private `run.app.` zone and its records; includes `dns.networks.targetWithPeeringZone` for the template's DNS peering | tested live (§0) |
| Private Networking | ~~`roles/servicedirectory.editor`~~ **not needed** | Google's PSC-for-APIs guide lists it for creating the endpoint. The demo doesn't turn on the Service Directory API, and the live test created the endpoint without it | tested live (§0) |
| Private Networking | (no new role) the agent connectivity template | IAM has no permission family for `agentConnectivityTemplates` yet: none appears in `list-testable-permissions`. The live test created it with the 17 roles, so `networkservices.editor` (or nothing) covers it | tested live (§0) |

With a separate `gateway_project_id`, the gateway, extension, policy, registry, IAP and Private Networking rows apply in the **gateway project** too.

### 2.3 Roles that exist only because of project IAM grants

`roles/resourcemanager.projectIamAdmin` is needed for these project grants alone:
- Terraform's `google_project_iam_member`: the UI SA (9 roles), the targets SA (2), the Model Armor callout roles for the egress gateway's own service agent and, with `shared_project_grants`, for `gcp-sa-dep` and `gcp-sa-aiplatform-re` (3 roles each), the presenters' `aiplatform.user`, and the PN `gcp-sa-agentgateway` `networkAdmin` + `dns.peer`;
- `deploy-theme`: 7 roles for each new engine's Agent Identity (`AGENT_ROLES` in `agdemo_core/infra/deploy_theme.py`);
- `down` / `teardown`: removing each engine's principal.

An owner can't make these grants ahead of time. The egress gateway's service agent and each engine's Agent Identity only exist once `up` or `deploy-theme` creates them. So Project IAM Admin is unavoidable for the deployer. To limit it, see the conditional grant in §5.3.

None of the other roles above can grant IAM on the project. The bucket and Cloud Run grants are resource-level (`storage.admin`, `run.admin`), and the IAP grants are IAP resource policies (`iap.admin`).

### 2.4 Compared with preflight and SETUP.md

`agdemo_core/infra/preflight.py` tests 14 permissions. Every one is covered by the roles above. Preflight is missing these, so it can pass for a deployer whose `up` later fails:

- `storage.buckets.create`, `storage.buckets.setIamPolicy` (state, recordings and staging buckets and their grants);
- `run.services.setIamPolicy` (needs `run.admin`; `run.developer` passes preflight's `run.services.create`);
- `iam.serviceAccounts.actAs`;
- the delete permissions `down` uses: `networkservices.agentGateways.delete`, `aiplatform.reasoningEngines.delete`, `agentregistry.services.delete`, `modelarmor.templates.delete`, `iam.serviceAccounts.delete`, `run.services.delete`, `networksecurity.authzPolicies.delete`;
- `networkservices.authzExtensions.use`, `networksecurity.authzPolicies.update`;
- `iap.webServices.setIamPolicy` and `iap.webTypes.setIamPolicy`. Preflight tests only `iap.webServiceVersions.setIamPolicy`. `testIamPermissions` shows that a registry entry accepts `iap.webServices.*` and `iap.webServiceVersions.*`, and the registry root `iap.webTypes.*` too (§3, note 1). So the entry itself is probably checked with `iap.webServices.setIamPolicy`;
- with Private Networking on: `compute.networks.create`, `compute.networkAttachments.create`, `compute.globalForwardingRules.create`, `dns.managedZones.create`;
- with `gemini_enterprise.app_id` set: `discoveryengine.agents.create`, `discoveryengine.agents.delete`.

Suggestion: add these to `PERMS`, with the PN and GE ones only when those features are on. Once §6 has run, preflight can name the roles from §2.2 in its fix text.

`docs/SETUP.md` ("Your permissions") used to list admin rights by product and missed Cloud Storage, Service Account User, Logging Viewer and the Private Networking roles. Since the live tests (§0) it links to §2.2 and names the two Private Networking roles.

## 3. Runtime service accounts

Their roles come from `infra/terraform/modules/base/iam.tf` (`ui_roles`, `targets_roles`) plus two bucket-level grants. The UI's roles are explained in ARCHITECTURE.md §12.

### 3.1 `<prefix>-ui` (the demo UI on Cloud Run)

What it calls (from `ui/backend/agdemo_ui/*`, `agdemo_core/policies/*`, `agdemo_core/gcp/*`, `agdemo_core/recordings.py`):
- **Gateway binding:** GET and PATCH `reasoningEngines/<id>` (`agentGatewayConfig`), list the engine's operations;
- **Tests, probes and ingress calls:** `:streamQuery` on the engines;
- **Egress allows (A2A, MCP server, MCP tool):** IAP `getIamPolicy` and `setIamPolicy` on `iap_web/agentRegistry/<kind>/<id>`. It never touches the registry root;
- **Model Armor checkbox:** create, delete, get and list the `CONTENT_AUTHZ` authz policies that point at the Model Armor extension (`authzExtensions.use`). It never changes extensions or enforcement mode (that's Policy Studio, a separate repo, and `reset --all`);
- **Reads:** gateways, the registry (services, agents, MCP servers, endpoints), Cloud Logging `entries:list` in the gateway project (gateway logs panel);
- **Recordings:** objects get and upload in `gs://<project>-<prefix>-recordings/recordings`;
- every call sends the project as the quota project (`x-goog-user-project`).

| Role (today) | Needed? | Proposed replacement | Status |
|---|---|---|---|
| `roles/aiplatform.user` (459 permissions) | Yes: `aiplatform.reasoningEngines.get/list/update/query`, `aiplatform.operations.list` | **`roles/aiplatform.expressUser`** (predefined, 38 permissions, beta). It includes `reasoningEngines.create/delete/get/list/query/update`, `operations.list`, `endpoints.predict`, sessions and memories. Alternative: the custom role `agdemoEngineOperator` (§5.2) | tested live: `expressUser` is enough (§0) |
| `roles/agentregistry.viewer` (24) | Yes: list and get services and the entries the registry creates from them | Keep | verified |
| `roles/iap.admin` (36) | Yes, but only for `get/setIamPolicy` on registry entries. It also brings IAP settings, tunnels and every other IAP resource type | **Custom role `agdemoRegistryEgressAdmin`**: `iap.webServices.getIamPolicy`, `iap.webServices.setIamPolicy`, `iap.webServiceVersions.getIamPolicy`, `iap.webServiceVersions.setIamPolicy` (note 1) | tested live: the custom role is enough (§0) |
| `roles/networkservices.viewer` (57) | Yes: `networkservices.agentGateways.get/list` (gateway status) | Keep, or fold into `agdemoGatewayPolicyEditor` (§5.2) | tested live: folded into `agdemoGatewayPolicyEditor` (§0) |
| `roles/networksecurity.editor` (165) | Yes, for `networksecurity.authzPolicies.create/delete/get/list` (Model Armor checkbox, policy status) | **Custom role `agdemoGatewayPolicyEditor`** (§5.2) | tested live (§0) |
| `roles/networkservices.serviceExtensionsAdmin` (52) | Only for `networkservices.authzExtensions.use` (and `get`) when the `CONTENT_AUTHZ` policy references the Model Armor extension | Fold into `agdemoGatewayPolicyEditor` (`authzExtensions.get/list/use`) | tested live: folded (§0) |
| `roles/modelarmor.viewer` (6) | **Probably not.** No UI code path reads Model Armor templates: the checkbox only reads and writes authz policies. `get_template` and `sanitize_prompt` are used only by the CLI | Remove | tested live: not needed (§0) |
| `roles/logging.viewer` (34) | Yes: `logging.logEntries.list` for the gateway logs panel (`agdemo_core/gcp/gateway_logs.py`) | Keep | verified |
| `roles/serviceusage.serviceUsageConsumer` (19) | Yes: `serviceusage.services.use`, because the calls send the project as the quota project | Keep | verified |
| `roles/storage.objectUser` on the recordings bucket (Terraform) | Yes: load and save recordings. Saving over an existing recording needs `storage.objects.delete`, so `objectCreator` + `objectViewer` isn't enough | Keep | verified |
| `roles/storage.objectUser` on the staging bucket (`ui deploy`) | Only when state.json has no recordings bucket (the old fallback) | Skip the grant when a recordings bucket exists (small change in `agdemo_core/infra/ui.py`) | tested live: not needed with a recordings bucket (§0) |

**Note 1: which IAP permission covers a registry entry.** A read-only `testIamPermissions` (owner credentials) gave these results:
- on `.../iap_web/agentRegistry/endpoints/<id>`: `iap.webServices.*` and `iap.webServiceVersions.*` are accepted; `iap.web.*` and `iap.webTypes.*` are rejected as "not valid for this resource";
- on the registry root `.../iap_web/agentRegistry`: `iap.webTypes.*`, `iap.webServices.*` and `iap.webServiceVersions.*` are accepted;
- on the UI's `iap_web/cloud_run-<region>/services/<prefix>-ui`: `iap.webServices.*` and `iap.webServiceVersions.*` are accepted.

So a registry entry sits at the `webServices` level, like a Cloud Run service, and its `setIamPolicy` is most likely checked with `iap.webServices.setIamPolicy`. The custom role includes both permissions until §6 settles it. There's no `roles/iap.policyAdmin`. The nearest predefined roles (`iap.settingsAdmin`, `iap.editor`, `iap.viewer`) have **no** `setIamPolicy` permission, so `iap.admin` is the only predefined role that works.

**Risk that remains with either role:** a project-level `iap.webServices.setIamPolicy` also lets `<prefix>-ui` change who can open the UI itself (its Cloud Run IAP resource is also a `webService`). To close that, add an IAM condition on the custom-role binding that limits it to `resource.name` under `.../iap_web/agentRegistry`. Whether IAP evaluates `resource.name` that way needs a live test.

**Gateway use:** none of the UI's roles has `networkservices.agentGateways.use`, and `networkservices.viewer` doesn't include it. Binding from the deployed UI worked in both live tests (§0, Internet and Private Networking), so the engine PATCH and the authz-policy target don't check it on the caller.

**Split projects:** with a separate `gateway_project_id`, the IAP, registry, authz policy, extension, gateway and logging roles are needed in the **gateway project**. Terraform grants them only in `project_id` today, which is a gap for split setups.

### 3.2 `<prefix>-targets` (Cloud Run demo agents and MCP servers)

`runtimes/mcp_server` makes no Google API calls. `runtimes/a2a_agent` calls Gemini on Vertex AI through `google-genai` (`aiplatform.endpoints.predict`).

| Role (today) | Needed? | Proposed replacement | Status |
|---|---|---|---|
| `roles/aiplatform.user` (459) | Only `aiplatform.endpoints.predict`, used by the A2A agents | **`roles/aiplatform.expressUser`** (38; has `endpoints.predict`), or a custom role with `aiplatform.endpoints.predict` only | tested live: `expressUser` is enough (§0) |
| `roles/logging.logWriter` | **No.** The runtimes don't use the Cloud Logging API. Cloud Run collects stdout and stderr without any role on the service account | Remove | tested live: not needed, container logs still appear (§0) |

### 3.3 Aside: the engines' Agent Identity (not asked, worth a later test)

`deploy-theme` grants each orchestrator's Agent Identity 7 project roles (`AGENT_ROLES`). In this project, the project's Agent Identity principalSet already holds `roles/aiplatform.agentDefaultAccess`. That role includes:
- `logging.logEntries.create`, `monitoring.timeSeries.create`, `cloudtrace.traces.patch`, `telemetry.traces.write`;
- `serviceusage.services.use`, `resourcemanager.projects.get`;
- `aiplatform.endpoints.predict`.

So `logging.logWriter`, `monitoring.metricWriter`, `cloudtrace.agent`, `browser` and `serviceUsageConsumer` may be redundant (verified by permission names; needs live test, and a check on whether that grant appears automatically in a fresh project).

## 4. Summary of the proposed trims

| Identity | Today | Proposed | Change |
|---|---|---|---|
| Deployer | Owner (SETUP.md: "simplest") | 15 predefined roles (+1 GE, +2 PN), or the trimmed 14 below | §2.2; both tested live (§0) |
| Deployer | `roles/aiplatform.user` | `roles/aiplatform.expressUser` | tested live, works (§0) |
| Deployer | `roles/iam.serviceAccountUser` on the project | The same role on `<prefix>-ui`, `<prefix>-targets` and the Cloud Build service account only (e.g. a Terraform `google_service_account_iam_member` for the deployer) | not tested (the trims run kept it on the project) |
| Deployer | `roles/iap.admin` | Custom `agdemoDeployerIap` (§5.2). Keep `iap.admin` if `run deploy --iap` needs IAP settings permissions, which are only at the TESTING level for custom roles | tested live, works without the settings permissions (§0) |
| Deployer | Project IAM Admin, unconditional | Project IAM Admin with a condition on the roles it may grant (§5.3) | tested live with two conditions (§5.3, §0) |
| Deployer | `roles/viewer` (if build logs need it) | Not needed: builds are submitted `--async` and polled | done, tested live (§0) |
| `<prefix>-ui` | 9 project roles | 6 grants: `aiplatform.expressUser`, `agentregistry.viewer`, `logging.viewer`, `serviceusage.serviceUsageConsumer`, custom `agdemoRegistryEgressAdmin`, custom `agdemoGatewayPolicyEditor` (`networkservices.viewer` folded into the last one) | tested live, works (§0). Drops `iap.admin`, `networksecurity.editor`, `serviceExtensionsAdmin`, `networkservices.viewer`, `modelarmor.viewer`; `aiplatform.user` → `expressUser` |
| `<prefix>-ui` | `objectUser` on staging + recordings | `objectUser` on recordings only | tested live (§0); needs the small `ui.py` change |
| `<prefix>-targets` | `aiplatform.user`, `logging.logWriter` | `aiplatform.expressUser` | tested live, works (§0) |

## 5. Custom-role proposals

### 5.1 When to use them

Use a custom role only where every predefined role is much broader than the use:
- `iap.admin` for one IAP operation;
- `networksecurity.editor` (165 permissions) and `serviceExtensionsAdmin` (52) for one policy type.

Custom roles are project-level. They need `roles/iam.roleAdmin` (an owner) to create, and they have to be updated by hand if Google renames a permission.

### 5.2 Permission lists

Each permission below is listed in `gcloud iam list-testable-permissions` for the project, and none is marked as unsupported in custom roles.

**`agdemoRegistryEgressAdmin`** (for `<prefix>-ui`): grant and revoke `roles/iap.egressor` on registry entries.
```
iap.webServices.getIamPolicy
iap.webServices.setIamPolicy
iap.webServiceVersions.getIamPolicy
iap.webServiceVersions.setIamPolicy
```

**`agdemoGatewayPolicyEditor`** (for `<prefix>-ui`): the Model Armor `CONTENT_AUTHZ` policies, and reading gateways and extensions.
```
networksecurity.authzPolicies.create
networksecurity.authzPolicies.delete
networksecurity.authzPolicies.get
networksecurity.authzPolicies.list
networksecurity.operations.get
networkservices.authzExtensions.get
networkservices.authzExtensions.list
networkservices.authzExtensions.use
networkservices.agentGateways.get
networkservices.agentGateways.list
networkservices.operations.get
```
(If the live test shows that creating a policy that targets a gateway checks `networkservices.agentGateways.use`, add it.)

**`agdemoEngineOperator`** (optional, for `<prefix>-ui`, if `aiplatform.expressUser` is still too broad):
```
aiplatform.reasoningEngines.get
aiplatform.reasoningEngines.list
aiplatform.reasoningEngines.update
aiplatform.reasoningEngines.query
aiplatform.operations.list
```

**`agdemoDeployerIap`** (optional, for the deployer instead of `iap.admin`): registry entries, the registry root (`reset --all` pruning), and the UI's Cloud Run IAP grants.
```
iap.webTypes.getIamPolicy
iap.webTypes.setIamPolicy
iap.webServices.getIamPolicy
iap.webServices.setIamPolicy
iap.webServiceVersions.getIamPolicy
iap.webServiceVersions.setIamPolicy
# only if `gcloud beta run deploy --iap` asks for it (TESTING level in custom roles):
iap.webServices.updateSettings
iap.webServices.getSettings
```

### 5.3 Limiting Project IAM Admin

Grant `roles/resourcemanager.projectIamAdmin` to the deployer **twice**, each with a condition, so it can only add or remove the roles the demo's tooling grants (Terraform, `deploy-theme`, `down`). IAM allows at most **10** roles in one `hasOnly()` list, so one condition can't hold them all. A change is allowed when either binding allows it, so each command's change must fit inside one list: `down` removes all of an Agent Identity principal's roles in one change (list A), and all of a service account's roles in one change (list B). Tested live (§0) with the trimmed UI roles of §4; with the untrimmed `iam.tf`, list B would need `iap.admin`, `networkservices.viewer`, `networksecurity.editor`, `serviceExtensionsAdmin` and `modelarmor.viewer` too, which doesn't fit in 10.

List A, the Agent Identity roles (`deploy-theme`) and the Model Armor service-agent roles:
```
api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly([
  'roles/aiplatform.user', 'roles/agentregistry.viewer', 'roles/logging.logWriter',
  'roles/monitoring.metricWriter', 'roles/cloudtrace.agent', 'roles/browser',
  'roles/serviceusage.serviceUsageConsumer', 'roles/modelarmor.calloutUser', 'roles/modelarmor.user'
])
```
List B, the `<prefix>-ui` and `<prefix>-targets` roles (trimmed, §4) and the Private Networking service-agent roles:
```
api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly([
  'roles/aiplatform.expressUser', 'roles/agentregistry.viewer', 'roles/logging.viewer',
  'roles/serviceusage.serviceUsageConsumer',
  'projects/<PROJECT_ID>/roles/agdemoRegistryEgressAdmin', 'projects/<PROJECT_ID>/roles/agdemoGatewayPolicyEditor',
  'roles/compute.networkAdmin', 'roles/dns.peer', 'roles/modelarmor.calloutUser', 'roles/modelarmor.user'
])
```
Put each in a YAML file (`title`, `expression`) and grant with `gcloud projects add-iam-policy-binding <PROJECT_ID> --member=... --role=roles/resourcemanager.projectIamAdmin --condition-from-file=<file>`: `--condition=expression=...` splits on the commas in the list. This stops the deployer from granting itself Owner. Terraform's read-modify-write of the whole policy worked with it (§0).

## 6. How to test least privilege

The goal is to run the whole lifecycle as a service account that holds **only** the §2.2 roles, in a separate checkout and with its own prefix, so the live demo and your own credentials are never touched. It follows the same pattern as T5 (TERRAFORM_PLAN.md §7i).

### 6.1 Setup (an owner runs this once; run on 2026-10-22, results in §0)

Placeholders: `<PROJECT_ID>`, `<YOU>` (your user, e.g. `user:you@example.com`), `<GCLOUD>` (path to gcloud).

```bash
PROJECT=<PROJECT_ID>
SA=agdtest-deployer
SA_EMAIL=$SA@$PROJECT.iam.gserviceaccount.com
ME=<YOU>

# 1. Snapshot project IAM first (to compare after down)
gcloud projects get-iam-policy $PROJECT --format=json > /tmp/agdtest-iam-before.json

# 2. The test deployer, which you may impersonate
gcloud iam service-accounts create $SA --project=$PROJECT \
  --display-name="agdemo least-privilege test deployer"
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL --project=$PROJECT \
  --member=$ME --role=roles/iam.serviceAccountTokenCreator

# 3. Phase A roles: the 15 predefined roles of section 2.2
for ROLE in \
  roles/resourcemanager.projectIamAdmin roles/serviceusage.serviceUsageAdmin \
  roles/iam.serviceAccountAdmin roles/iam.serviceAccountUser roles/storage.admin \
  roles/artifactregistry.admin roles/cloudbuild.builds.editor roles/run.admin \
  roles/aiplatform.user roles/networkservices.editor roles/networksecurity.editor \
  roles/agentregistry.editor roles/iap.admin roles/modelarmor.admin roles/logging.viewer
do
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA_EMAIL \
    --role=$ROLE --condition=None --quiet > /dev/null && echo "granted $ROLE"
done

# Optional phases (add only when testing them):
#   GE:  roles/discoveryengine.agentAdmin
#   PN:  roles/compute.networkAdmin roles/dns.admin
```

Custom roles for Phase C (§5.2). These need `roles/iam.roleAdmin`, so you or an owner create them:
```bash
gcloud iam roles create agdemoRegistryEgressAdmin --project=$PROJECT --stage=BETA \
  --title="agdemo UI: registry egress grants" \
  --permissions=iap.webServices.getIamPolicy,iap.webServices.setIamPolicy,iap.webServiceVersions.getIamPolicy,iap.webServiceVersions.setIamPolicy
gcloud iam roles create agdemoGatewayPolicyEditor --project=$PROJECT --stage=BETA \
  --title="agdemo UI: Model Armor authz policies" \
  --permissions=networksecurity.authzPolicies.create,networksecurity.authzPolicies.delete,networksecurity.authzPolicies.get,networksecurity.authzPolicies.list,networksecurity.operations.get,networkservices.authzExtensions.get,networkservices.authzExtensions.list,networkservices.authzExtensions.use,networkservices.agentGateways.get,networkservices.agentGateways.list,networkservices.operations.get
```

### 6.2 A separate checkout (like T5)

`config/state.json` and `infra/terraform/.terraform` belong to one checkout, so use a git worktree:

```bash
cd <repo>
git worktree add -b lp-test ../agdemo-lp-test main
cd ../agdemo-lp-test
cp <repo>/config/demo.yaml config/demo.yaml     # git-ignored; then edit:
#   environment.resource_prefix: agdtest
#   environment.region: us-east1                  # a region with no demo gateway (T5 used it)
#   terraform.shared_project_grants: false        # don't manage the live demo's shared grants
#   themes.enabled: [helpdesk]
#   gemini_enterprise.app_id: ""                  # Phase A; set it for the GE phase
#   private_networking.enabled: false             # Phase A; see Phase D
```

The state bucket is shared (`<project>-agdemo-tfstate`), and the `agdtest/...` prefix keeps the state separate. Before every apply, check that the plan only names `agdtest-*` resources, as T5 did.

### 6.3 Run as the service account without touching your own credentials

Use a **separate gcloud config directory**. Your normal `~/.config/gcloud` (and the ADC file in it) is never read or written.

```bash
export CLOUDSDK_CONFIG=$HOME/.config/gcloud-agdtest          # separate gcloud config dir
gcloud auth login                                              # your user, stored only in this dir
gcloud config set project $PROJECT
gcloud config set auth/impersonate_service_account $SA_EMAIL  # every gcloud call: as the SA
gcloud auth application-default login --impersonate-service-account=$SA_EMAIL
export GOOGLE_APPLICATION_CREDENTIALS=$CLOUDSDK_CONFIG/application_default_credentials.json
export AGDEMO_GCLOUD=<GCLOUD>
```

How each part picks up the service account:
- **gcloud subprocesses** (`builds submit`, `run deploy`, `iap web add-iam-policy-binding`, `services describe`) read `auth/impersonate_service_account` from `CLOUDSDK_CONFIG`. The same thing as an environment variable: `export CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=$SA_EMAIL`. `./agdemo` passes the environment through to gcloud.
- **The Python SDK** (`agdemo_core/gcp/rest.py`, `recordings.py`, the `vertexai` SDK, `agents-cli`) uses `google.auth.default()`. That reads `GOOGLE_APPLICATION_CREDENTIALS`, here an `impersonated_service_account` file that holds your user's refresh token and the target SA. As far as I know, `google-auth` doesn't read `GOOGLE_IMPERSONATE_SERVICE_ACCOUNT`, so the file is what makes Python run as the SA.
- **Terraform** runs as a subprocess with the same environment. The Google provider and the GCS backend read the same `GOOGLE_APPLICATION_CREDENTIALS` file; the provider's auth library supports the impersonated type. `GOOGLE_IMPERSONATE_SERVICE_ACCOUNT=$SA_EMAIL` is the provider's other method, but **don't set it on top of the impersonated ADC file**: the SA would then try to impersonate itself, which needs Token Creator on itself and fails with a 403.

Check the identity before running anything:
```bash
gcloud config get auth/impersonate_service_account       # -> agdtest-deployer@...
gcloud auth print-access-token > /dev/null && echo gcloud-ok
uv run python -c 'import google.auth; c,_=google.auth.default(); print(type(c).__module__, getattr(c,"service_account_email",None))'
# expect: google.auth.impersonated_credentials agdtest-deployer@<PROJECT_ID>.iam.gserviceaccount.com
```
Preflight's "gcloud auth" line still shows your own account (the `core/account`). That's expected: the calls are made as the SA.

### 6.4 The phases

Each `up` takes about 15–20 minutes and each `down` about 10 (T5). On a 403, write down the permission it names, add the smallest role that has it (as the owner, in your normal shell), and re-run the same command (`up` is idempotent).

| Phase | Deployer roles | `demo.yaml` | Commands (in the worktree, with the §6.3 environment) |
|---|---|---|---|
| A | The 15 roles | base (above) | `./agdemo preflight`, `./agdemo up`, `./agdemo check-clean`, then in the UI (Live): toggle each helpdesk policy, Model Armor on/off, open gateway logs and registry, run and record a test, Reset. Then `./agdemo reset --all`, `./agdemo down --yes --prefix agdtest` |
| B | A, with the deployer trims: `aiplatform.user` → `aiplatform.expressUser`; the conditional Project IAM Admin (§5.3); `iam.serviceAccountUser` per SA | base | `up` / `down` again |
| C | A | in the worktree only, edit `infra/terraform/modules/base/iam.tf`: `ui_roles` = §4's 6 grants (custom roles as `projects/<PROJECT_ID>/roles/...`), `targets_roles = ["roles/aiplatform.expressUser"]`; skip the staging-bucket grant in `ui.py` | `up` with the UI, then the same UI checklist as A (this tests `agdtest-ui` and `agdtest-targets`: the A2A agents `kb-agent` and `hr-records-agent` call Gemini). `down` |
| D (optional; run 2026-10-22, §0) | A + `compute.networkAdmin`, `dns.admin` | `private_networking.enabled: true`, `private_networking.region:` a region with **no** egress gateway in the project (not the live demo's base or PN region, and not one with another team's gateway), `themes.enabled: [helpdesk, privnet]` | `up`, the privnet checks, `down`. Also set `region:` in `themes/privnet/theme.yaml` to the same region, or `deploy-theme` stops with a region mismatch |
| E (optional) | A + `discoveryengine.agentAdmin` | `gemini_enterprise.app_id` set | `./agdemo publish-ge helpdesk`, `check-clean` (GE agents listed), `down` (GE unregister). This registers an `agdtest` agent in a **shared** GE app until `down` |

To test the UI SA from your machine instead of Cloud Run, use a third config dir that impersonates `agdtest-ui@...`. Give yourself Token Creator on it after `up`; `down` deletes the SA. Then run `./agdemo ui local --mode live`.

### 6.5 What to record

For each phase:
- **Pass or fail per step**, with the exact error of every 403 (it names the permission and the resource), the role you added and whether the re-run passed;
- **Timings** of `up` and `down` (compare with T5);
- **The permissions actually used**, from the Admin Activity audit log (as the owner, from your normal shell):
  ```bash
  gcloud logging read "logName:\"cloudaudit.googleapis.com%2Factivity\" AND protoPayload.authenticationInfo.principalEmail=\"$SA_EMAIL\"" \
    --project=$PROJECT --freshness=1d --format=json \
    | jq -r '.[].protoPayload.authorizationInfo[]? | select(.granted) | .permission' | sort | uniq -c
  ```
  (Reads only show up if Data Access logs are on for those services. They're off by default, and turning them on is a project setting change for an owner to decide.) Repeat with `principalEmail="agdtest-ui@<PROJECT_ID>.iam.gserviceaccount.com"` for Phase C;
- **The note 1 answer:** which `iap.*.setIamPolicy` appears in the audit entries for registry entries, the registry root and the Cloud Run IAP resource;
- whether `gcloud builds submit` (default logs) failed without `roles/viewer`; whether `run deploy --iap` needed IAP settings permissions; whether the connectivity template or PSC create named a permission; whether `agents-cli` needed more than `agentAdmin`;
- **A clean finish:** after `down`, `gcloud projects get-iam-policy $PROJECT --format=json` compared with `/tmp/agdtest-iam-before.json`. The only differences should be the `agdtest-deployer` bindings. Also read-only lists in the test region(s) with no `agdtest-*` resources left, and the live demo's lists unchanged.

### 6.6 Clean up after the test

```bash
for ROLE in <each role granted in 6.1>; do
  gcloud projects remove-iam-policy-binding $PROJECT --member=serviceAccount:$SA_EMAIL --role=$ROLE --condition=None --quiet
done
gcloud iam service-accounts delete $SA_EMAIL --project=$PROJECT --quiet
gcloud iam roles delete agdemoRegistryEgressAdmin --project=$PROJECT    # if not kept
gcloud iam roles delete agdemoGatewayPolicyEditor --project=$PROJECT
rm -rf $HOME/.config/gcloud-agdtest                                     # the separate config dir
git worktree remove ../agdemo-lp-test && git branch -D lp-test
```
Remove the bindings **before** deleting the SA, or they stay in the policy as `deleted:serviceAccount:...`. The state prefix `agdtest/...` and `<project>-agdtest-recordings` stay by design, as after T5.
