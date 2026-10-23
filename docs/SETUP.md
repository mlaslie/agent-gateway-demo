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
- **A Google Cloud organization is best, but not required.** Agent Identity principals use the organization's trust domain (`principal://agents.global.org-<ORG_ID>.system.id.goog/…`, see [ARCHITECTURE.md](ARCHITECTURE.md) §4); `./agdemo tf init` looks up the organization id (or set `organization_id`). With no organization, the CLI and Terraform use the project's trust domain, `agents.global.proj-<PROJECT_NUMBER>.system.id.goog`, as Google's Agent Identity docs describe. That path hasn't been tested live ([BACKLOG.md](BACKLOG.md) #19).
- **A supported region.** Agent Gateway, Agent Runtime, Agent Registry and Cloud Run must all be available there, because the gateway and the agent must be in the same region. `./agdemo preflight` checks this.
- **No conflicting gateway bindings.** All Agent Runtime agents in a project and region must share the same egress and ingress gateway. If that region already has agents bound to other gateways, either pick another region or set `gateways.create: false` and reuse the existing gateways.
- **Tools:** `gcloud` (recent, with beta components), **Terraform 1.7+** (`./agdemo up` builds the shared foundation with it), Python 3.11+, `uv`, `jq`. Node 20+ only for local UI development.
- **Your permissions.** Project Owner is simplest. Otherwise a project owner grants you the deployer roles once: `./agdemo deployer-roles --member user:you@example.com` prints the commands (`--apply` runs them). That's the 15 predefined roles of [PERMISSIONS.md](PERMISSIONS.md) §2.2 (plus `roles/compute.networkAdmin` and `roles/dns.admin` for Private Networking), or 14 trimmed ones with `--auth-strategy minimum` (see `iam.auth_strategy` below). Both sets were tested live end to end (`up`, a Live scenario, `down`). Preflight lists anything missing.
- **Agent Runtime restrictions:**
  - Agents bound to a gateway can't use revisions or traffic splitting.
  - Engines created before 2026-04-29 can't be bound.
  - VPC Service Controls only works with a gateway created after 2026-09-08 that uses a connectivity template with `ALL_TRAFFIC` egress (not used by this demo).

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
| `ui.iap_access` / `ui.admin_access` | Who can view the UI / who can change Live policy (`./agdemo up` also grants `ui.admin_access` members `roles/aiplatform.user`, see §7) |

Optional: `gateway_project_id` (egress gateway in another project), `gateways.create: false` (reuse existing gateways), `themes.enabled` / `themes.default`, `default_mode`, `models.default`, `model_armor.filters`, `cloud_run.public_targets` (whether the mock Cloud Run targets allow unauthenticated invocation; enforcement happens at the gateway either way).

Optional, **least privilege** ([PERMISSIONS.md](PERMISSIONS.md), "Auth strategy"):

| Key | What to set |
|---|---|
| `iam.auth_strategy` | `standard` (default: Google's predefined roles) or `minimum` (the tested least-privilege roles for the deployer and the demo's service accounts, with project custom roles). `./agdemo up --auth-strategy minimum` does the same for one run; switching an existing environment needs `--allow-role-changes` once |

Optional, for **Private Networking mode** (a VPC-enabled gateway, README "Private Networking mode"):

| Key | What to set |
|---|---|
| `private_networking.enabled` | `true` to build the VPC side too |
| `private_networking.region` | A second supported region, **different** from `environment.region`: a project can have only one egress gateway per region serving agents, so the VPC-enabled gateway needs its own region. If you change it, set the same `region:` in `themes/privnet/theme.yaml` (and any other Private Networking theme), or `deploy-theme` stops with a region mismatch |

Authenticate (two logins, both with your Google account):

```bash
gcloud auth login && gcloud auth application-default login
```

- `gcloud auth login` lets the `gcloud` command line act as you (builds, deploys, IAM).
- `gcloud auth application-default login` creates **Application Default Credentials (ADC)**: a credentials file on your machine that libraries and tools use automatically. Terraform and the Python SDK use ADC.
- Neither is used by the deployed UI. It runs on Cloud Run as its own **service account** (`<prefix>-ui`) with only the roles it needs. People reach it through **IAP** (Identity-Aware Proxy, a Google sign-in gate in front of the app), limited to `ui.iap_access`.

## 4. Deploy

```bash
./agdemo preflight
```

Shows a pass/fail table (auth, billing, roles, APIs, region support, quotas, gateway conflicts) with the fix for each failure. You can let it enable missing APIs.

```bash
./agdemo up
```

Does everything below in one go (Terraform foundation, every `themes.enabled` theme, the UI, a verify; about 40–55 minutes) and is idempotent. Step by step instead:

```bash
./agdemo tf init && ./agdemo tf apply
```

Creates the shared resources with Terraform: APIs, service accounts, Artifact Registry, gateways, IAP and Model Armor authz extensions, and platform allowlist entries in the registry. **Expect about 10–20 minutes**, mostly gateway creation. (`./agdemo bootstrap` is deprecated and points here; `--legacy` runs the old path.)

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

> Agents are **not** added to Gemini Enterprise automatically, and ticking **GE Demo** doesn't register them. Run `./agdemo publish-ge <theme>` once per theme. The agents appear in the app as **"Helpdesk Agent (Agent Gateway demo)"** and **"Store Ops Agent (Agent Gateway demo)"** (your own themes: "&lt;orchestrator display name&gt; (Agent Gateway demo)").

GE Demo mode shows the agent-to-anywhere policies from the Gemini Enterprise UI: you chat with the theme's agent in Gemini Enterprise, while this UI shows and changes the policies.

1. In `config/demo.yaml`, set `gemini_enterprise.app_id` (the engine id of an existing Gemini Enterprise app), `location` (`global`, `us` or `eu`) and `app_url` (the app's web URL, linked from the UI).
2. Register each deployed theme's agent in that app (ADK registration through `agents-cli`; idempotent):

```bash
./agdemo publish-ge helpdesk
```

3. Run `./agdemo ui deploy` so the Cloud Run UI picks up the config.
4. In the UI, tick **GE Demo**. Clicking a test now copies its prompt to the clipboard instead of running it. Paste the prompt into Gemini Enterprise and pick the agent ("Helpdesk Agent (Agent Gateway demo)" or "Store Ops Agent (Agent Gateway demo)"). **Show what happened** probes the connections so the diagram reflects the current policy.

Notes:
- Gemini Enterprise calls the agent with the signed-in user's credentials, so presenters need `roles/aiplatform.user` on the project (`./agdemo up` grants it to every `ui.admin_access` member).
- Gemini Enterprise supports the egress gateway only. Use GE Demo for the egress scenarios (1–4 and 6) and detach the ingress gateway first (scenario 5, Model Armor on ingress, runs from the demo UI); whether Gemini Enterprise calls succeed with an ingress gateway attached hasn't been tested.

## 8. Adding themes and scenarios

Copy `themes/_template/` to `themes/<id>/`, edit the YAML (orchestrator, components, agents, tools, policies, scenarios), and validate it with `load_theme('<id>')`. Then add it to `themes.enabled` and run `./agdemo deploy-theme <id>`. No code changes are needed unless you add a new *kind* of policy. See `docs/ADDING_A_THEME.md`.

## 9. Costs and cleanup

Costs come from the gateways, the Agent Runtime agents (which run continuously), Cloud Run (scales to zero), Model Armor and Gemini calls.

```bash
./agdemo teardown helpdesk
```

```bash
./agdemo down
```

`down` resets every theme, unregisters the agents from Gemini Enterprise, deletes the engines, Cloud Run services, registry entries and the UI, checks that nothing else is left (`./agdemo check-clean --strict`), then runs `terraform destroy`. You type `<project>/<prefix>` to confirm. It does **not** turn off APIs, delete the state or recordings buckets, or touch resources it didn't create. (`./agdemo teardown --all` is the old non-Terraform path.)

## 10. Troubleshooting (quick list)

| Symptom | Usual cause |
|---|---|
| Every tool call fails, even allowed ones | `iap.egressor` binding not in effect yet, or a registry hostname variant (regional / `.mtls.`) isn't registered |
| Agent can't reach the model once the gateway is attached | Platform endpoints missing from the allowlist; re-run `./agdemo up` (or `./agdemo tf apply`) |
| Binding the gateway fails | A different gateway is already bound in this project and region, or the engine is older than 2026-04-29 or has revisions |
| Model Armor never blocks anything | Engine wasn't created with Agent Identity, or the Model Armor authz policy isn't attached |
| UI changes in Live mode don't show | Still taking effect. The line shows *pending*; use Live with fallback |

More in `docs/TROUBLESHOOTING.md`.
