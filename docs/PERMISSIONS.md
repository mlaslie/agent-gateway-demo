# Permissions: the minimum IAM roles

> **Status (2026-10-22): the deployer's 15 base roles (§2.2) were tested live and are enough** for the full lifecycle of an Internet-mode environment: `up`, a Live scenario through the deployed UI, and `down`. The results are in §0. The trims in §4 and §5, and the GE and Private Networking roles, are still untested.
>
> Originally (2026-10-21) this page was worked out from the code and from read-only checks. Every role below comes from reading what the demo calls: `infra/terraform/**`, `agdemo_core/**`, `ui/backend/**` and `runtimes/**`. Permission names were checked against the live role catalogue (`gcloud iam roles describe`, the IAM `roles.list` API) and with `testIamPermissions`. Nothing was created or granted. Each suggestion is marked **verified** (the permission names were checked) or **needs live test** (only a run with these roles will show whether it's enough). The test plan is in §6.

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

Not covered by this test: `publish-ge` (GE role), Private Networking (its 2 roles), the custom-role and `expressUser` trims (§4, §5), the condition on Project IAM Admin (§5.3), and whether `roles/logging.viewer` (row 15) is still needed now that builds don't stream logs.

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
| 1 | `roles/resourcemanager.projectIamAdmin` | Project grants: Terraform's `google_project_iam_member` resources (the UI and targets SAs, the Model Armor service agents, the presenters, the PN service agent), `deploy-theme`'s Agent Identity roles, and `down`'s removal of each engine's principal. Also gives `resourcemanager.projects.get/getIamPolicy`, which preflight, Terraform's `data.google_project` and the organization lookup need | `up`, `deploy-theme`, `down`, `reset --all` (read) | verified |
| 2 | `roles/serviceusage.serviceUsageAdmin` | Turns on the APIs (`google_project_service`, `preflight --fix`). Also has `serviceusage.services.use`, which every call needs because the code sends the project as the quota project | `up`, all | verified |
| 3 | `roles/iam.serviceAccountAdmin` | Creates and deletes `<prefix>-ui` and `<prefix>-targets` (`google_service_account`); lists service accounts (`check-clean`); deletes old leftover SAs (`down`) | `up`, `down`, `check-clean` | verified |
| 4 | `roles/iam.serviceAccountUser` | `iam.serviceAccounts.actAs` on `<prefix>-ui` and `<prefix>-targets` for `gcloud run deploy --service-account`, and on the Cloud Build service account if Cloud Build asks for it | `deploy-theme`, `ui deploy` | tested live (§0) |
| 5 | `roles/storage.admin` | The state bucket and the recordings bucket (API create), the staging bucket(s) (Terraform), bucket IAM grants (Terraform and `ui deploy`), the Cloud Build source bucket, the engine code the `vertexai` SDK uploads, and Terraform state reads and writes | `up`, `deploy-theme`, `ui deploy`, `down` | verified |
| 6 | `roles/artifactregistry.admin` | `artifactregistry.repositories.create/delete` (Terraform). `repoAdmin` and `writer` can't create repositories | `up`, `down` | verified |
| 7 | `roles/cloudbuild.builds.editor` | `gcloud builds submit` for the image builds (`cloudbuild.builds.create/get`) | `deploy-theme`, `ui deploy` | verified |
| 8 | `roles/run.admin` | Deploys and deletes services; `--allow-unauthenticated` and `add-iam-policy-binding` need `run.services.setIamPolicy`, which `run.developer` doesn't have | `deploy-theme`, `ui deploy`, `down` | verified |
| 9 | `roles/aiplatform.user` | Creates, updates, PATCHes (unbinds), lists and deletes Agent Runtime engines (`aiplatform.reasoningEngines.*`) | `deploy-theme`, `reset --all`, `down`, `check-clean` | verified (trim in §5) |
| 10 | `roles/networkservices.editor` | Agent gateways (`networkservices.agentGateways.*`) and authz extensions (`networkservices.authzExtensions.*`, including `use` and `update`). This one role covers both, so `networkservices.admin` and `serviceExtensionsAdmin` aren't needed (`admin` adds only tag-binding and `setIamPolicy` permissions on other resource types) | `up`, `reset --all`, `down` | verified |
| 11 | `roles/networksecurity.editor` | Authz policies: create (Terraform), PATCH (`reset --all` repoint), delete (`reset --all`, `down`). `networksecurity.admin` adds only `setIamPolicy` | `up`, `reset --all`, `down` | verified |
| 12 | `roles/agentregistry.editor` | `agentregistry.services.create/update/delete/get/list`; reads of the agent, MCP server and endpoint entries the registry creates | `up`, `deploy-theme`, `down`, `check-clean` | verified |
| 13 | `roles/iap.admin` | IAP IAM policies on registry entries (Terraform's `google_iap_agent_registry_endpoint_iam_member`; `reset --all` pruning, also on the registry root; teardown). The UI's Cloud Run IAP: `--iap` and the `httpsResourceAccessor` grants | `up`, `ui deploy`, `reset --all`, `down`, `check-clean` | verified (a custom alternative is in §5) |
| 14 | `roles/modelarmor.admin` | `modelarmor.templates.create/update/delete/list/get` (Terraform, `check-clean`, `down`). 16 permissions, smaller than `modelarmor.editor` | `up`, `down`, `check-clean` | verified |
| 15 | `roles/logging.viewer` | `logging.logEntries.list`. `gcloud builds submit` follows the UI build's log in Cloud Logging (`CLOUD_LOGGING_ONLY`) | `ui deploy` | held during the live test; builds no longer stream logs (§0), so it may now be removable: untested |
| (16) | ~~`roles/viewer`~~ **not needed** | The live test hit it: a blocking `gcloud builds submit` streams the log, which needs project Viewer for the default logs bucket (and `--suppress-logs` didn't avoid the check). Builds are now submitted with `--async` and polled (`gcloud.submit_build`), which needs only `cloudbuild.builds.editor` | — | tested live (§0) |

**Optional features**

| Feature | Role | Why | Status |
|---|---|---|---|
| Gemini Enterprise (`publish-ge`; `down` and `check-clean` with `gemini_enterprise.app_id` set) | `roles/discoveryengine.agentAdmin` | `discoveryengine.agents.create/update/get/list/delete` on the GE app (15 permissions). If `agents-cli` also reads the app (`discoveryengine.engines.get`), use `roles/discoveryengine.editor` instead | needs live test |
| Private Networking | `roles/compute.networkAdmin` | VPC, subnet, network attachment, PSC address and forwarding rule (`compute.*.create/delete/use`). It's large (about 970 permissions), but no smaller predefined role covers network attachments and PSC forwarding rules together | verified |
| Private Networking | `roles/dns.admin` | The private `run.app.` zone and its records; includes `dns.networks.targetWithPeeringZone` for the template's DNS peering | verified |
| Private Networking | (maybe) `roles/servicedirectory.editor` | Google's PSC-for-APIs guide lists it for creating the endpoint. The demo doesn't turn on the Service Directory API, so it's probably not needed | needs live test |
| Private Networking | (no new role) the agent connectivity template | IAM has no permission family for `agentConnectivityTemplates` yet: none appears in `list-testable-permissions`. The create may check the `networkservices.agentGateways.*` permissions or nothing at all | needs live test |

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

`docs/SETUP.md` ("Your permissions") lists admin rights for Network Services, Network Security, Service Extensions, IAP, Agent Registry, Vertex AI / Agent Runtime, Cloud Run, Model Armor, Artifact Registry, Cloud Build, Service Accounts, Project IAM and Service Usage. It's missing **Cloud Storage** (`storage.admin`: the state, recordings and staging buckets), **Service Account User** (`actAs` for the Cloud Run deploys), **Logging Viewer** (build logs), and the optional **Compute Network Admin + DNS Admin** (Private Networking) and **Discovery Engine** (GE). "Service Extensions" is already part of `networkservices.editor`. When §6 confirms the list, that paragraph can link here.

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
| `roles/aiplatform.user` (459 permissions) | Yes: `aiplatform.reasoningEngines.get/list/update/query`, `aiplatform.operations.list` | **`roles/aiplatform.expressUser`** (predefined, 38 permissions, beta). It includes `reasoningEngines.create/delete/get/list/query/update`, `operations.list`, `endpoints.predict`, sessions and memories. Alternative: the custom role `agdemoEngineOperator` (§5.2) | verified by permission names; needs live test (the PATCH's long-running operation polling) |
| `roles/agentregistry.viewer` (24) | Yes: list and get services and the entries the registry creates from them | Keep | verified |
| `roles/iap.admin` (36) | Yes, but only for `get/setIamPolicy` on registry entries. It also brings IAP settings, tunnels and every other IAP resource type | **Custom role `agdemoRegistryEgressAdmin`**: `iap.webServices.getIamPolicy`, `iap.webServices.setIamPolicy`, `iap.webServiceVersions.getIamPolicy`, `iap.webServiceVersions.setIamPolicy` (note 1) | verified by permission names (all four are GA and allowed in custom roles); needs live test (which of the two the API checks) |
| `roles/networkservices.viewer` (57) | Yes: `networkservices.agentGateways.get/list` (gateway status) | Keep, or fold into `agdemoGatewayPolicyEditor` (§5.2) | verified |
| `roles/networksecurity.editor` (165) | Yes, for `networksecurity.authzPolicies.create/delete/get/list` (Model Armor checkbox, policy status) | **Custom role `agdemoGatewayPolicyEditor`** (§5.2) | verified by permission names; needs live test |
| `roles/networkservices.serviceExtensionsAdmin` (52) | Only for `networkservices.authzExtensions.use` (and `get`) when the `CONTENT_AUTHZ` policy references the Model Armor extension | Fold into `agdemoGatewayPolicyEditor` (`authzExtensions.get/list/use`) | verified by permission names; needs live test |
| `roles/modelarmor.viewer` (6) | **Probably not.** No UI code path reads Model Armor templates: the checkbox only reads and writes authz policies. `get_template` and `sanitize_prompt` are used only by the CLI | Remove | needs live test |
| `roles/logging.viewer` (34) | Yes: `logging.logEntries.list` for the gateway logs panel (`agdemo_core/gcp/gateway_logs.py`) | Keep | verified |
| `roles/serviceusage.serviceUsageConsumer` (19) | Yes: `serviceusage.services.use`, because the calls send the project as the quota project | Keep | verified |
| `roles/storage.objectUser` on the recordings bucket (Terraform) | Yes: load and save recordings. Saving over an existing recording needs `storage.objects.delete`, so `objectCreator` + `objectViewer` isn't enough | Keep | verified |
| `roles/storage.objectUser` on the staging bucket (`ui deploy`) | Only when state.json has no recordings bucket (the old fallback) | Skip the grant when a recordings bucket exists (small change in `agdemo_core/infra/ui.py`) | verified (from the code) |

**Note 1: which IAP permission covers a registry entry.** A read-only `testIamPermissions` (owner credentials) gave these results:
- on `.../iap_web/agentRegistry/endpoints/<id>`: `iap.webServices.*` and `iap.webServiceVersions.*` are accepted; `iap.web.*` and `iap.webTypes.*` are rejected as "not valid for this resource";
- on the registry root `.../iap_web/agentRegistry`: `iap.webTypes.*`, `iap.webServices.*` and `iap.webServiceVersions.*` are accepted;
- on the UI's `iap_web/cloud_run-<region>/services/<prefix>-ui`: `iap.webServices.*` and `iap.webServiceVersions.*` are accepted.

So a registry entry sits at the `webServices` level, like a Cloud Run service, and its `setIamPolicy` is most likely checked with `iap.webServices.setIamPolicy`. The custom role includes both permissions until §6 settles it. There's no `roles/iap.policyAdmin`. The nearest predefined roles (`iap.settingsAdmin`, `iap.editor`, `iap.viewer`) have **no** `setIamPolicy` permission, so `iap.admin` is the only predefined role that works.

**Risk that remains with either role:** a project-level `iap.webServices.setIamPolicy` also lets `<prefix>-ui` change who can open the UI itself (its Cloud Run IAP resource is also a `webService`). To close that, add an IAM condition on the custom-role binding that limits it to `resource.name` under `.../iap_web/agentRegistry`. Whether IAP evaluates `resource.name` that way needs a live test.

**Gateway use:** none of the UI's roles has `networkservices.agentGateways.use`, and `networkservices.viewer` doesn't include it. If binding from the deployed UI works today, the engine PATCH and the authz-policy target don't check it on the caller. That needs live test too.

**Split projects:** with a separate `gateway_project_id`, the IAP, registry, authz policy, extension, gateway and logging roles are needed in the **gateway project**. Terraform grants them only in `project_id` today, which is a gap for split setups.

### 3.2 `<prefix>-targets` (Cloud Run demo agents and MCP servers)

`runtimes/mcp_server` makes no Google API calls. `runtimes/a2a_agent` calls Gemini on Vertex AI through `google-genai` (`aiplatform.endpoints.predict`).

| Role (today) | Needed? | Proposed replacement | Status |
|---|---|---|---|
| `roles/aiplatform.user` (459) | Only `aiplatform.endpoints.predict`, used by the A2A agents | **`roles/aiplatform.expressUser`** (38; has `endpoints.predict`), or a custom role with `aiplatform.endpoints.predict` only | verified by permission names; needs live test |
| `roles/logging.logWriter` | **No.** The runtimes don't use the Cloud Logging API. Cloud Run collects stdout and stderr without any role on the service account | Remove | needs live test (check that container logs still appear) |

### 3.3 Aside: the engines' Agent Identity (not asked, worth a later test)

`deploy-theme` grants each orchestrator's Agent Identity 7 project roles (`AGENT_ROLES`). In this project, the project's Agent Identity principalSet already holds `roles/aiplatform.agentDefaultAccess`. That role includes:
- `logging.logEntries.create`, `monitoring.timeSeries.create`, `cloudtrace.traces.patch`, `telemetry.traces.write`;
- `serviceusage.services.use`, `resourcemanager.projects.get`;
- `aiplatform.endpoints.predict`.

So `logging.logWriter`, `monitoring.metricWriter`, `cloudtrace.agent`, `browser` and `serviceUsageConsumer` may be redundant (verified by permission names; needs live test, and a check on whether that grant appears automatically in a fresh project).

## 4. Summary of the proposed trims

| Identity | Today | Proposed | Change |
|---|---|---|---|
| Deployer | Owner (SETUP.md: "simplest") | 15 predefined roles (+1 GE, +2 PN) | §2.2; the 15 tested live (§0) |
| Deployer | `roles/aiplatform.user` | `roles/aiplatform.expressUser` | needs live test (the `vertexai` SDK create path) |
| Deployer | `roles/iam.serviceAccountUser` on the project | The same role on `<prefix>-ui`, `<prefix>-targets` and the Cloud Build service account only (e.g. a Terraform `google_service_account_iam_member` for the deployer) | needs live test |
| Deployer | `roles/iap.admin` | Custom `agdemoDeployerIap` (§5.2). Keep `iap.admin` if `run deploy --iap` needs IAP settings permissions, which are only at the TESTING level for custom roles | needs live test |
| Deployer | Project IAM Admin, unconditional | Project IAM Admin with a condition on the roles it may grant (§5.3) | needs live test |
| Deployer | `roles/viewer` (if build logs need it) | Not needed: builds are submitted `--async` and polled | done, tested live (§0) |
| `<prefix>-ui` | 9 project roles | 6 grants: `aiplatform.expressUser`, `agentregistry.viewer`, `logging.viewer`, `serviceusage.serviceUsageConsumer`, custom `agdemoRegistryEgressAdmin`, custom `agdemoGatewayPolicyEditor` (`networkservices.viewer` folded into the last one) | drops `iap.admin`, `networksecurity.editor`, `serviceExtensionsAdmin`, `networkservices.viewer`, `modelarmor.viewer`; `aiplatform.user` → `expressUser` |
| `<prefix>-ui` | `objectUser` on staging + recordings | `objectUser` on recordings only | code change in `ui.py` |
| `<prefix>-targets` | `aiplatform.user`, `logging.logWriter` | `aiplatform.expressUser` | needs live test |

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

Grant `roles/resourcemanager.projectIamAdmin` to the deployer with a condition, so it can only add or remove these roles. They are every role the demo's tooling grants: Terraform, `deploy-theme` and `down`. If §5.2's custom roles go into `iam.tf`, add them to the list.

```
api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly([
  'roles/aiplatform.user', 'roles/agentregistry.viewer', 'roles/iap.admin',
  'roles/networkservices.viewer', 'roles/networksecurity.editor',
  'roles/networkservices.serviceExtensionsAdmin', 'roles/modelarmor.viewer',
  'roles/logging.viewer', 'roles/serviceusage.serviceUsageConsumer',
  'roles/logging.logWriter', 'roles/modelarmor.calloutUser', 'roles/modelarmor.user',
  'roles/monitoring.metricWriter', 'roles/cloudtrace.agent', 'roles/browser',
  'roles/compute.networkAdmin', 'roles/dns.peer'
])
```
This stops the deployer from granting itself Owner. It's a documented IAM feature, but it hasn't been tested here with Terraform's read-modify-write of the whole policy: **needs live test**.

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
| D (optional) | A + `compute.networkAdmin`, `dns.admin` | `private_networking.enabled: true`, `private_networking.region:` a region with **no** egress gateway in the project (not the live demo's base or PN region, and not one with another team's gateway), `themes.enabled: [helpdesk, privnet]` | `up`, the privnet checks, `down`. Watch for the connectivity-template and PSC forwarding-rule creates (`servicedirectory`?) |
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
