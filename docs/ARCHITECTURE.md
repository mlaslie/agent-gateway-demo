# Architecture

This page explains how the demo is built and which GCP calls sit behind every policy toggle. Every
command and REST call here was **run against a live project** (my-demo-project / us-east4,
Oct 2026, gcloud 576.0.0) unless marked *documented only*. Names below use the default prefix `agdemo`.
The code derives every name from `config/demo.yaml` (`DemoConfig.name()` plus `agdemo_core/infra/common.py`).

```
                           ┌────────────── Agent Registry (region) ──────────────┐
                           │ agents/…  (A2A cards, auto-registered Runtime agent)│
                           │ mcpServers/… (tool spec)   endpoints/… (Google APIs)│
                           └───────────────▲──────────────────────────▲──────────┘
                                           │ discovers / allowlist     │ IAP IAM policy per entry
user ───streamQuery──▶ [agdemo-ingress]──▶ Agent Runtime engine ──▶ [agdemo-egress] ──▶ Cloud Run A2A / MCP
 (backend credentials)   CLIENT_TO_AGENT     (AGENT_IDENTITY)         AGENT_TO_ANYWHERE      + Google APIs
                         MA CONTENT_AUTHZ*                            IAP REQUEST_AUTHZ (roles/iap.egressor)
                         (no caller authz)                            MA CONTENT_AUTHZ*      (* checkbox)
```

## 1. Resources

| Resource | Name / id | Created by |
|---|---|---|
| Egress gateway | `agentGateways/agdemo-egress` (`AGENT_TO_ANYWHERE`, registry `//agentregistry.googleapis.com/projects/P/locations/R`) | bootstrap |
| Ingress gateway | `agentGateways/agdemo-ingress` (`CLIENT_TO_AGENT`) | bootstrap |
| IAP authz extension | `authzExtensions/agdemo-egress-iap-authz` (`service: iap.googleapis.com`, `iapPolicyVersion: V1`) | bootstrap (egress only) |
| IAP authz policy | `authzPolicies/agdemo-egress-iap-policy` (`REQUEST_AUTHZ`, `CUSTOM`). The ingress gateway gets no IAP policy: it enforces Model Armor only (§10). Older versions also created `agdemo-ingress-iap-authz` / `-policy`; `teardown` removes them if present | bootstrap (egress only) |
| Model Armor template | `templates/agdemo-shield` (PI + jailbreak, SDP basic) | bootstrap |
| Model Armor authz extension | `authzExtensions/agdemo-ma-authz` (`service: modelarmor.R.rep.googleapis.com`) | bootstrap |
| Model Armor authz policies | `authzPolicies/agdemo-{egress,ingress}-ma-policy` (`CONTENT_AUTHZ`) | **the UI checkbox only** |
| Platform allowlist | 25 registry `services/agdemo-plat-*` (endpoints) + `roles/iap.egressor` for every Runtime agent in the project | bootstrap |
| Service accounts | `agdemo-ui`, `agdemo-targets` | bootstrap |
| Artifact Registry / staging | `R-docker.pkg.dev/P/agdemo`, `gs://P-agdemo-staging` | bootstrap |
| Per theme | Cloud Run `agdemo-<theme>-<component>`, registry `services/agdemo-<theme>-<component>`, engine `agdemo-<theme>-orchestrator` | deploy-theme |

Everything is written to `config/state.json` (`shared` and `themes.<id>`).

Verified REST endpoints (from `gcloud --log-http`):

