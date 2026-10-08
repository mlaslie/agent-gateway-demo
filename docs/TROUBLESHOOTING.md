# Troubleshooting

Commands use values from `config/demo.yaml`. Below, `P` is the project, `R` the region and `PFX` the
resource prefix. `./agdemo status [theme]` shows the live state of everything listed here.

## Quick map

| Symptom | Usual cause | Fix |
|---|---|---|
| Every egress call returns 403, even allowed ones, right after a change | IAP policy changes take time to propagate (registrations take about 4 min, IAM bindings 1 to 7 min) | Wait, or use **Live with fallback** |
| Agent can't reach Gemini / sessions / logging once the egress gateway is attached | A platform hostname variant isn't registered or allowed | Find it in the gateway log (below), then add it to `platform_endpoints()` in `agdemo_core/infra/common.py` and re-run `./agdemo bootstrap` |
| Engine PATCH fails after ~13 min with "The Reasoning Engine failed to be updated" | The container boots behind a default-deny gateway and can't reach a platform endpoint | Same as above. Check `reasoning_engine_stderr` for the host |
| `gateway_attach` stays *pending* for many minutes | The PATCH redeploys the engine container (5 to 15 min) | Expected. Attach the gateways before presenting |
| Binding the gateway fails with `FAILED_PRECONDITION` | Another engine in the same project + region is bound to a different gateway, or the engine was created before 2026-04-29, or it has revisions or traffic split | `./agdemo preflight` lists conflicting bindings |
| A2A call to an allowed agent is denied | Agent Registry has no URL for the agent (A2A 1.0 cards keep the URL in `supportedInterfaces`) | See ARCHITECTURE.md §Registry. `deploy-theme` falls back to `no-spec` + `interfaces` |
| Tool-level policy blocks `initialize` / `tools/list` | The condition doesn't allow an empty tool name | Conditions must include `''`. The handlers do this already |
| Model Armor never blocks | No CONTENT_AUTHZ policy is attached (checkbox off), a service agent is missing Model Armor roles, or the engine wasn't created with `AGENT_IDENTITY` | `./agdemo status`, then re-run `bootstrap` (it grants the roles) |
| Ingress: denied caller still gets in | The ingress gateway isn't attached to the engine (`gw-ingress` off), or the IAP extension is in DRY_RUN | `gateways.iap_enforcement: ENFORCE`, then `bootstrap` |
| Ingress: allowed caller gets 403 | The caller isn't in the ingress IAP policy, or the policy hasn't propagated yet | Apply `ingress-allowed-caller` and wait |
| `GET /healthz` on Cloud Run returns Google's 404 page | Cloud Run reserves paths that end in `z` | Use another path (for example `/health`) |
| `deploy-theme` Cloud Run step fails with `allUsers` | Org policy (domain restricted sharing) | Set `cloud_run.public_targets: false`. The orchestrator then uses ID tokens and is granted `run.invoker` |

## Logs

What the gateway decided for each request. The log has one entry per governed connection, with the
hostname, the verdict and the registry entry it matched:

```bash
gcloud logging read 'logName:"networkservices.googleapis.com%2Fgateway_requests"' \
  --project=P --freshness=30m --limit=100 \
  --format='table(timestamp,jsonPayload.enforcedGatewaySecurityPolicy.hostname,jsonPayload.authzPolicyInfo.result,jsonPayload.agentGatewayInfo.agentRegistryResource)'
```

An entry with no `agentRegistryResource` means the host is **unregistered**. Register it, matching the
host exactly, including any `.mtls.` or `:443` variant.

Agent Runtime container output (startup failures, tool errors):

```bash
gcloud logging read 'logName:"projects/P/logs/aiplatform.googleapis.com%2Freasoning_engine_stderr"' --project=P --limit=50
```

Model Armor verdicts:

```bash
gcloud logging read 'logName:"projects/P/logs/modelarmor.googleapis.com%2Fsanitize_operations"' --project=P --limit=20
```

## Inspecting the pieces

```bash
# gateway binding of an engine (null = wide open)
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://R-aiplatform.googleapis.com/v1/projects/P/locations/R/reasoningEngines/ENGINE_ID" | jq .spec.deploymentSpec.agentGatewayConfig

# who may egress to a registry entry
gcloud iap web get-iam-policy --resource-type=agent-registry --mcp-server=REGISTRY_ID --region=R --project=P
gcloud iap web get-iam-policy --resource-type=agent-registry --agent=REGISTRY_ID --region=R --project=P
gcloud iap web get-iam-policy --resource-type=agent-registry --endpoint=REGISTRY_ID --region=R --project=P

# authz policies / extensions attached to the gateways
gcloud network-security authz-policies list --location=R --project=P
gcloud beta service-extensions authz-extensions list --location=R --project=P
```

## Reset and cleanup

- `./agdemo reset <theme>` removes every policy for that theme and detaches its gateways. Detaching runs as a background engine PATCH.
- `./agdemo teardown <theme>` deletes the engine (force), the Cloud Run services and the registry entries. It also removes the engine principal's project roles.
- `./agdemo teardown --all` also deletes the authz policies, the extensions, both gateways, the Model Armor template, the `PFX-plat-*` registry entries, the demo service accounts, the Artifact Registry repo and the staging bucket. It never deletes anything without the prefix, and it never disables APIs.
