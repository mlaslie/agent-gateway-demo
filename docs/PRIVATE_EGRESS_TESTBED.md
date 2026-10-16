# Private egress test bed: what we ran into, and how to reproduce it

**Status:** notes for later review (2026-10-16). Nothing in the demo is built yet.

This is the shortest working path from a live test run in a throwaway region (October 2026). The full record is in the Policy Studio repo:
- `docs/P0_FINDINGS.md`: every command, result, error code and timing;
- `docs/PRIVATE_NETWORKING_PLAN.md`: background and decisions.

Placeholders: `PROJECT_ID` / `PROJECT_NUMBER`, `REGION` (one region for everything), `VPC`, `SUBNET`, `ATT`, `PSCIP` (an unused private IP, e.g. `10.70.250.10`), `TMPL`, `GW` (the egress gateway), `SVC` (the Cloud Run service).

## What it shows

An Agent Runtime agent calls an **internal-only Cloud Run MCP server** through the **egress Agent Gateway**:
- **without** a grant, the call is denied with 403;
- **with** `roles/iap.egressor`, it succeeds;
- the gateway log's upstream `serverIp` is the private PSC IP (10.x), which proves the call took the private path;
- the same service called without the gateway gets a 404, because ingress is internal.

## What we ran into (the gotchas)

1. **Project number vs ID.**
   - The gateway's `agentConnectivityTemplate` reference must use the project **number**; with the ID you get 400.
   - Inside the template, `networkAttachment` and `targetNetwork` must use the project **ID**; a number fails validation.
2. **Existing gateways can't jump straight to a template.**
   - A PATCH from no template to a template fails ("internal error", code 13) and wipes the gateway's labels.
   - What works is two PATCHes:
     1. inline `networkConfig` (about 3.5 min);
     2. then `agentConnectivityTemplate,networkConfig` (about 3 min).
   - Always include `labels` in the mask.
   - Google's doc says to delete and recreate the gateway; that isn't necessary.
3. **Templates are frozen while in use.** Only `description` can change; to change anything else, create a new template and swap it (about 11 s). A template in use can't be deleted.
4. **The attachment must be in the gateway's region.** A different region fails at template create with an unhelpful "internal error".
5. **Agents behind the gateway may get 401 on every Google API call.** Fix: deploy them with `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false`. We didn't find the cause.
6. **Only one egress gateway per project and region can serve agents.** A second bind fails with "Another Agent Gateway is already active …".
7. **Never give an agent its own PSC interface as well.**
   - Creating an agent with both is rejected.
   - Adding a gateway to such an agent breaks its Google API access.
   - An agent with a PSC interface and no gateway reaches the VPC with no checks at all.
8. **Cloud Run internal ingress does accept PSC-endpoint traffic.** No load balancer is needed. Cloud Run's logs show the caller as `0.0.0.0`, so use the gateway log as proof.
9. **Register both run.app URL forms** (`SVC-PROJECT_NUMBER.REGION.run.app` and `SVC-hash-xx.a.run.app`). The registry matches URLs exactly.
10. **Timing:**
    - gateway create: about 3.5 min;
    - agent create: 4.5–7 min;
    - bind: about 5 min;
    - grant to first 200: about 2 min.
11. **The PSC endpoint name** allows letters and digits only: no hyphens, at most 20 characters.
12. **Ingress (dropped from scope):** private clients do reach the ingress gateway, but its Model Armor enforcement was inconsistent.

## Simplest way to reproduce

Everything is in one project and one region. Steps 1–6 are one-off network setup.