| API | Base |
|---|---|
| Agent gateways | `https://networkservices.googleapis.com/v1/projects/P/locations/R/agentGateways` (`?agentGatewayId=`) |
| Authz extensions | `https://networkservices.googleapis.com/v1beta1/projects/P/locations/R/authzExtensions` (`?authzExtensionId=`) |
| Authz policies | `https://networksecurity.googleapis.com/v1/projects/P/locations/R/authzPolicies` (`?authzPolicyId=`) |
| Agent Registry | `https://agentregistry.googleapis.com/v1/projects/P/locations/R/{services,agents,mcpServers,endpoints}` |
| IAP on registry entries | `https://iap.googleapis.com/v1/projects/<NUMBER>/locations/R/iap_web/agentRegistry[/{agents,mcpServers,endpoints}/<id>]:{get,set}IamPolicy` |
| Agent Runtime | `https://R-aiplatform.googleapis.com/v1/projects/<NUMBER>/locations/R/reasoningEngines/<id>` |
| Model Armor | `https://modelarmor.R.rep.googleapis.com/v1/projects/P/locations/R/templates` |

## 2. Gateways (bootstrap)

Created with a REST POST. The `gcloud` equivalent:

```yaml
# egress.yaml                                    # ingress.yaml
name: agdemo-egress                              name: agdemo-ingress
protocols: [MCP]                                 protocols: [MCP]
googleManaged:                                   googleManaged:
  governedAccessPath: AGENT_TO_ANYWHERE            governedAccessPath: CLIENT_TO_AGENT
registries:
  - //agentregistry.googleapis.com/projects/P/locations/R
```

```bash
gcloud network-services agent-gateways import agdemo-egress --source=egress.yaml --location=R --project=P
```

- **Timing (verified):** both gateways were readable less than 6 minutes after the POST. The ingress gateway was ready in about 2 minutes.
- `agentGatewayCard.serviceExtensionsServiceAccount` on the egress gateway is a tenant-project `service-<N>@gcp-sa-dep.iam.gserviceaccount.com`. Bootstrap grants it the Model Armor roles, together with the project's own `gcp-sa-dep` and `gcp-sa-aiplatform-re` agents.
- us-east4 supports Agent Gateway, Agent Registry and Agent Runtime (preflight checks this with list calls).

IAP authz extension and REQUEST_AUTHZ policy (one pair per gateway):

```yaml
name: agdemo-egress-iap-authz          # ENFORCE: failOpen false, no iamEnforcementMode
service: iap.googleapis.com            # DRY_RUN: failOpen true + metadata.iamEnforcementMode: DRY_RUN
failOpen: false
timeout: 1s
metadata: {iapPolicyVersion: "V1"}     # V1 = IAM allow policies (roles/iap.egressor + IAM conditions)
---
name: agdemo-egress-iap-policy
target: {resources: [projects/P/locations/R/agentGateways/agdemo-egress]}
policyProfile: REQUEST_AUTHZ
action: CUSTOM
customProvider: {authzExtension: {resources: [projects/P/locations/R/authzExtensions/agdemo-egress-iap-authz]}}
```

```bash
gcloud beta service-extensions authz-extensions import agdemo-egress-iap-authz --source=ext.yaml --location=R
gcloud network-security authz-policies import agdemo-egress-iap-policy --source=policy.yaml --location=R
```

Each authz policy create or delete is a long-running operation that takes **about 4 minutes** (measured 260 s for two policies in parallel).

`iapPolicyVersion: V2` switches IAP to IAM *access policies* (Unified Access Policies: `gcloud iam access-policies create` plus `gcloud iam policy-bindings create`, with CEL attributes such as `destination.agent_registry.mcp_server.tool.name`). V2 needs the org constraint `iam.managed.disableAccessPolicyBindings` to be off. The demo uses **V1**: it is per-resource, easy to show, and verified with tool conditions.

## 3. Agent Registry (answers question a)

A manually registered **Service** is projected by the registry into a read-only `agents/…`, `mcpServers/…` or
`endpoints/…` resource. The Service's `registryResource` field names that projection
(`projects/<NUMBER>/locations/R/<kind>/agentregistry-0000…`), and IAP policies are set on the projection.

