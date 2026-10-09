# Internal Contracts

These are the interfaces between the parts of the demo. Shared Python code lives in `agdemo_core/`:
- `config.py`: `DemoConfig`, which also derives every resource name
- `state.py`: `config/state.json`
- `themes.py`: the theme schema

Do not change these contracts without updating this file.

## 1. Node ids (diagram)

| Node id | Meaning |
|---|---|
| `user` | The user calling the agent (ingress flow; the only ingress caller node) |
| `ingress_gateway` | `CLIENT_TO_AGENT` gateway |
| `orchestrator` | The theme's Agent Runtime agent (`theme.orchestrator`) |
| `egress_gateway` | `AGENT_TO_ANYWHERE` gateway |
| `registry` | Agent Registry (shown at the side, with dashed "discovers" lines) |
| `<component id>` | Cloud Run A2A agent or MCP server; MCP nodes list their tools as rows |
| `model_armor` | Badge drawn on the gateway nodes when it's enabled |

## 2. Edge ids <a id="edges"></a>

| Edge id | Path |
|---|---|
| `<a2a component id>` | orchestrator → A2A agent, e.g. `kb-agent` |
| `<mcp component id>:<tool name>` | orchestrator → one MCP tool, e.g. `tickets-mcp:delete_ticket` |
| `ingress:user` | user → (ingress gateway) → orchestrator |

`agdemo_core.themes.edge_ids(theme)` returns them all.

### Edge state (what the UI draws)

```json
{"state": "direct|allowed|denied|blocked|pending|unknown|error",
 "governed": true,
 "source": "live|simulated|replayed",
 "detail": "403 from gateway: iap.egressor missing",
 "http_status": 403}
```