```bash
# 1. Subnet (/28, Private Google Access) and network attachment
gcloud compute networks create VPC --subnet-mode=custom
gcloud compute networks subnets create SUBNET --network=VPC --region=REGION \
  --range=10.70.0.0/28 --enable-private-ip-google-access
gcloud compute network-attachments create ATT --region=REGION --subnets=SUBNET \
  --connection-preference=ACCEPT_AUTOMATIC

# 2. PSC endpoint for Google APIs
gcloud compute addresses create pscip --global --purpose=PRIVATE_SERVICE_CONNECT \
  --addresses=PSCIP --network=VPC
gcloud compute forwarding-rules create pscapis --global --network=VPC \
  --address=pscip --target-google-apis-bundle=all-apis

# 3. Private DNS: run.app -> the PSC IP
gcloud dns managed-zones create run-app --dns-name=run.app. --visibility=private --networks=VPC
gcloud dns record-sets create run.app. --zone=run-app --type=A --ttl=300 --rrdatas=PSCIP
gcloud dns record-sets create '*.run.app.' --zone=run-app --type=A --ttl=300 --rrdatas=PSCIP

# 4. Gateway service agent roles
SA=serviceAccount:service-PROJECT_NUMBER@gcp-sa-agentgateway.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding PROJECT_ID --member=$SA --role=roles/compute.networkAdmin
gcloud projects add-iam-policy-binding PROJECT_ID --member=$SA --role=roles/dns.peer

# 5. Connectivity template (no console UI for this)
cat > tmpl.yaml <<'EOF'
accessPath: AGENT_TO_ANYWHERE
deploymentModel: CENTRALIZED
egressNetworkConfig:
  networkAttachment: projects/PROJECT_ID/regions/REGION/networkAttachments/ATT
  dnsPeeringConfig:
    domains: ["run.app."]
    targetNetwork: projects/PROJECT_ID/global/networks/VPC
  vpcEgress: PRIVATE_RANGES_ONLY
EOF
gcloud network-services agent-connectivity-templates import TMPL --source=tmpl.yaml --location=REGION

# 6. The private destination: any small MCP or HTTP server, ingress internal
gcloud run deploy SVC --source=. --region=REGION --ingress=internal --no-allow-unauthenticated
```

**7. Give the gateway private egress.**
- **New gateway:** create it with `agentConnectivityTemplate: projects/PROJECT_NUMBER/locations/REGION/agentConnectivityTemplates/TMPL`, using the gcloud `agent-gateways import` YAML form.
- **Existing gateway (e.g. `agdemo-egress`):** do the two-step PATCH from gotcha 2, at a time when no theme is bound. The exact bodies are in P0_FINDINGS §3. Policy Studio Audit (N3) also shows them.

**8. Agent.** Deploy or redeploy it with Agent Identity, bound to GW, with the env var from gotcha 5. The demo's deploy path needs that env var added.

**9. Registry.** Register SVC with both run.app URLs. Policy Studio does this from Inventory → Register… or from the Builder.

**10. Test.**
1. Call SVC from the agent: 403.
2. Grant `roles/iap.egressor` (Policy Studio Builder or the demo's grant toggle).
3. Wait about 2 min and call again: 200.
4. In Logs Explorer, query `networkservices.googleapis.com/gateway_requests` for GW. `httpRequest.serverIp` should be `PSCIP:443`.

**Cleanup (reverse order):**
1. Revoke the grant and delete the registry entry.
2. Switch the gateway back to no template: PATCH `agentConnectivityTemplate,networkConfig` with an empty body. Or delete the throwaway gateway.
3. Delete the template.
4. Wait about 2.5 min, then delete the attachment.
5. Delete the DNS zone records and the zone.
6. Delete the forwarding rule and the address.
7. Delete the subnet and the VPC.
8. Remove the two IAM roles.

**Cost while it exists:** about USD 8/month, almost all of it the PSC endpoint. Cloud Run idle costs nothing.

## Decided (2026-10-16)

- The test bed becomes a **Private Networking mode** in the demo, in **us-west1**, with a dedicated VPC-enabled gateway created with the template from the start. The live `agdemo-egress` gateway is **not** changed and no in-place migration is needed. See `TERRAFORM_PLAN.md` §7c.
- **Content:** Helpdesk-style orchestrator, **Payroll MCP** (private) plus one public MCP for side-by-side contrast.
- **Built by Terraform** (`up` / `down`), with the CLI deploying the code.

## Superseded review items

- Whether to build this into the demo as `./agdemo private up|status|down`, or as part of the Terraform foundation (see `docs/TERRAFORM_PLAN.md` once written).
- Whether to update `agdemo-egress` in place (option a, recommended) or use a separate region (option b).
- Which theme gets the private MCP server, and what it's called.
- Whether in-place gateway updates disrupt traffic for bound agents. This is untested, so do it after a Reset.
