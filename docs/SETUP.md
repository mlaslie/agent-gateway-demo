# Setup Guide — Agent Gateway & Agent Registry Demo

> **Status:** every command below has been run end to end in one environment (us-east4). A clean install in a second project, the portability test, is still to do. Known product gaps and workarounds are listed in [ISSUES.md](ISSUES.md).

New to the project? Read the [README](../README.md) first for what the demo shows and how the parts fit together.

This guide is for people comfortable with Google Cloud projects, IAM, Cloud Run and gcloud. It deploys the demo into **your own** project. Nothing in the repo is tied to a particular environment.

---

## 1. What gets deployed

| Layer | Resources (all named `<prefix>-…`, all in one region) |
|---|---|
| Governance | Egress gateway (`AGENT_TO_ANYWHERE`), ingress gateway (`CLIENT_TO_AGENT`), IAP authz extension + policy, Model Armor template + authz extension |
| Discovery | Agent Registry entries for every agent, MCP server and platform endpoint |
| Per theme | 1 orchestrator agent on Agent Runtime (with Agent Identity) · its A2A agents and MCP servers on Cloud Run (2 + 2 in the shipped `helpdesk` and `retail` themes), all serving mock data |
| Demo UI | Cloud Run service (FastAPI + React) behind IAP, plus its service account |
| Supporting | Artifact Registry repo, Cloud Build, service accounts, IAM bindings |

Architecture details: `docs/ARCHITECTURE.md`.

## 2. Prerequisites

- **A project with billing**, in an org where you can turn on IAP. A dedicated or sandbox project is recommended.
- **A supported region.** Agent Gateway, Agent Runtime, Agent Registry and Cloud Run must all be available there, because the gateway and the agent must be in the same region. `./agdemo preflight` checks this.
- **No conflicting gateway bindings.** All Agent Runtime agents in a project and region must share the same egress and ingress gateway. If that region already has agents bound to other gateways, either pick another region or set `gateways.create: false` and reuse the existing gateways.
- **Tools:** `gcloud` (recent, with beta components), Python 3.11+, `uv`, Node 20+ (only for local UI development), `jq`.
- **Your permissions.** Project Owner is simplest. Otherwise you need admin rights for Network Services, Network Security, Service Extensions, IAP, Agent Registry, Vertex AI / Agent Runtime, Cloud Run, Model Armor, Artifact Registry, Cloud Build, Service Accounts, Project IAM and Service Usage. Preflight lists anything missing.
- **Agent Runtime restrictions:**
  - Agents bound to a gateway can't use revisions or traffic splitting.
  - Engines created before 2026-04-29 can't be bound.
  - VPC Service Controls isn't supported with Agent Gateway.

## 3. Configure

```bash
git clone <repo-url> agent-gateway-demo
```

```bash
cd agent-gateway-demo && cp config/demo.example.yaml config/demo.yaml
```

Edit `config/demo.yaml`. At minimum, set:

| Key | What to set |
|---|---|
| `environment.project_id` | Your project |
| `environment.region` | A supported region (see prerequisites) |
| `environment.resource_prefix` | A short unique prefix (lowercase, 10 characters or fewer), so several copies can share a project |
| `ui.iap_access` / `ui.admin_access` | Who can view the UI / who can change Live policy |
| `ingress_demo.allowed_principal` | Usually the presenter's account |

Optional: `gateway_project_id` (egress gateway in another project), `gateways.create: false` (reuse existing gateways), `themes.enabled` / `themes.default`, `default_mode`, `models.default`, `model_armor.filters`, `cloud_run.public_targets` (whether the mock Cloud Run targets allow unauthenticated invocation; enforcement happens at the gateway either way).

Authenticate:

```bash
gcloud auth login && gcloud auth application-default login
```

## 4. Deploy

```bash
./agdemo preflight
```

Shows a pass/fail table (auth, billing, roles, APIs, region support, quotas, gateway conflicts) with the fix for each failure. You can let it enable missing APIs.

```bash
./agdemo bootstrap
```

Creates the shared resources: APIs, service accounts, Artifact Registry, gateways, IAP and Model Armor authz extensions, and platform allowlist entries in the registry. **Expect about 10–20 minutes**, mostly gateway creation.

```bash
./agdemo deploy-theme helpdesk
```