| State | Meaning | Color |
|---|---|---|
| `direct` | Call succeeds and does **not** pass through a gateway (the gateway isn't attached) | grey/blue, solid |
| `allowed` | Goes through the gateway and policy allows it | green |
| `denied` | The gateway blocks it on IAM or policy (403) | red |
| `blocked` | Model Armor blocks it | orange, with a shield |
| `pending` | A policy change hasn't taken effect yet | amber, dashed and animated |
| `unknown` | Not tested yet | light grey, dotted |
| `error` | Infrastructure or other failure (not policy) | dark red |

## 3. Policy evaluation rules (simulator = expected behavior)

`agdemo_core/simulate.py: evaluate(theme, applied: set[policy_id], model_armor: bool, test: ScenarioTest|None) -> dict[edge_id, EdgeState]`

Egress edges, when the theme's `gateway_attach{path: egress}` is **not** applied:
- The edge is `direct` and `governed: false`.

Egress edges, when the egress gateway **is** applied:
- A2A edge `X` is `allowed` if a policy `a2a_allow{target: X}` is applied. Otherwise `denied`.
- MCP edge `S:T` is `allowed` if either of these is applied:
  - `mcp_server_allow{target: S}`
  - `mcp_tool_allow{target: S}` where `read_only: true` and T is read-only, or T is in `tools`

  Otherwise `denied`.
- If `model_armor` is on and `test.malicious` is true, every **MCP** edge that would have been `allowed` becomes `blocked`. A2A edges are not screened by egress Model Armor (verified behaviour, see ARCHITECTURE.md).

Ingress edge `ingress:user`. The ingress gateway (`CLIENT_TO_AGENT`) enforces Model Armor only, not caller identity, so this edge is **never `denied`**:
- `gateway_attach{path: ingress}` **not** applied: `direct`, `governed: false`.
- Ingress gateway applied: `allowed`, `governed: true`.
- Ingress gateway applied, `model_armor` on and `test.malicious` true: `blocked` (verified 403 "Model Armor: Prompt violates content security configurations").

In **Demo** mode this is the only source of edge state. In **Live** mode it's the *expected* state; the UI shows it as a ghost hint while a change is `pending`.

## 4. Policy handlers (Live)

`agdemo_core/policies/<type>.py` implements:

```python
class Handler(Protocol):
    def apply(self, ctx: Ctx, theme: Theme, policy: Policy) -> None: ...
    def remove(self, ctx: Ctx, theme: Theme, policy: Policy) -> None: ...
    def status(self, ctx: Ctx, theme: Theme, policy: Policy) -> PolicyStatus: ...
    def describe(self, ctx: Ctx, theme: Theme, policy: Policy) -> list[str]:
        """'under the hood' lines: gcloud/REST equivalents, shown in the UI."""
```

- `Ctx` holds `config: DemoConfig`, `state: dict`, and the authed clients and session.
- `PolicyStatus = {"applied": bool, "status": "applied|removed|pending|pending_removal|error", "detail": str, "changed_at": iso8601|None}`.
- A change counts as `pending` until either a probe confirms the expected outcome, or the propagation timeout passes (`PENDING_SECONDS = 600`).
- Model Armor uses the separate handler `agdemo_core/policies/model_armor.py`, with `set(ctx, enabled)` and `status(ctx)`. It is global and applies to every theme.
- `gateway_attach` handles both directions: it PATCHes `spec.deploymentSpec.agentGatewayConfig` on the theme's engine. Each direction is preserved independently.
- Handler modules (`_MODULES` in `agdemo_core/policies/__init__.py`): `gateway_attach`, and `egress_allow` for `a2a_allow`, `mcp_server_allow` and `mcp_tool_allow`. There is no ingress caller policy: the ingress gateway's only enforcement is the global Model Armor checkbox.
- Live ingress calls go through `agdemo_core/gcp/ingress_client.invoke_via_ingress(ctx, theme, message)`. It sends `:streamQuery` to the theme's engine with the backend's own credentials (the UI service account on Cloud Run, the operator's ADC locally); no impersonation. It returns a result for edge `ingress:user` with `outcome` `ok` or `blocked` (or `error`).

## 5. Runtime configuration (generated by the CLI)

`./agdemo deploy-theme <t>` turns each component's spec into JSON and passes it as the env var `COMPONENT_SPEC` (base64 JSON), or as a mounted file for large specs.

| Runtime | Env vars |
|---|---|
| `runtimes/mcp_server` | `COMPONENT_SPEC`: a `McpServerSpec` |
| `runtimes/a2a_agent` | `COMPONENT_SPEC`: an `A2AAgentSpec`, plus `MODEL`, `PUBLIC_URL` (used in the agent card), `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_GENAI_USE_VERTEXAI=TRUE` |
| `runtimes/orchestrator` | `ORCHESTRATOR_SPEC`: the theme's `Orchestrator` plus a `topology` list (below), plus `MODEL` and `AUTH_MODE` (`none` or `id_token`) |

The `topology` entries look like this:

```json
[{"id":"kb-agent","kind":"a2a_agent","display_name":"KB Agent","url":"https://...run.app","description":"...","skills":[...]},
 {"id":"tickets-mcp","kind":"mcp_server","display_name":"Tickets MCP","url":"https://...run.app/mcp","tools":[{"name":"get_ticket","read_only":true,"description":"..."}]}]
```

Endpoints:
- **MCP server:** Streamable HTTP at `/mcp`, and `GET /healthz`. Tool annotations come from `read_only` (`readOnlyHint` / `destructiveHint`).
- **A2A agent:** built with ADK `to_a2a()`. The agent card is at `/.well-known/agent-card.json` and the JSON-RPC endpoint is at `/`. Card skills come from `skills`.

## 6. Orchestrator tools and the probe protocol

The orchestrator has no `McpToolset` bound at import time, because a toolset that can't be reached would break the whole agent once the gateway denies it. Instead it exposes **three function tools**. Each one opens its connection per call and **never raises**:

| Tool | Does |
|---|---|
| `list_capabilities()` | Returns the topology (what the agent may *try* to use) |
| `call_agent(agent_id: str, message: str)` | Sends an A2A `message/send` to that component |
| `call_mcp_tool(server_id: str, tool: str, arguments: dict)` | Uses the MCP client over Streamable HTTP |

Each tool returns:

```json
{"edge": "tickets-mcp:get_ticket", "outcome": "ok|denied|blocked|error",
 "http_status": 200, "detail": "...", "result": <payload or null>, "latency_ms": 123}
```

How failures map to outcomes:
- HTTP 403 from the gateway or IAP becomes `denied`.
- A Model Armor block becomes `blocked`. It's recognized by response body or header; the exact signal is documented in `docs/ARCHITECTURE.md` once verified.
- Anything else becomes `error`.

**Probe mode (deterministic, no LLM).** A user message whose text starts with `__PROBE__ ` followed by JSON, for example `{"probes":[{"edge":"kb-agent"},{"edge":"tickets-mcp:delete_ticket"}], "malicious": false}`, is handled by a `before_model_callback`:
Each probe is `{"edge"}` plus optional overrides: `"message"` (A2A: the text to send) and `"args"` (MCP: tool arguments). Plain edge-id strings are also accepted. The backend sends a test's own prompt as `message` when the test probes a single A2A agent, or a scenario `Probe.message` / `Probe.args` when the theme sets one. The successful results (`result`) are shown in the activity log, so the audience sees what actually came back.

1. It runs each probe:
   - A2A probes send `message`, or else the component's `probe_message`.
   - MCP probes call the tool with `args`, or else its `probe_args`.
   - When `malicious` is true, it adds the theme's injection string from `ORCHESTRATOR_SPEC.malicious_payload`.
2. It replies with a single text part, `__PROBE_RESULT__ ` followed by JSON: `{"results":[<tool return object>...]}`.

The UI backend uses probe mode to report edge state. Natural-language "Run test" prompts use the LLM, and the backend maps the `function_call` / `function_response` events to edges using the returned `edge` field.

## 7. UI backend HTTP API (FastAPI package `agdemo_ui` in `ui/backend/agdemo_ui`)

Every mutating request carries `mode: "live" | "demo" | "live_with_fallback"`. Demo-mode policy state is kept in memory per theme on the server, so it's shared by every viewer.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/config` | | `{themes:[{id,name,description,deployed:bool}], default_theme, default_mode, environment:{project_id,region,prefix}, live_available:bool, can_admin:bool}` |
| GET | `/api/themes/{id}` | | `{theme: Theme (model_dump), nodes:[{id,type,label,sublabel,tools?}], edges:[{id,source,target,tool?,kind}]}` |
| GET | `/api/themes/{id}/state?mode=` | | `{policies:{pid: PolicyStatus}, gateways:{egress:{attached,status}, ingress:{attached,status}}, model_armor:{enabled,status}, edges:{edge_id: EdgeState}, expected:{edge_id: EdgeState}}` |
| POST | `/api/themes/{id}/policies/{pid}` | `{action:"apply"\|"remove", mode}` | PolicyStatus (Live calls return quickly with `pending`; the work runs in the background) |
| POST | `/api/model-armor` | `{enabled, mode}` | `{enabled,status}` |
| POST | `/api/themes/{id}/tests/{test_id}/run` | `{mode, use_llm: bool}` | **SSE** stream of events (below) |
| POST | `/api/themes/{id}/probe` | `{mode}` | `{edges:{...}}`: probes every edge, then refreshes the state |
| POST | `/api/themes/{id}/reset` | `{mode}` | Removes every applied policy and detaches the gateways (Live) |
| POST | `/api/themes/{id}/sync` | `{mode}` | `{policies:[{id,label,applied,status,detail}], model_armor:{enabled,status,detail}}`: re-reads everything from GCP (no caches) and clears pending states GCP shows as done, including stalled ones older than 15 min |
| POST | `/api/themes/{id}/verify` | `{mode}` | `{ok, checks:[{id,label,ok,detail}]}`: fresh (uncached) check that the theme is back at step 1: every policy removed, Model Armor off, and every egress connection probes `direct` |
| POST | `/api/themes/{id}/tests/{test_id}/record` | | Live only. Runs the test and saves a recording |
| GET | `/api/themes/{id}/policies/{pid}/explain` | | `{lines:[...]}` |
| GET | `/api/themes/{id}/gateway-logs?mode=&since=&denied_only=&limit=` | | Gateway log entries (§9) |
| GET | `/api/themes/{id}/registry?mode=&refresh=` | | Agent Registry entries and who may call each one (§10). Read-only |
| GET | `/api/themes/{id}/terraform?mode=&include_shared=` | | Terraform for the theme's current policy state (§11). Read-only |

The SSE events, all of which are JSON in the `data:` field:

```
{"type":"status","text":"Calling helpdesk-agent via Agent Runtime..."}
{"type":"edge","edge":"kb-agent","state":{EdgeState}}
{"type":"message","role":"agent"|"tool","text":"..."}
{"type":"fallback","reason":"policy change pending"}        # live_with_fallback switched to replay
{"type":"done","edges":{edge_id: EdgeState}}
{"type":"error","text":"..."}
```

The frontend is served by the backend as static files (`ui/frontend/dist`) at `/`. In local development, Vite proxies `/api` to `:8080`.

Implementation notes (additive to the table above):
- `/api/config` also returns `live_unavailable_reason` (empty when Live works). `default_mode` is forced to `demo` when `live_available` is false.
- `/api/themes/{id}/state?mode=live|live_with_fallback` when Live is unavailable returns every policy as `status: "error"`, edges `unknown`, plus `live_error`.
- In Live, an edge touched by a `pending`/`pending_removal` policy is reported as `state: "pending"`; `expected` holds the simulator's state. A pending change is confirmed by the first probe/test whose results for that policy's edges match `expected`; the backend also re-probes those edges every 30 s until `PENDING_SECONDS`.
- Test-run body also accepts optional `scenario_id` (test ids may repeat across scenarios; first match otherwise). Record body (optional): `{use_llm, scenario_id}`; returns `{saved, signature, events}`.
- `/api/themes/{id}/probe` in `live_with_fallback` returns simulated edges plus `fallback: <reason>` when the live probe fails.
- SSE `message` events may also carry `role: "user"` (the prompt that was sent).
- `can_admin` is always true locally; on Cloud Run (`K_SERVICE` set) the IAP header `X-Goog-Authenticated-User-Email` must match a `user:`/`serviceAccount:`/`domain:` entry of `ui.admin_access` (groups can't be resolved). Admin is required for Live policy toggles, Model Armor, reset and record; test runs and probes are open to all viewers.
- `GET /api/healthz` returns `{status: "ok"}`.

## 8. Recordings <a id="recordings"></a>

`themes/<id>/recordings/<test_id>/<signature>.json`, where `signature` is the sorted applied egress/ingress policy ids plus `ma-on` or `ma-off`, joined with `+` (or `none`).

```json
{"theme":"helpdesk","test":"ask-kb","signature":"gw-egress+allow-kb+ma-off","recorded_at":"...",
 "events":[ ...SSE events in order, with "t_ms" offsets... ]}
```

Replay works in two steps:
1. If a recording matches the signature, replay its events, keeping the relative timing (capped at 1.5 s per gap).
2. Otherwise, build the events from `simulate.evaluate`, with a short synthesized agent message.

Replayed edges carry `source: "replayed"` (or `"simulated"`).

Signature details: policy ids are all applied policy ids of the theme, sorted; when none are applied the policy part is the literal `none` (e.g. `none+ma-off`, `allow-kb+gw-egress+ma-on`). Live "Record" writes to `themes/<id>/recordings/` or, when `AGDEMO_RECORDINGS_DIR` is set (e.g. a writable volume on Cloud Run), to `$AGDEMO_RECORDINGS_DIR/<id>/<test_id>/<signature>.json`; loading checks that directory first, then the theme's own `recordings/`.

**Recording keys (relevant policies only).** A recording is filed under `simulate.test_signature(...)`:
the applied policies that can affect *that test's* connections (sorted, or `none`), plus `ma-on`/`ma-off`
for malicious tests or `ma-any` for benign ones. Example: "Ask for a salary" recorded with every helpdesk
policy and Model Armor on is keyed `allow-hr+gw-egress+ma-any`; at step 1 it's `none+ma-any`. Replay
looks up that key (then the older full-signature key). Record each test in the policy state you'll present.

**Storage.** `AGDEMO_RECORDINGS_URI=gs://<bucket>/<prefix>` stores recordings in GCS (what `./agdemo ui deploy`
sets: the staging bucket, so they survive restarts and redeploys); otherwise `AGDEMO_RECORDINGS_DIR` or
`themes/<id>/recordings/` on disk. Committed files under `themes/<id>/recordings/` are always a fallback.

## 9. Gateway logs <a id="gateway-logs"></a>

Agent Gateway writes one Cloud Logging entry per egress request it handles:
- log: `projects/P/logs/networkservices.googleapis.com%2Fgateway_requests`
- resource: `networkservices.googleapis.com/Gateway`, labelled `gateway_name=<egress gateway>`

The ingress gateway writes none, so these logs cover egress only (see ISSUES.md #1).

`GET /api/themes/{id}/gateway-logs?mode=&since=<iso>&denied_only=<bool>&limit=<n>` returns:

```json
{"source": "live|simulated",
 "filter": "<the Cloud Logging filter used>",
 "console_url": "https://console.cloud.google.com/logs/query;query=...?project=P",
 "entries": [GatewayLogEntry, ...]}          // newest first
```

`GatewayLogEntry`:

```json
{"id": "<insertId>", "timestamp": "2026-10-08T00:47:46.214Z",
 "gateway": "agdemo-egress",
 "decision": "allowed|denied|blocked",        // blocked = a Model Armor policy denied it
 "status": 403, "method": "POST", "url": "https://.../mcp", "host": "agdemo-helpdesk-tickets-mcp-....run.app",
 "mcp_method": "tools/call", "mcp_tool": "list_tickets",       // null for A2A / non-MCP
 "edge": "tickets-mcp:list_tickets",          // diagram edge id, or the component id when the tool is unknown, or null
 "component": "tickets-mcp",
 "policies": [{"name": "agdemo-egress-iap-policy", "kind": "iap|model_armor|other", "result": "DENIED"}],
 "decided_by": "agdemo-egress-iap-policy",    // the denying policy (null when allowed)
 "summary": "DENIED by agdemo-egress-iap-policy · tools/call list_tickets · 403",
 "console_url": "<Logs Explorer link to this exact entry>",
 "simulated": false,
 "raw": { ...the full Cloud Logging entry... }}
```

- `since` is any RFC 3339 timestamp (e.g. `Date.toISOString()`); it defaults to 15 minutes ago, and `limit` to 50.
- `console_url` is `null` for simulated entries and in Demo mode. A2A summaries name the target agent ("POST / → KB Agent").
- Entries only come from hosts that belong to the theme's components.
- In **Demo** mode, and for runs that fell back to replay or simulation, entries are synthesized from the run's governed edge results. They have the same shape, with `simulated: true`, and their `raw` mimics the real format.
- Expect a delay of a few seconds to about a minute before real entries can be read.
- **Calling agent** (themes with `additional_orchestrators`, §12): see "Gateway log attribution" in §12. Entries then also carry `caller: {"orchestrator": <id or null for the primary>, "display_name"}` (or `null`), and the summary ends with "(from <agent>)".

## 10. Registry view <a id="registry-view"></a>

`GET /api/themes/{id}/registry?mode=&refresh=<bool>` lists the theme's Agent Registry entries and, for each one, who holds `roles/iap.egressor` on it (who may call it through Agent Gateway). It never changes anything in GCP: in Live it only lists the registry projections (`agents`, `mcpServers`, `endpoints`) and calls IAP `getIamPolicy` on `iap.googleapis.com/v1/projects/<NUM>/locations/<R>/iap_web/agentRegistry/{agents|mcpServers|endpoints}/<id>`. Logic: `agdemo_core/gcp/registry_view.py`.

```json
{"theme": "helpdesk", "source": "live|simulated", "fetched_at": "<iso>",
 "console_url": "https://console.cloud.google.com/agent-platform/agent-registry?project=P",
 "orchestrator": {"display_name": "Helpdesk Agent", "principal": "principal://agents.global.org-…/reasoningEngines/123"},
 "agents": [RegistryItem, ...],          // the theme's A2A agents, then the orchestrator's auto-registered entry
 "mcp_servers": [RegistryItem, ...],
 "endpoints": {"count": 25, "items": [RegistryItem, ...]},   // <prefix>-plat-* platform endpoints
 "error": "list endpoints: HTTP 403 …"}  // only when part of the view could not be read (or why it is simulated)
```

`RegistryItem`:

```json
{"id": "tickets-mcp",                    // component id, "orchestrator", or the endpoint URL
 "kind": "a2a_agent|mcp_server|endpoint|orchestrator",
 "display_name": "…", "label": "auto-registered (Agent Runtime)",   // label: orchestrator only
 "resource": "projects/P/locations/R/mcpServers/agentregistry-…", "registry_id": "agentregistry-…",
 "description": "…", "url": "https://…/mcp",
 "card": {"name", "description", "version", "protocol_version", "skills": [Skill]},   // A2A agents
 "skills": [{"id", "name", "description", "tags": []}],                               // A2A agents
 "tools": [{"name", "description", "read_only": true,                                // MCP servers
            "annotations": {"readOnlyHint": true, "idempotentHint": true}}],
 "access": [Access, ...],                // [] = no one (default deny); null when the IAP policy couldn't be read
 "access_error": null, "error": null}    // per-item failures; the rest of the view still loads
```

`Access` (one per member of each `roles/iap.egressor` binding):

```json
{"member": "principal://…", "member_label": "Helpdesk Agent (Agent Identity)",
 "member_kind": "orchestrator|project_agents|agent|user|service_account|group|domain|other",
 "role": "roles/iap.egressor",
 "condition": {"title": "agdemo helpdesk tickets-readonly-hints", "expression": "api.getAttribute(…)"} ,   // null = unconditional
 "policy_id": "tickets-readonly-hints",  // the theme policy that creates this binding, else null
 "policy_text": "…", "note": "platform allowlist (./agdemo bootstrap)"}
```

- The orchestrator's principal becomes "<orchestrator> (Agent Identity)"; the project's `principalSet://…/attribute.platformContainer/aiplatform/projects/<NUM>` becomes "All agents in project (Agent Identity)".
- `policy_id` matches orchestrator bindings to theme policies: unconditional for `a2a_allow` / `mcp_server_allow`, and the exact condition expression from `egress_allow.condition()` for `mcp_tool_allow`.
- Live results are cached for 15 s per theme (`refresh=true` skips the cache); GCP calls run in a thread pool.
- **Demo** mode (and `live_with_fallback` when Live isn't available) returns the same shape with `source: "simulated"`, built from the theme files (`agents.yaml` skills, `tools.yaml` `read_only` as the MCP hints) and the bindings the applied Demo policies would create. `mode=live` without Live access returns 409.

## 11. Terraform export <a id="terraform-export"></a>

`GET /api/themes/{id}/terraform?mode=&include_shared=<bool>` renders Terraform (HCL) for the theme's current policy state. It never changes anything in GCP. Logic: `agdemo_core/terraform.py` (`render(cfg, state, theme, applied, model_armor, include_shared) -> {"main.tf", "variables.tf", "README.md"}`, a pure function); CLI: `./agdemo export-terraform <theme> [--out DIR] [--include-shared]` (Live state, default output `config/generated/terraform/<theme>`).

```json
{"theme": "helpdesk", "source": "live|simulated",
 "files": {"main.tf": "…", "variables.tf": "…", "README.md": "…"},
 "applied": ["gw-egress", "allow-kb", "tickets-readonly-hints"],     // theme order
 "model_armor": false, "include_shared": false,
 "state_line": "gw-egress, allow-kb, tickets-readonly-hints · Model Armor off",
 "error": "…"}                                                         // only when live_with_fallback fell back
```

- **Policy state** is the one the UI shows. Live: the handlers' desired applied set (`applied` and not `pending_removal`, i.e. `LiveEngine.signature_now`) plus the Model Armor status. Demo: the in-memory Demo store. `live_with_fallback` without Live access uses the Demo store with `source: "simulated"` and `error`; `mode=live` returns 409.
- Ids come from `config/state.json` (engine, orchestrator principal, registry projection ids, Cloud Run URLs); missing values become UPPER_CASE placeholders, listed in a `PLACEHOLDERS` comment.
- Provider: `hashicorp/google` `>= 8.1.0` (no `google-beta`), Terraform `>= 1.5`. Variables: `project_id`, `project_number`, `gateway_project_id`, `gateway_project_number` (both default to the main project), `region`, `prefix`.
- Mapping (resources named after the policy id, each with a comment holding the policy text):

| Policy | Terraform |
|---|---|
| `a2a_allow` | `google_iap_agent_registry_agent_iam_member` (`roles/iap.egressor`, member = orchestrator principal, `project` = gateway project number) |
| `mcp_server_allow` | `google_iap_agent_registry_mcp_server_iam_member` |
| `mcp_tool_allow` | the same with `condition {}` = `egress_allow.condition(theme, policy)` (tool names, or `mcp.tool.isReadOnly`) |
| `gateway_attach` (egress / ingress) | one `terraform_data.gateway_binding` with a `local-exec` `curl -X PATCH …?updateMask=spec.deploymentSpec.agentGatewayConfig` carrying every attached direction, and a destroy-time PATCH with `{}`; commented `google_vertex_ai_reasoning_engine.spec.deployment_spec.agent_gateway_config` example |
| Model Armor on | `google_network_security_authz_policy.model_armor_{egress,ingress}` (`CONTENT_AUTHZ`, `CUSTOM`, Model Armor authz extension) |

- `include_shared=false` references the shared resources by name in `locals`. `include_shared=true` also emits them: `google_network_services_agent_gateway` ×2, `google_network_services_authz_extension` (IAP egress, Model Armor per gateway project), `google_network_security_authz_policy` (IAP `REQUEST_AUTHZ`), `google_model_armor_template`, `google_agent_registry_service` (theme components with their A2A card / MCP tool spec, and the platform endpoints) with `google_iap_agent_registry_endpoint_iam_member` for all project agents, the UI and targets service accounts, and project IAM (UI, targets, presenters, orchestrator identity, Model Armor service agents). Registry ids then come from `basename(google_agent_registry_service.<c>.registry_resource)`.
- Output passes `terraform fmt -check` and `terraform validate` (checked against provider 8.6.0); it has not been applied to GCP.

## 12. Multiple agents per theme (backlog #3) <a id="multi-agent"></a>

A theme can have **additional orchestrators** next to the primary one. They're separate Agent Runtime engines, each with its own Agent Identity, built from the same orchestrator package (their own `ORCHESTRATOR_SPEC`, with the same topology). They're bound to the **same** shared gateways, because GCP requires one egress and one ingress gateway per project and region. The point the demo makes is that access follows the agent's identity.

**theme.yaml**

```yaml
orchestrator: {...}                      # primary, unchanged
additional_orchestrators:                # optional, default []
  - id: hr-assistant                     # short id, unique in the theme
    display_name: HR Assistant
    description: ...
    instruction: ...
```

**Policies** take an optional `params.source: <orchestrator id>`. Without it, the policy applies to the primary orchestrator, so existing policies are unchanged.
- `gateway_attach{path: egress, source: hr-assistant}` binds *that* engine to the egress gateway.
- `a2a_allow` / `mcp_server_allow` / `mcp_tool_allow` with a `source` grant `roles/iap.egressor` to *that* engine's Agent Identity principal.
- Ingress policies stay primary-only.

**Edge ids.** Primary edges are unchanged (`kb-agent`, `tickets-mcp:get_ticket`, `ingress:user`). Edges from an additional orchestrator are `<orchestrator id>/<edge>`, for example `hr-assistant/hr-records-agent` or `hr-assistant/tickets-mcp:get_ticket`. Helpers in `agdemo_core/themes.py`:
- `split_source(edge) -> (orchestrator_id | None, base_edge)`
- `source_edge(orchestrator_id | None, base_edge) -> str`
- `orchestrator_ids(theme)`

`edge_ids(theme)` returns the primary egress edges, then every additional orchestrator's egress edges, then `ingress:user`.

**Node ids.** `orchestrator` is the primary; `orchestrator:<id>` is an additional one. Scenarios list the nodes they show, e.g. `[orchestrator, orchestrator:hr-assistant, egress_gateway, registry, kb-agent, hr-records-agent]`.

**Simulator.** Each orchestrator is evaluated on its own. Its egress edges are `direct` unless *its* egress `gateway_attach` (matched by `source`) is applied. When it is applied, an edge is allowed only by allow policies with the same `source`. Model Armor rules are unchanged.

**state.json.** The primary stays at `themes.<t>.orchestrator`. Additional ones go in `themes.<t>.orchestrators.<id> = {engine, principal, registry, display_name, identity_type}`.

**Probes and runs.** The backend groups edges by orchestrator and sends each group's base edge ids to that engine's `__PROBE__`. Results are mapped back to prefixed edge ids. A test can name its agent with `ScenarioTest.agent: <orchestrator id>` (default primary); natural-language runs (`use_llm`) go to that agent's engine.

**Reset and Verify** cover every orchestrator's gateway binding and allows. Recording signatures and the Terraform export include `source` policies like any other.

**Verified notes (2026-10-08, helpdesk + HR Assistant, live)**
- Implementation: `Theme.additional_orchestrators`, `ScenarioTest.agent`, `split_source` / `source_edge` / `orchestrator_ids` / `base_egress_edges` in `agdemo_core/themes.py`; the loader rejects an unknown `source`, `source` on an ingress `gateway_attach`, an unknown `orchestrator:<id>` scenario node and an unknown test `agent`. Graph nodes `orchestrator:<id>` have `type: "orchestrator"`, `label` = display name, `sublabel` "Agent Runtime · <id>"; their edges are the primary's with `id` prefixed and `source: "orchestrator:<id>"`. Simulated labels name the caller ("HR Assistant → HR Records Agent"). `/registry` adds the additional orchestrators' auto-registered entries (`id: "orchestrator:<id>"`), labels their principals "<name> (Agent Identity)" (`member_kind: "orchestrator"`, `policy_id` matched within the same `source`) and returns `additional_orchestrators: [{id, display_name, principal}]`. Terraform: `member = local.agent_principals["<id>"]` for sourced allows, and one `terraform_data.gateway_binding_<id>` per additional engine that has a gateway.
- Engines: `deploy-theme` creates `<prefix>-<theme>-<id>` (AGENT_IDENTITY, no gateway) and reuses existing engines; the two engines' gateway PATCHes run in parallel (they're serialized per engine only). Applying `gw-egress` + `gw-egress-hr` + `allow-kb` + `hr-assistant-allow-hr` gave: Helpdesk Agent → KB allowed, Helpdesk Agent → HR Records denied (403 "Egress request is not authorized"), HR Assistant → HR Records allowed, HR Assistant → KB denied. Probes sent right after the PATCH operation finished can still reach the old (unbound) revision and come back as plain successes for a minute or so.
- **Gateway log attribution.** `gateway_requests` entries have no principal or engine field. They do carry the calling agent's mTLS client certificate fingerprint (`jsonPayload.mtls.clientCertSha256Fingerprint`): it differs per engine and is the same on that engine's own platform calls, whose URL names the engine (`…/reasoningEngines/<id>/sessions/…:appendEvent`). The fingerprint rotates (it changed for the same engine across a redeploy), so `gateway_logs.fetch` learns `{fingerprint: orchestrator}` per query from platform entries of the theme's engines (2 h before `since`) and maps each entry to the caller's edge (`hr-assistant/hr-records-agent`). Entries whose fingerprint isn't matched (or matches several engines) get `edge: null` (with `component` set) in a theme with additional orchestrators; themes with one orchestrator are unchanged. Not every request is necessarily logged: some probe calls in this run had no entry.

