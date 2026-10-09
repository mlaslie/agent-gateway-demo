# Backlog

Ideas for extending the demo. Effort is rough: S = hours, M = a day or two, L = several days. "Verified" means the underlying GCP behavior has been confirmed live in this project.

| # | Item | Area | Effort | GCP behavior | Status |
|---|---|---|---|---|---|
| 1 | **Data exfiltration attempt:** a generic "fetch URL" tool; a prompt-injected request tries to post data to an unregistered internet host and the gateway denies it | Scenario | S | Verified (unregistered hosts are denied) | Proposed |
| 2 | **Unregistered agent:** deploy an agent that isn't in Agent Registry (blocked), register it (still blocked), grant it (allowed) | Scenario | S | Verified | Proposed |
| 3 | **Same gateway, different agents:** a second orchestrator (HR Assistant / Pricing Analyst) with its own Agent Identity gets different access through the same gateway (scenario 7, CONTRACTS §12) | Scenario | M | Verified (per-principal egressor) | Done |
| 4 | **Time-boxed access:** allow an agent for 15 minutes or business hours only (IAM condition on `request.time`) | Scenario | S | Not verified | Proposed |
| 5 | **Dry-run before enforce:** toggle the egress IAP extension between `DRY_RUN` and `ENFORCE`; gateway logs show would-be denials without blocking | Scenario | S | Config verified; demo not built | Proposed |
| 6 | **Custom business-rule authorization:** delegate decisions to your own authz service (e.g. refunds over $500 need approval) using tool arguments | Scenario | M–L | Not verified | Proposed |
| 7 | **Model Armor on responses:** block sensitive data on the way back (e.g. a salary in the reply) | Scenario | S | Not verified | Proposed |
| 8 | **Auto-play / presenter mode:** one button runs the whole story with narration cards, from recordings | Demo UX | M | n/a | Proposed |
| 9 | **Attack library for Model Armor:** pick attack types (injection, jailbreak, PII types, malicious URLs) and template filters; show which filter caught what | Demo UX | S–M | Partly verified | Proposed |
| 10 | **Agent Registry browser:** live view of agent cards and skills, MCP tools with their hints, platform endpoints, and who has access to each | Demo UX | S | Verified | **Done** (registry sheet: click the Agent Registry node on the diagram) |
| 11 | **Gateway overhead panel:** latency direct vs through the gateway, from timings already captured | Demo UX | S | n/a | Proposed |
| 12 | **Export to Terraform:** generate Terraform for the current state (gateway bindings, IAP bindings and conditions, Model Armor policies) | Production | M | Depends on provider support (see #17) | **Done** (Policies card → Export Terraform, `./agdemo export-terraform`: IAP grants with conditions, gateway binding via `terraform_data` PATCH, Model Armor policies, optional shared infra; passes `terraform validate`) |
| 13 | **Compliance evidence report:** downloadable page of the policies in place plus gateway log entries proving enforcement | Production | S–M | n/a | Proposed |
| 14 | **Org policy guardrail:** organization policy rejects creating an Agent Runtime agent without an approved gateway | Production | M | Documented; needs org-level permissions | Proposed |
| 15 | **Private destinations:** a private API reached through the gateway over PSC | Production | L | Documented | Proposed |
| 16 | **Theme generator:** describe a use case in a sentence and Gemini drafts the theme files for review | Content | M | n/a | Proposed |
| 17 | **"Show me the code" per policy:** for every policy add/remove (gateway attach/detach, A2A/MCP allows, read-only by name or hint, Model Armor on/off), show the equivalent REST call, Python SDK and gcloud command | Demo UX | S–M | No new GCP behavior: same calls the handlers already make (see research below) | Researched — feasible |

## #17 research: tooling support per policy operation

Researched Oct 2026 (read-only). gcloud 576.0.0 (`--help` checked for GA/beta/alpha); Python clients inspected
by importing the latest PyPI releases; Terraform from the `hashicorp/terraform-provider-google` docs and CHANGELOG
(latest release 8.6.0, 2026-10-06). Nothing was created or changed in GCP. P = project id, N = project number, R = region.

Legend: ✅ supported (GA tooling, dedicated method/command/resource) · ⚠️ partial (beta/pre-1.0, private API,
generic-only, or needs a workaround) · ❌ not supported.

### Matrix

| Operation | REST | Python SDK | gcloud | Terraform (`google` provider) |
|---|---|---|---|---|
| **1a. Attach / detach egress gateway** on an engine | ✅ `PATCH https://R-aiplatform.googleapis.com/v1/projects/N/locations/R/reasoningEngines/ID?updateMask=spec.deploymentSpec.agentGatewayConfig`, body `{"spec":{"deploymentSpec":{"agentGatewayConfig":{"agentToAnywhereConfig":{"agentGateway":"projects/P/locations/R/agentGateways/agdemo-egress"}, …other direction re-sent…}}}}`; detach = PATCH without it (`{}` for none). LRO, redeploys | ⚠️ [^py-engine] | ❌ no `gcloud [alpha\|beta] ai reasoning-engines` group exists [^cli-engine] | ⚠️ `google_vertex_ai_reasoning_engine.spec.deployment_spec.agent_gateway_config.agent_to_anywhere_config.agent_gateway` (GA, added in 8.1.0) — but only if Terraform owns the whole engine [^tf-engine] |
| **1b. Attach / detach ingress gateway** | ✅ same PATCH with `clientToAgentConfig.agentGateway` | ⚠️ [^py-engine] | ❌ [^cli-engine] | ⚠️ `…agent_gateway_config.client_to_agent_config.agent_gateway` [^tf-engine] |
| **2a. Grant / revoke `roles/iap.egressor` on an A2A agent** (registry `agents/ID`) | ✅ `POST https://iap.googleapis.com/v1/projects/N/locations/R/iap_web/agentRegistry/agents/ID:getIamPolicy` (`{"options":{"requestedPolicyVersion":3}}`) then `…:setIamPolicy` (`{"policy":{…,"version":3}}`); read-modify-write with etag | ✅ `google-cloud-iap` `iap_v1.IdentityAwareProxyAdminServiceClient.get_iam_policy` / `set_iam_policy` (`resource="projects/N/locations/R/iap_web/agentRegistry/agents/ID"`; URI template is `{resource=**}` so any IAP resource works) | ✅ GA `gcloud iap web add-iam-policy-binding --resource-type=agent-registry --agent=ID --region=R --member=principal://… --role=roles/iap.egressor`; revoke: `remove-iam-policy-binding` same flags | ✅ `google_iap_agent_registry_agent_iam_member` (`agent_id`, `location`, `role`, `member`) — GA since 7.40.0 |
| **2b. … on an MCP server** (`mcpServers/ID`) | ✅ same with `/mcpServers/ID` | ✅ same client, `…/agentRegistry/mcpServers/ID` | ✅ GA `--mcp-server=ID` | ✅ `google_iap_agent_registry_mcp_server_iam_member` (`mcp_server_id`) |
| **2c. … with an IAM condition** (`mcp.toolName in [...]`, `mcp.tool.isReadOnly`) | ✅ binding gets `"condition":{"title","expression"}`; policy `version: 3` | ✅ `google.iam.v1.policy_pb2.Binding(condition=google.type.expr_pb2.Expr(...))`; `GetIamPolicyRequest(options=GetPolicyOptions(requested_policy_version=3))` | ✅ GA `--condition='^\|^title=…\|expression=…'` (custom delimiter needed: expressions contain commas) or `--condition-from-file`; revoke must pass the same `--condition` (or `--all`) | ✅ `condition { title, expression }` block on the `_iam_member` / `_iam_binding` resources (docs: "supports IAM Conditions") |
| **3. Model Armor on / off** = create / delete `CONTENT_AUTHZ` authz policy per gateway | ✅ `POST https://networksecurity.googleapis.com/v1/projects/P/locations/R/authzPolicies?authzPolicyId=agdemo-egress-ma-policy` (body: `target.resources=[agentGateways/…]`, `policyProfile: CONTENT_AUTHZ`, `action: CUSTOM`, `customProvider.authzExtension.resources=[authzExtensions/agdemo-ma-authz]`); off = `DELETE …/authzPolicies/NAME`. LRO ~4 min | ✅ `google-cloud-network-security` (0.13.x, pre-1.0) `network_security_v1.NetworkSecurityClient.create_authz_policy` / `delete_authz_policy` (`AuthzPolicy.PolicyProfile.CONTENT_AUTHZ`, `AuthzAction.CUSTOM`) | ✅ GA, YAML only: `gcloud network-security authz-policies import NAME --source=policy.yaml --location=R`; off: `… authz-policies delete NAME --location=R` (no flag-based `create`) | ✅ `google_network_security_authz_policy` (`policy_profile = "CONTENT_AUTHZ"`, `action = "CUSTOM"`, `custom_provider { authz_extension { resources } }`, `target { resources = [agentGateways/…] }`) |
| 3s. Model Armor authz extension (supporting) | ✅ `POST https://networkservices.googleapis.com/v1/…/authzExtensions?authzExtensionId=agdemo-ma-authz` (`service: modelarmor.R.rep.googleapis.com`, `metadata.model_armor_settings`). v1 confirmed by `gcloud … list --log-http`; the code currently uses v1beta1 | ✅ `google-cloud-network-services` (0.10.x, pre-1.0) `network_services_v1.DepServiceClient.create_authz_extension` | ✅ GA `gcloud service-extensions authz-extensions import NAME --source=ext.yaml --location=R` (ARCHITECTURE.md still shows `beta`; GA now exists) | ✅ `google_network_services_authz_extension` (`service` doc lists `modelarmor.{region}.rep.googleapis.com` for CONTENT_AUTHZ) |
| 3t. Model Armor template (supporting) | ✅ `POST https://modelarmor.R.rep.googleapis.com/v1/projects/P/locations/R/templates?templateId=…` | ✅ `google-cloud-modelarmor` `modelarmor_v1.ModelArmorClient.create_template` (set `client_options.api_endpoint` to the regional `rep` host) | ✅ GA `gcloud model-armor templates create` (awkward flag groups) | ✅ `google_model_armor_template` (`filter_config`, `template_metadata.custom_prompt_safety_error_message`, …) |
| 4a. Agent gateways (egress / ingress) | ✅ `POST https://networkservices.googleapis.com/v1/…/agentGateways?agentGatewayId=…` | ✅ `network_services_v1.NetworkServicesClient.create_agent_gateway` (`AgentGateway.google_managed.governed_access_path`, `registries`) | ✅ GA, YAML only: `gcloud network-services agent-gateways import NAME --source=gw.yaml --location=R` | ✅ `google_network_services_agent_gateway` (`google_managed { governed_access_path }`, `registries`; `protocols` is deprecated since 7.37.0) |
| 4b. IAP authz extension + `REQUEST_AUTHZ` policy (egress) | ✅ as 3s/3 with `service: iap.googleapis.com`, `metadata.iapPolicyVersion: V1`, `policyProfile: REQUEST_AUTHZ` | ✅ same clients as 3 / 3s | ✅ GA `authz-extensions import` + `authz-policies import` | ✅ `google_network_services_authz_extension` (`service = "iap.googleapis.com"`) + `google_network_security_authz_policy` (`policy_profile = "REQUEST_AUTHZ"`) |
| 4c. Agent Registry services (A2A card / MCP tool spec / endpoints) | ✅ `POST https://agentregistry.googleapis.com/v1/projects/P/locations/R/services?serviceId=…` | ✅ `google-cloud-agentregistry` (0.1.x, pre-1.0) `agentregistry_v1.AgentRegistryClient.create_service` / `delete_service` (`agent_spec`, `mcp_server_spec`, `endpoint_spec`, `interfaces`) | ✅ GA `gcloud agent-registry services create ID --location=R --agent-spec-type=a2a-agent-card --agent-spec-content=…` / `--mcp-server-spec-type=tool-spec …` / `--endpoint-spec-type=no-spec --interfaces=url=…,protocolBinding=JSONRPC` | ✅ `google_agent_registry_service` (`agent_spec { type = "A2A_AGENT_CARD", content }`, `mcp_server_spec { type = "TOOL_SPEC", content }`, `endpoint_spec`, `interfaces`; output `registry_resource` feeds the IAP IAM resources). Data sources `google_agent_registry_{agent,mcp_server,endpoint}` exist |
| 4d. Platform allowlist grant to all project agents (`principalSet://…/attribute.platformContainer/…`) on endpoints | ✅ as 2 with `/endpoints/ID` | ✅ as 2 | ✅ GA `--endpoint=ID` | ✅ `google_iap_agent_registry_endpoint_iam_member`; whole registry: `google_iap_agent_registry_iam_member` |

[^py-engine]: The installed GAPIC `google-cloud-aiplatform` 2.4.0 (`aiplatform_v1` and `v1beta1`
`ReasoningEngineSpec.DeploymentSpec`) has **no** `agent_gateway_config` field, so `ReasoningEngineServiceClient.update_reasoning_engine`
can't express it (❌). The `vertexai` client has the types (`types.ReasoningEngineSpecDeploymentSpecAgentGatewayConfig`) and
`client.agent_engines.update(name=…, config={"agent_gateway_config": …})`, but in 2.4.0 that path raises `ValueError` unless
`agent=` or source-code options are also passed (it re-uploads the agent), it treats `{}` as "no change" (can't detach the last
direction), and it blocks until the LRO finishes. Workable options to show: `google.auth.transport.requests.AuthorizedSession(...).patch(url, params={"updateMask": …}, json=body)`
(what `engines.set_gateways()` does today), or the private `client.agent_engines._update(name=…, config={"spec": {"deployment_spec": {"agent_gateway_config": …}}, "update_mask": "spec.deployment_spec.agent_gateway_config"})` (code-read, not run).

[^cli-engine]: No gcloud command manages Agent Runtime engines (checked `gcloud ai`, `beta ai`, `alpha ai`). The closest CLI is
`agents-cli deploy --agent-gateway-egress=<gateway> / --agent-gateway-ingress=<gateway>` (empty value unbinds), but that runs a
full deploy of the agent source, not a config-only update. For the "gcloud" tab show the `curl -X PATCH … $(gcloud auth print-access-token)` form.

[^tf-engine]: The field exists (GA, `google` 8.1.0, 2026-09-01), but `google_vertex_ai_reasoning_engine` is the whole engine
(`spec.package_spec` / `source_code_spec` / `container_spec`, `identity_type`, …). Our engines are created by `deploy-theme`
through the vertexai SDK, so Terraform can only manage the binding after an `import` of the engine with a matching spec, and a
spec diff could trigger a redeploy. Not verified against a live engine. Whether removing the `agent_gateway_config` block sends `{}`
(unbind) is also unverified.

Side note: the `google_network_security_authz_policy` docs also describe MCP match rules in `http_rules` ("allowed only when the
AuthzPolicy points to an AgentGateway"). That's an alternative to IAP tool conditions; the demo doesn't use it and it is not verified here.

### "Show me the code": feasible

- **REST:** ✅ for every operation. These are exactly the calls the handlers already make; `describe()` already emits most of them.
- **gcloud:** ✅ for IAP grants, including conditions (2a–2c), and for Model Armor on/off (YAML `import` + `delete`). ❌ for gateway
  attach/detach: show the `curl` PATCH with `$(gcloud auth print-access-token)` and a one-line note ("no gcloud command for Agent
  Runtime; `agents-cli deploy --agent-gateway-egress` binds as part of a full redeploy").
- **Python:** use the official clients where they exist: `google-cloud-iap` for grants (read-modify-write with
  `requested_policy_version=3`), `google-cloud-network-security` for Model Armor on/off. For gateway attach/detach, show
  `AuthorizedSession.patch(...)` with a note that the GAPIC client lacks the field and `vertexai` `agent_engines.update()` needs the
  agent code. Mention that several clients are pre-1.0 (`network-security` 0.13, `network-services` 0.10, `agentregistry` 0.1).
- Snippets are cheap: values come from `state.json` (engine, principal, registry ids). Effort S–M (three renderers per policy type
  plus a tabbed popup).

### Terraform conclusion (for #12)

Can be emitted as real Terraform (`hashicorp/google` ≥ 8.1.0, no `google-beta` needed):

- `google_network_services_agent_gateway` (egress + ingress)
- `google_network_services_authz_extension` (IAP and Model Armor extensions)
- `google_network_security_authz_policy` (IAP `REQUEST_AUTHZ`; Model Armor `CONTENT_AUTHZ` per gateway = the checkbox state)
- `google_model_armor_template`
- `google_agent_registry_service` (A2A cards, MCP tool specs, the 25 platform endpoints)
- `google_iap_agent_registry_agent_iam_member` / `_mcp_server_iam_member` / `_endpoint_iam_member` (`roles/iap.egressor`, with
  `condition {}` for tool-level policies) and `google_iap_agent_registry_iam_member` for registry-wide grants
- Supporting IAM: `google_project_iam_member` for the engine principal and service agents

Needs a fallback:

- **Engine gateway binding (1a/1b):** emit `terraform_data` + `local-exec` running the `curl` PATCH (and the reverse PATCH on
  destroy), with a comment pointing at `google_vertex_ai_reasoning_engine.spec.deployment_spec.agent_gateway_config` for teams that
  manage the engine itself in Terraform (needs an `import` block; not verified).
- **Auto-registered Runtime agent entries** (`agents/…` created by the platform): reference them with the
  `google_agent_registry_agent` data source or a literal id. Don't create them.
- **Agent Runtime engines and Cloud Run services** are out of scope for #12 (built by `deploy-theme`). Reference them by id.

Unverified: none of the Terraform resources were applied (docs/CHANGELOG only); Python client calls were checked by inspection,
not run; the `vertexai` private `_update` path was not run.