| What | Body of `POST …/services?serviceId=<id>` | gcloud |
|---|---|---|
| A2A agent on Cloud Run | `{"displayName", "agentSpec": {"type": "A2A_AGENT_CARD", "content": <card JSON>}}`; `interfaces` must be empty | `gcloud agent-registry services create ID --location=R --agent-spec-type=a2a-agent-card --agent-spec-content="$(curl -s URL/.well-known/agent-card.json)"` |
| MCP server on Cloud Run | `{"mcpServerSpec": {"type": "TOOL_SPEC", "content": {"tools": [<tools/list items incl. annotations>]}}, "interfaces": [{"url": "https://…run.app/mcp", "protocolBinding": "JSONRPC"}]}` | `--mcp-server-spec-type=tool-spec --mcp-server-spec-content=toolspec.json --interfaces=url=…/mcp,protocolBinding=JSONRPC` |
| Plain endpoint (Google API) | `{"endpointSpec": {"type": "NO_SPEC"}, "interfaces": [{"url": "https://logging.googleapis.com", "protocolBinding": "JSONRPC"}]}` | `--endpoint-spec-type=no-spec --interfaces=url=…,protocolBinding=JSONRPC` |

- **A2A 1.0 cards are accepted.** The URL lives in `supportedInterfaces[].url` and the projection has no top-level `interfaces`. The gateway still matched the host: the call to kb-agent was `ALLOWED` and logged against its registry entry. If a card is ever rejected, `deploy-theme` falls back to `agentSpec: NO_SPEC` + `interfaces`.
- The tool spec limit is 10 KB. Annotations (`readOnlyHint`, `destructiveHint`) come from `tools.yaml`.
- Hostname matching is **exact**: regional, `.mtls.` and gRPC `host:443` forms are separate entries. Wildcards aren't supported.
- **Agent Runtime engines are auto-registered** as `agents/…`, with the attributes `agentregistry.googleapis.com/system/RuntimeReference` and `…/RuntimeIdentity`. `deploy-theme` stores that entry as `themes.<t>.orchestrator.registry`.
- `gcloud agent-registry bindings` links a source agent to a target with an auth-provider connector (delegated OAuth). The demo doesn't need it: bindings are **not** the egress allowlist.
- New registrations took effect at the gateway within about 15 minutes. The quickstarts say about 4 minutes. The very first call to a newly reachable host returned a TLS handshake failure once and a correct 403 on the retry.

## 4. Agent identity and egress grants (answer b)

The engine is created with `identity_type=AGENT_IDENTITY`. Its principal is `principal://` + `spec.effectiveIdentity`:

```
principal://agents.global.org-000000000000.system.id.goog/resources/aiplatform/projects/123456789012/locations/us-east4/reasoningEngines/1234567890123456789
```

- The trust domain is `agents.global.org-<ORG_ID>.system.id.goog`. Get `ORG_ID` from `gcloud projects get-ancestors P`. The path uses the project **number**.
- All agents in the project: `principalSet://agents.global.org-<ORG_ID>.system.id.goog/attribute.platformContainer/aiplatform/projects/<NUMBER>`.

Grant or revoke (the handlers do the same through `…:getIamPolicy` / `…:setIamPolicy` with etag retry):

```bash
gcloud iap web add-iam-policy-binding --resource-type=agent-registry --agent=<REGISTRY_ID> \
  --region=R --project=P --member='principal://…/reasoningEngines/<ID>' --role=roles/iap.egressor
gcloud iap web remove-iam-policy-binding … same flags …
# --mcp-server=<ID> for MCP servers, --endpoint=<ID> for endpoints; no flag = the whole regional registry
```

`roles/iap.egressor` contains a single permission, `iap.webServiceVersions.egressViaIAP`. Every egress decision
shows up in Data Access audit logs as `iap.googleapis.com` `AuthorizeUser`, with `granted: true|false` and the
registry resource.

The project roles `deploy-theme` grants to the engine principal are `aiplatform.user`, `agentregistry.viewer`,
`logging.logWriter`, `monitoring.metricWriter`, `cloudtrace.agent`, `browser` and
`serviceusage.serviceUsageConsumer`. When `cloud_run.public_targets=false` it also grants `run.invoker` on each target.

## 5. MCP tool-level policy (answer c): supported, verified

IAP V1 bindings accept IAM conditions on MCP request attributes that the gateway parses from the JSON-RPC body:

| Attribute | Meaning |
|---|---|
| `iap.googleapis.com/mcp.toolName` | `params.name` of `tools/call` (empty for `initialize`, `tools/list`, notifications) |
| `iap.googleapis.com/mcp.tool.isReadOnly` | from the registered tool spec's `readOnlyHint` |
| `iap.googleapis.com/mcp.method`, `mcp.resourceName`, `mcp.promptName`, `request.auth.type` | *documented only* |

Binding that the `tickets-readonly` policy creates (verified):

```json
{"role": "roles/iap.egressor",
 "members": ["principal://agents.global.org-000000000000.system.id.goog/resources/aiplatform/projects/123456789012/locations/us-east4/reasoningEngines/1234567890123456789"],
 "condition": {"title": "agdemo helpdesk tickets-readonly",
               "expression": "api.getAttribute('iap.googleapis.com/mcp.toolName', '') in ['list_tickets', 'get_ticket', '']"}}
```

Result with the egress gateway attached: `list_tickets` 200 and `get_ticket` 200, while `close_ticket` 403 and `delete_ticket` 403. Each
policy owns one binding, identified by its condition, so `tickets-readonly` and `tickets-all` (unconditional) can be applied together. IAM ORs the bindings.

**The `''` entry is required.** Without it, `initialize` and `tools/list` (which carry no tool name) are denied, and the MCP session never starts.

Alternative (`params.use_annotation: true`): the condition
`api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true || api.getAttribute('iap.googleapis.com/mcp.toolName', '') == ''`.
This uses the annotation stored in the registry rather than a list of names. It is documented in the cloudnet codelab but not run here.
No fallback is needed. If conditions ever stop working, the documented fallback is to register two MCP services
on different paths (`/mcp-ro`, `/mcp-rw`) and grant each without a condition.

## 6. Model Armor (answer d)

Template (REST, `POST https://modelarmor.R.rep.googleapis.com/v1/projects/P/locations/R/templates?templateId=agdemo-shield`):

```json
{"filterConfig": {"piAndJailbreakFilterSettings": {"filterEnforcement": "ENABLED", "confidenceLevel": "MEDIUM_AND_ABOVE"},
                  "sdpSettings": {"basicConfig": {"filterEnforcement": "ENABLED"}}},
 "templateMetadata": {"logSanitizeOperations": true, "logTemplateOperations": true,
   "customPromptSafetyErrorCode": 403,
   "customPromptSafetyErrorMessage": "agdemo-model-armor-block: prompt blocked by Model Armor",
   "customLlmResponseSafetyErrorCode": 403,
   "customLlmResponseSafetyErrorMessage": "agdemo-model-armor-block: response blocked by Model Armor"}}
```

The equivalent `gcloud model-armor templates create agdemo-shield --location=R --pi-and-jailbreak-filter-settings-enforcement=enabled --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above --basic-config-filter-enforcement=enabled …` has awkward flag groups. The demo uses REST.

Extension (bootstrap) and the policy the checkbox creates or deletes:

```yaml
name: agdemo-ma-authz
service: modelarmor.us-east4.rep.googleapis.com
failOpen: false
timeout: 5s
metadata:
  model_armor_settings: '[{"request_template_id": "projects/P/locations/R/templates/agdemo-shield",
                           "response_template_id": "projects/P/locations/R/templates/agdemo-shield"}]'
---
name: agdemo-egress-ma-policy        # and agdemo-ingress-ma-policy
target: {resources: [projects/P/locations/R/agentGateways/agdemo-egress]}
policyProfile: CONTENT_AUTHZ
action: CUSTOM
customProvider: {authzExtension: {resources: [projects/P/locations/R/authzExtensions/agdemo-ma-authz]}}
```

