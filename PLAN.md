# Agent Gateway + Agent Registry Demo — Plan

Reference environment: project `my-demo-project`, region proposed `us-east4` (must support Agent Gateway + Agent Runtime). It's separate from `us-central1` so `demo-gateway-001` and the existing demos are untouched.
**Nothing environment-specific is hard-coded.** Every project, region and name comes from `config/demo.yaml`, so anyone can run the demo in their own environment (see "Portable configuration").

## Decisions
| Topic | Decision |
|---|---|
| Gateway placement | New region, dedicated egress (`AGENT_TO_ANYWHERE`) + ingress (`CLIENT_TO_AGENT`) gateways and regional Agent Registry |
| Run mode | UI selector: **Live** (real GCP only) · **Demo** (simulated, no GCP changes) · **Live with fallback** (real GCP; instant replay if enforcement is pending or a call fails) |
| UI hosting | Cloud Run behind IAP |
| Theme / use case | UI drop-down; themes are plug-in packs. First pack: IT / HR helpdesk. Second pack (e.g. retail ops) proves the pattern |

## Portable configuration
The single source of truth is `config/demo.yaml`, created from the committed `config/demo.example.yaml`. The real file is git-ignored.

```yaml
environment:
  project_id: my-project            # Runtime agent, ingress gateway, Cloud Run, UI
  gateway_project_id: ""            # optional; egress gateway/registry project (defaults to project_id)
  region: us-east4                  # gateways, Runtime, registry, Cloud Run (must all match)
  resource_prefix: agdemo           # prefixed onto every created resource; lets several copies coexist
  labels: {app: agent-gateway-demo} # put on every resource that supports labels, for discovery/cleanup
gateways:
  create: true                      # false = reuse existing gateways (names below)
  egress_name: agdemo-egress
  ingress_name: agdemo-ingress
  iap_enforcement: ENFORCE          # or DRY_RUN
models:
  default: gemini-2.5-flash
model_armor:
  template_id: agdemo-shield
  filters: [prompt_injection_jailbreak, sensitive_data]
ui:
  deploy: cloud_run                 # cloud_run | local
  iap_access: ["group:demo-viewers@example.com"]   # who can open the UI
  admin_access: ["user:presenter@example.com"]     # who can toggle Live policies (bootstrap grants them roles/aiplatform.user)
themes:
  enabled: [helpdesk, retail]
  default: helpdesk
default_mode: live_with_fallback    # live | demo | live_with_fallback
```

- **One CLI, `./agdemo`.** A Python CLI that reads the config file. Commands: `preflight`, `bootstrap`, `deploy-theme <t>`, `ui deploy`, `status`, `reset <t>`, `teardown [--all]`. The UI backend and every script use the same config loader, so nothing has to be kept in sync.
- **Preflight checks:** gcloud auth, billing, the caller's IAM roles, API enablement, whether Agent Gateway and Agent Runtime are available in the region, quotas, and conflicts with existing Runtime gateway bindings in that project and region. It reports a pass/fail table and the exact fix for each failure.
- **Idempotent.** Every step is "create if missing, else verify". Generated values (engine IDs, Cloud Run URLs, registry IDs) are written to `config/state.json`, which is git-ignored. Re-running is safe.
- **No secrets in the repo.** It uses Application Default Credentials and service accounts only. The image registry is Artifact Registry in the user's own project.
- **Reuse instead of create:** existing gateways (`gateways.create: false`), or a separate gateway project for cross-project egress.
- **Demo mode with no GCP at all:** `./agdemo ui local --mode demo` runs the UI from recordings only. Handy for previewing or presenting with no project.

## Modular themes and scenarios
New themes and scenarios are added as **data**. No code changes are needed for the common cases.

```
themes/
  helpdesk/
    theme.yaml          # display name, nodes (agents, MCP servers, skills), registry metadata
    tools.yaml          # MCP tools: name, description, readOnly/destructive, mock response data
    agents.yaml         # A2A agents: instructions, skills, model
    scenarios/
      01-wide-open.yaml
      02-deny-all.yaml
      03-a2a-allow-c-deny-d.yaml
      ...               # each: title, diagram edges, policies (plain English + policy type + params),
                        #       test prompts, expected outcome per edge
    recordings/         # captured runs used by Demo and fallback modes
  retail/ ...
```

- **Generic runtimes:** one ADK A2A agent image and one MCP server image, both configured from the theme files. A new theme deploys new Cloud Run services from the same images with different config.
- **Generic Agent Runtime agent:** one orchestrator package, with its instructions and registry discovery filter set per theme.
- **Typed policies:** each policy in a scenario file names a policy type (`gateway_attach`, `a2a_allow`, `mcp_tool_allow`, `mcp_server_allow`, `model_armor`). Each type has one apply/remove/status handler in the backend that turns it into the real GCP calls. A new kind of policy is the only thing that needs new code: one handler plus a diagram rendering rule.
- **Resource naming:** every resource is named `<resource_prefix>-<theme>-<component>` (e.g. `agdemo-helpdesk-tickets-mcp`). Themes can be deployed side by side, and `./agdemo deploy-theme <t>` / `teardown <t>` work on one theme at a time.
- **Shared across themes:** the gateways are shared, because the region allows only one egress and one ingress gateway per project. The UI's Reset and the policy handlers only touch resources belonging to the selected theme.

## Run modes
| Mode | Policy toggles | "Run test" | Diagram |
|---|---|---|---|
| **Live** | Real GCP changes | Real agent call | Shows actual GCP state; "pending" while changes take effect |
| **Demo** | Simulated, instant | Replays the theme's recordings | Driven by expected outcomes in the scenario files |
| **Live with fallback** | Real GCP changes | Real call; on timeout, error, or while a change is still pending, replays the recording | Real state, with a "replayed" badge when a recording is shown |