Builds the generic runtime images if needed, deploys the theme's Cloud Run services (`<prefix>-<theme>-<component>`), registers them in Agent Registry, and creates the orchestrator on Agent Runtime **with Agent Identity and no gateway** (the wide-open start state). Repeat for each theme in `themes.enabled` that you want live, for example `./agdemo deploy-theme retail`. Themes that aren't deployed still work in Demo mode.

```bash
./agdemo ui deploy
```

Deploys the UI to Cloud Run behind IAP and prints its URL. Alternatively, run it locally with `./agdemo ui local`.

Every step is idempotent. Generated IDs and URLs go into `config/state.json`. If a step fails, fix the cause and re-run it.

## 5. Verify

```bash
./agdemo status
```

For each theme this shows the resources, gateway bindings, which policies are applied, and whether a wide-open smoke test passed. Then open the UI, choose the theme, mode **Live**, the **Wide open** tab, and click **Run test**. Every line in the diagram should show as a direct, successful call.

## 6. Running the demo

- **Theme** drop-down: picks the use case. **Mode**: Live / Demo (simulated) / Live with fallback. **Model Armor** checkbox: applies to all scenarios. **Reset**: returns the theme to wide open.
- IAM and gateway changes take **about 1–7 minutes** to take effect. Run the demo in **Live with fallback**, or apply the policies a few minutes before presenting.
- `./agdemo reset <theme>` (or **Reset** in the UI) removes that theme's applied policies and detaches its gateways.
- No GCP project at all? `./agdemo ui local --mode demo` runs the whole UI from recordings and the simulator.

The presenter checklist and talk track are in `docs/DEMO_SCRIPT.md`.

## 7. Optional: Gemini Enterprise (GE Demo mode)

GE Demo mode shows the agent-to-anywhere policies from the Gemini Enterprise UI: you chat with the theme's agent in Gemini Enterprise, while this UI shows and changes the policies.

1. In `config/demo.yaml`, set `gemini_enterprise.app_id` (the engine id of an existing Gemini Enterprise app), `location` (`global`, `us` or `eu`) and `app_url` (the app's web URL, linked from the UI).
2. Register each deployed theme's agent in that app (ADK registration through `agents-cli`; idempotent):

```bash
./agdemo publish-ge helpdesk
```

3. Run `./agdemo ui deploy` so the Cloud Run UI picks up the config.
4. In the UI, tick **GE Demo**. Clicking a test now copies its prompt to the clipboard instead of running it. Paste the prompt into Gemini Enterprise and pick the agent "<Agent> (Agent Gateway demo)". **Show what happened** probes the connections so the diagram reflects the current policy.

Notes:
- Gemini Enterprise calls the agent with the signed-in user's credentials, so presenters need `roles/aiplatform.user` on the project (bootstrap grants it to `ingress_demo.allowed_principal`).
- Gemini Enterprise supports the egress gateway only. Use GE Demo for the egress scenarios (1–4 and 6) and detach the ingress gateway first; whether Gemini Enterprise calls succeed with an ingress gateway attached hasn't been tested.

## 8. Adding themes and scenarios

Copy `themes/_template/` to `themes/<id>/`, edit the YAML (orchestrator, components, agents, tools, policies, scenarios), and validate it with `load_theme('<id>')`. Then add it to `themes.enabled` and run `./agdemo deploy-theme <id>`. No code changes are needed unless you add a new *kind* of policy. See `docs/ADDING_A_THEME.md`.

## 9. Costs and cleanup

Costs come from the gateways, the Agent Runtime agents (which run continuously), Cloud Run (scales to zero), Model Armor and Gemini calls.

```bash
./agdemo teardown helpdesk
```

```bash
./agdemo teardown --all
```

`--all` removes every resource carrying the configured prefix and labels, including the gateways. It does **not** turn off APIs or touch resources it didn't create.

## 10. Troubleshooting (quick list)

| Symptom | Usual cause |
|---|---|
| Every tool call fails, even allowed ones | `iap.egressor` binding not in effect yet, or a registry hostname variant (regional / `.mtls.`) isn't registered |
| Agent can't reach the model once the gateway is attached | Platform endpoints missing from the allowlist; re-run `bootstrap` |
| Binding the gateway fails | A different gateway is already bound in this project and region, or the engine is older than 2026-04-29 or has revisions |
| Model Armor never blocks anything | Engine wasn't created with Agent Identity, or the Model Armor authz policy isn't attached |
| UI changes in Live mode don't show | Still taking effect. The line shows *pending*; use Live with fallback |

More in `docs/TROUBLESHOOTING.md`.