- **On / off** = create or delete the two CONTENT_AUTHZ policies. Each takes about 4 minutes (LRO), and the effect is visible within seconds after that.
- Limits: at most 4 authz policies per egress gateway, and **at most one CONTENT_AUTHZ per ingress gateway**.
- Service agents need `roles/modelarmor.calloutUser`, `roles/modelarmor.user` and `roles/serviceusage.serviceUsageConsumer`. The egress agent is `service-<NUM>@gcp-sa-dep` (and the gateway's tenant `serviceExtensionsServiceAccount`). The ingress agent is `service-<NUM>@gcp-sa-aiplatform-re`.

**What a block looks like (verified, use these to classify):**

| Path | HTTP | Body |
|---|---|---|
| Egress, MCP `tools/call` | **403** | `{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"agdemo-model-armor-block: prompt blocked by Model Armor"}],"isError":true}}`. The text is the template's `customPromptSafetyErrorMessage` |
| Ingress, `:streamQuery` | **403** | `{"error":{"code":403,"message":"Model Armor: Prompt violates content security configurations","status":"PERMISSION_DENIED"}}` |
| Egress, A2A `message/send` | 200 | **Not inspected.** The same malicious payload to kb-agent passed with Model Armor on (tested twice, 10 minutes apart). Egress Model Armor screens MCP payloads; A2A JSON-RPC is not screened |

Classifier rule: a 403 body containing `agdemo-model-armor-block` (egress MCP) or `Model Armor:` (ingress) → `blocked`.
Any other 403 → `denied`.

## 7. Egress deny (what a policy denial looks like)

| Case | HTTP | Body |
|---|---|---|
| Registered destination, no matching `roles/iap.egressor` (or the condition is false) | **403** | `Egress request is not authorized.` (plain text, from the gateway; seen for A2A card GET, A2A POST and MCP `tools/call`) |
| First call to a host right after attach or registration | n/a | `ConnectError: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE]`. Transient (gone on retry); treat as `error`/pending |

Gateway log entries (`networkservices.googleapis.com/gateway_requests`): `httpRequest.status`, `jsonPayload.authzPolicyInfo.result`
(`ALLOWED`/`DENIED`), `jsonPayload.enforcedGatewaySecurityPolicy.hostname`, `jsonPayload.agentGatewayInfo.agentRegistryResource`.

## 8. Platform endpoints under default deny (answer g)

`bootstrap` registers these 25 entries and grants `roles/iap.egressor` to the **project principal set** on each.
That covers only these entries; the rest of the registry stays default-deny.

```
R-aiplatform(.mtls).googleapis.com  aiplatform.R.rep.googleapis.com  aiplatform(.mtls).googleapis.com
agentregistry(.mtls)  logging(.mtls)  telemetry(.mtls)  cloudtrace(.mtls)  monitoring(.mtls)
cloudresourcemanager(.mtls)  iamcredentials(.mtls)  secretmanager(.mtls)
gRPC host:443 forms: cloudresourcemanager.mtls, logging.mtls, cloudtrace.mtls, telemetry.mtls
```

Verified: with this list, the ADK orchestrator booted behind the default-deny egress gateway (PATCH succeeded) and ran Gemini
and sessions. In the gateway log, `us-east4-aiplatform.mtls.googleapis.com` (`/v1beta1/…/reasoningEngines/…` sessions plus
model calls), `cloudresourcemanager.mtls.googleapis.com:443` (gRPC `GetProject`), `telemetry.mtls.googleapis.com:443` and
`iamcredentials.mtls.googleapis.com` were all `ALLOWED`. `iamcredentials` returns 404 for a tenant SA token lookup, which is harmless.

## 9. Binding, unbinding and timing (answer f)

```bash
curl -X PATCH -H "Authorization: Bearer $(gcloud auth print-access-token)" -H 'Content-Type: application/json' \
 "https://R-aiplatform.googleapis.com/v1/<engine>?updateMask=spec.deploymentSpec.agentGatewayConfig" \
 -d '{"spec":{"deploymentSpec":{"agentGatewayConfig":{
       "agentToAnywhereConfig":{"agentGateway":"projects/P/locations/R/agentGateways/agdemo-egress"},
       "clientToAgentConfig":{"agentGateway":"projects/P/locations/R/agentGateways/agdemo-ingress"}}}}}'
```

- The PATCH replaces the whole `agentGatewayConfig`. To change one direction, re-send the other: `engines.set_gateways()` does this, and it was verified that after attaching ingress the egress binding was still present. To unbind a direction, PATCH without it. To unbind everything, send `{}`.
- It is a long-running operation (`UpdateReasoningEngineOperationMetadata`) that **redeploys the container**. Measured times:
  egress attach **5 min 11 s**, ingress attach (egress kept) **2 min 24 s**, egress detach (ingress kept) **2 min 25 s**,
  ingress detach to an empty config `{}` **2 min 34 s**. After detaching, calls are direct again immediately. While it runs, `GET` still shows the old config. In-flight operations are listed at `GET <engine>/operations`, which is what `gateway_attach.status()` uses (plus `config/state.json` `pending_ops`).
- **Coexistence:** the gateways are shared. Each theme's engine attaches and detaches independently, and an unbound engine can coexist with bound ones in the same project and region. The constraint is only that every *bound* engine uses the same egress gateway and the same ingress gateway (preflight flags engines bound elsewhere).
- Attaching a gateway archives earlier engine revisions. Engines created before 2026-04-29 can't be bound.

## 10. Ingress (answer e)

**How a client calls:** exactly as without a gateway. It sends
`POST https://R-aiplatform.googleapis.com/v1/<engine>:streamQuery?alt=sse` with an OAuth access token
(`{"class_method":"async_stream_query","input":{"user_id":…,"message":…}}`). When the engine has
`clientToAgentConfig`, the Google Front End applies the ingress gateway's authz policies to `query` / `streamQuery`.
There is no separate gateway hostname. This is confirmed: with the ingress MA policy on, a prompt-injection `streamQuery` returned the
403 in §6 before reaching the agent.

**What the ingress gateway enforces:** in `CLIENT_TO_AGENT` mode Agent Gateway supports **Model Armor (`CONTENT_AUTHZ`)
policy enforcement only**. With the ingress MA policy on, a prompt-injection / PII `streamQuery` is rejected before it
reaches the agent with (verified):

```json
{"error":{"code":403,"message":"Model Armor: Prompt violates content security configurations","status":"PERMISSION_DENIED"}}
```

A benign request with the gateway attached goes through normally (governed). The ingress gateway writes **no request
logs** to Cloud Logging; the gateway-log feature (§9 of CONTRACTS.md) covers egress only.

**Caller identity is not enforced by the gateway.** Who may call the agent is decided by IAM on Agent Runtime
(`roles/aiplatform.user`, i.e. `reasoningEngines.query/streamQuery`), with or without an ingress gateway. Verified
history, kept brief: an IAP `REQUEST_AUTHZ` policy on the ingress gateway plus a `roles/iap.httpsResourceAccessor`
grant on the orchestrator's auto-registered registry entry had no effect. An ungranted service account still got `200`
for 22+ minutes (`iapPolicyVersion` V1 and V2), and no IAP `AuthorizeUser` audit entry was ever written for an ingress
call, while CONTENT_AUTHZ was applied on the same path. This is by design for `CLIENT_TO_AGENT` (ISSUES.md #1).

**How the demo calls ingress:** `agdemo_core/gcp/ingress_client.invoke_via_ingress(ctx, theme, message)` sends the
`streamQuery` above with the **backend's own credentials**: the `agdemo-ui` service account on Cloud Run, or the
operator's ADC when the UI runs locally. There is no impersonation and there are no demo caller service accounts.
Scenario 5 has one `user` node and one edge, `ingress:user`. Simulation: no ingress gateway → `direct`; attached →
`allowed` (governed); attached + Model Armor on + a `malicious` test → `blocked`. Never `denied`.

## 11. Verification log

| Check | Result |
|---|---|
| preflight | all PASS (billing via gcloud fallback) |
| bootstrap (twice, idempotent) | gateways, IAP authz, MA template + extension, 25 platform endpoints, SAs |
| deploy-theme helpdesk | 2 images (Cloud Build), 4 Cloud Run services, 4 registry entries (A2A card + MCP tool spec), engine with AGENT_IDENTITY, no gateway |
| wide open probe | all 6 edges `ok` (direct) |
| egress attached + allow-kb + tickets-readonly | kb ok · hr 403 · get/list_ticket ok · close/delete 403 · directory 403 (after 1 transient TLS failure) |
| Model Armor on, malicious probe | MCP get_ticket **403 blocked** (body above); A2A kb-agent **not blocked** |
| ingress attached, MA on, malicious streamQuery | **403 Model Armor** (body above) |
| ingress attached, benign streamQuery | 200 (governed); caller identity not checked by the gateway (see §10) |
| egress detached (ingress kept), then ingress detached (`{}`) | each about 2.5 min; probes direct again immediately |
| Model Armor off | CONTENT_AUTHZ policies deleted (224 s) |
| `./agdemo reset helpdesk` | all policies removed; theme back to wide open |

## 12. UI on Cloud Run (`./agdemo ui deploy`)

- Cloud Build runs from the repo root with `-f ui/Dockerfile` (the config is generated at `config/generated/ui-cloudbuild.yaml`). The image is pushed to `R-docker.pkg.dev/P/agdemo/ui:latest`.
- `gcloud beta run deploy agdemo-ui --iap --no-allow-unauthenticated --service-account=agdemo-ui@…` sets `AGDEMO_CONFIG_YAML`, `AGDEMO_STATE_JSON` and `AGDEMO_RECORDINGS_DIR=/tmp/recordings` from `config/generated/ui.env.yaml`. Re-run `ui deploy --skip-build` after `deploy-theme`, so the UI picks up new state.
- The IAP service agent `service-<NUM>@gcp-sa-iap.iam.gserviceaccount.com` gets `roles/run.invoker`. Each member of `ui.iap_access` and `ui.admin_access` gets `roles/iap.httpsResourceAccessor` (`gcloud beta iap web add-iam-policy-binding --resource-type=cloud-run --service=agdemo-ui`). Bootstrap also grants each `ui.admin_access` member `roles/aiplatform.user` on the project, so presenters can call the agent from Gemini Enterprise.

Project roles for `agdemo-ui` (from bootstrap), and why each is needed:

| Role | Needed for |
|---|---|
| `roles/aiplatform.user` | `reasoningEngines.get/update` (gateway PATCH), `query/streamQuery` (tests, probes, ingress calls) |
| `roles/iap.admin` | get/set IAP IAM policy on registry entries (egress allows) |
| `roles/agentregistry.viewer` | resolve registry entries |
| `roles/networksecurity.editor` | create/delete the Model Armor CONTENT_AUTHZ policies |
| `roles/networkservices.serviceExtensionsAdmin` | `authzExtensions.use` when attaching the MA extension |
| `roles/networkservices.viewer` | read gateways |
| `roles/modelarmor.viewer`, `roles/logging.viewer`, `roles/serviceusage.serviceUsageConsumer` | status / logs / quota |

## 13. Notes for the other components

- **Orchestrator classifier (runtimes):** egress deny = `403` with body `Egress request is not authorized.`. Egress Model Armor (MCP) = `403` with a JSON-RPC body whose text contains `agdemo-model-armor-block`. Ingress Model Armor = `403` with `"message": "Model Armor: Prompt violates content security configurations"`. `ssl/tls alert handshake failure` right after attach or registration is transient.
- **Simulator (backend):** in Live mode, egress Model Armor does **not** block A2A edges, only MCP edges. Ingress (`ingress:user`) is never `denied`: only `direct`, `allowed` or `blocked` (see §10).
- **Cloud Run reserves paths ending in `z`**: `GET /healthz` on the deployed targets returns Google's 404 page. Use `/health` if you need it externally.
- The A2A agent card (a2a 1.0, URL in `supportedInterfaces`) registers as `A2A_AGENT_CARD` and is governed correctly.