Demo mode needs no GCP access, so it works offline and for themes that haven't been deployed yet. A small "Record" action in Live mode captures runs into `recordings/`.

## Components (all Google ADK) — helpdesk theme
| Diagram node | Component | Host | Role |
|---|---|---|---|
| Agent A `helpdesk-agent` | ADK orchestrator, created with `AGENT_IDENTITY` | Agent Runtime | Starts wide open; gets governed |
| Agent C `kb-agent` | ADK agent over A2A (`to_a2a`), skills: search_kb, summarize_article | Cloud Run | Allowed A2A peer |
| Agent D `hr-records-agent` | ADK agent over A2A, skills: lookup_employee, get_compensation | Cloud Run | Denied A2A peer |
| MCP B `tickets-mcp` | FastMCP. Read: `list_tickets`, `get_ticket`. Write: `close_ticket`, `delete_ticket` (readOnlyHint / destructiveHint) | Cloud Run | Tool-level policy |
| MCP E `directory-mcp` | FastMCP. `lookup_user`, `reset_password` | Cloud Run | Server-level allow/deny |
| Agent Registry | All of the above + A2A agent cards/skills | Demo region | Discovery + egress allowlist |

## Scenarios
0. **Wide open** — Agent A, no gateway; direct calls to C, D, B (all tools), E. All edges grey/green.
1. **Gateway attached = default deny** — PATCH `agentGatewayConfig` (egress). Only platform endpoints (model, logging, trace, sessions) allowlisted. Every agent and MCP edge goes red (403).
2. **Agent A → Gateway → A2A on Cloud Run** — "Helpdesk agent can talk to KB agent over A2A, but not HR-records agent." (`roles/iap.egressor` on C's registry endpoint only)
3. **Agent A → Gateway → MCP** — "Helpdesk agent can only use the tickets MCP server's read-only tools." (Conditional egressor binding on the MCP tool; fallback: split read/write into separate registry endpoints.)
4. **User → Ingress gateway → Agent A** — "Every call into the helpdesk agent goes through the gateway, and Model Armor screens it." One user: a normal request is allowed (governed); with Model Armor on, a prompt injection / PII request is blocked with a 403 before it reaches the agent. The ingress gateway (`CLIENT_TO_AGENT`) enforces Model Armor only, not caller identity; who may call is IAM on Agent Runtime (`roles/aiplatform.user`).
5. **Model Armor checkbox (all scenarios)** — Turns a Model Armor authz extension on the gateways on or off. Prompt-injection and PII prompts get blocked, shown as a shield on the gateway node.

## Web UI
- React + React Flow diagram; one tab per scenario; edge states: direct / allowed / denied / pending.
- Policy panel: plain-English sentence + Apply/Remove toggle; expandable "under the hood" with the real IAM/gcloud resource.
- "Run test": sends a fixed prompt to Agent A and lights up each call it makes with the result (200 / 403 / Model Armor block).
- FastAPI backend reads actual GCP state (engine gateway config, registry IAM, authz policies) and returns the diagram state from it.
- Header controls: **Theme** drop-down · **Mode** selector (Live / Demo / Live with fallback) · **Model Armor** checkbox · Reset.
- Scenario tabs are generated from the selected theme's `scenarios/` files; Reset returns that theme to its wide-open start.

## Repo layout
```
runtimes/
  orchestrator/        # generic ADK agent for Agent Runtime (configured by theme)
  a2a_agent/           # generic ADK A2A agent image for Cloud Run
  mcp_server/          # generic FastMCP server image for Cloud Run
themes/<theme>/        # see "Modular themes and scenarios"
config/                # demo.example.yaml (committed); demo.yaml + state.json (git-ignored)
agdemo                 # CLI entry point: preflight / bootstrap / deploy-theme / ui / status / reset / teardown
infra/                 # step implementations used by the CLI: gateways, registry, IAM, Model Armor, IAP
docs/                  # SETUP.md, DEMO_SCRIPT.md, ADDING_A_THEME.md, ARCHITECTURE.md, TROUBLESHOOTING.md
ui/
  backend/             # FastAPI: theme loader, policy handlers, mode engine (live / demo / fallback)
  frontend/            # React + React Flow
```

## Build phases
1. Config schema + loader, `./agdemo preflight` and `bootstrap`: APIs, service accounts, gateways (IAP authz per config), Model Armor template.
2. Generic runtimes (orchestrator, A2A agent, MCP server) + theme schema; helpdesk theme files.
3. `./agdemo deploy-theme helpdesk`: Cloud Run services, registry entries, the Agent Runtime agent with AGENT_IDENTITY and no gateway; verify wide-open calls.
4. Policy handlers (apply/remove/status) per policy type; verify each scenario by hand in Live.
5. UI: theme drop-down, mode selector, scenario tabs, Model Armor checkbox; deploy to Cloud Run with IAP.
6. Record runs for Demo/fallback; add a second theme to prove the plug-in pattern; finish the docs set.
7. Portability test: a clean run from the README in a fresh project (or with a different prefix and region) and a full teardown, to confirm nothing is hard-coded.

## Things to verify early
- IAM conditions on individual MCP tools through the gateway (else use separate read/write endpoints).
- Removing a gateway from an engine (PATCH to empty config) works, and how long the PATCH takes.
- Agent Gateway + Agent Runtime availability in the chosen region.
