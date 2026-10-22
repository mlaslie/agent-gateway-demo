// "Build it by hand" page data: every step of the build as curl / gcloud / Terraform / Python, the timelines,
// the mental-model strip and the static sections. Types in data.d.ts; checks in data.test.ts.
// Strings named what / why / note and the *Html() sections are trusted, hand-written HTML.
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
/* ============================================================== shared snippets */
const TOKEN = 'TOKEN="$(gcloud auth print-access-token)"';
const AUTH = '-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"';
const AGENT = 'AGENT="principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID"';
const ALL_AGENTS = 'ALL_AGENTS="principalSet://agents.global.org-ORG_ID.system.id.goog/attribute.platformContainer/aiplatform/projects/PROJECT_NUMBER"';
const PYREST = `import google.auth
from google.auth.transport.requests import AuthorizedSession

s = AuthorizedSession(google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])[0])
`;
const PYIAP = `from google.cloud import iap_v1  # pip install google-cloud-iap
from google.iam.v1 import iam_policy_pb2, options_pb2, policy_pb2
from google.type import expr_pb2

iap = iap_v1.IdentityAwareProxyAdminServiceClient()


def grant(resource, member, title=None, expression=None, role="roles/iap.egressor"):
    """Read-modify-write the IAP policy (version 3 keeps conditions)."""
    policy = iap.get_iam_policy(request=iam_policy_pb2.GetIamPolicyRequest(
        resource=resource, options=options_pb2.GetPolicyOptions(requested_policy_version=3)))
    b = policy_pb2.Binding(role=role, members=[member])
    if expression:
        b.condition.CopyFrom(expr_pb2.Expr(title=title or "", expression=expression))
    policy.bindings.append(b)
    policy.version = 3
    iap.set_iam_policy(request=iam_policy_pb2.SetIamPolicyRequest(resource=resource, policy=policy))
`;
const JQ_ADD = `| jq --arg m "$MEMBER" --arg t "$TITLE" --arg e "$EXPR" '{policy: (.version = 3
    | .bindings = ((.bindings // []) + [{role: "roles/iap.egressor", members: [$m]}
        + (if $e == "" then {} else {condition: {title: $t, expression: $e}} end)]))}' \\`;
function iapCurl(kind, idVar, member, title, expr) {
  return `${TOKEN}
${member}
RES="https://iap.googleapis.com/v1/projects/PROJECT_NUMBER/locations/REGION/iap_web/agentRegistry/${kind}/${idVar}"
MEMBER="$${member.split("=")[0]}" TITLE="${title || ""}" EXPR="${expr || ""}"

# Read-modify-write: get the policy (version 3 keeps conditions), add the binding, set it back.
curl -sS -X POST "\${RES}:getIamPolicy" ${AUTH} \\
  -d '{"options":{"requestedPolicyVersion":3}}' \\
${JQ_ADD}
| curl -sS -X POST "\${RES}:setIamPolicy" ${AUTH} -d @-`;
}
const LOOKUP = (svc, v) => `${v}=$(gcloud agent-registry services describe ${svc} --project=PROJECT_ID --location=REGION \\
  --format='value(registryResource)' | awk -F/ '{print $NF}')   # the entry id, not the service id`;
const PLATFORM_HOSTS = ["REGION-aiplatform.googleapis.com", "REGION-aiplatform.mtls.googleapis.com", "aiplatform.REGION.rep.googleapis.com",
  "aiplatform.googleapis.com", "aiplatform.mtls.googleapis.com", "agentregistry.googleapis.com", "agentregistry.mtls.googleapis.com",
  "logging.googleapis.com", "logging.mtls.googleapis.com", "telemetry.googleapis.com", "telemetry.mtls.googleapis.com",
  "cloudtrace.googleapis.com", "cloudtrace.mtls.googleapis.com", "monitoring.googleapis.com", "monitoring.mtls.googleapis.com",
  "cloudresourcemanager.googleapis.com", "cloudresourcemanager.mtls.googleapis.com", "iamcredentials.googleapis.com",
  "iamcredentials.mtls.googleapis.com", "secretmanager.googleapis.com", "secretmanager.mtls.googleapis.com",
  "cloudresourcemanager.mtls.googleapis.com:443", "logging.mtls.googleapis.com:443", "cloudtrace.mtls.googleapis.com:443",
  "telemetry.mtls.googleapis.com:443"];
const APIS = ["aiplatform.googleapis.com", "agentregistry.googleapis.com", "networkservices.googleapis.com", "networksecurity.googleapis.com",
  "iap.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com", "modelarmor.googleapis.com", "run.googleapis.com",
  "cloudbuild.googleapis.com", "artifactregistry.googleapis.com", "logging.googleapis.com", "monitoring.googleapis.com",
  "cloudtrace.googleapis.com", "telemetry.googleapis.com", "cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com",
  "storage.googleapis.com", "compute.googleapis.com", "dns.googleapis.com", "certificatemanager.googleapis.com"];
const READONLY_EXPR = "api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true || api.getAttribute('iap.googleapis.com/mcp.toolName', '') == ''";
const NAMES_EXPR = (tools) => `api.getAttribute('iap.googleapis.com/mcp.toolName', '') in [${tools.map(t => `'${t}'`).join(", ")}, '']`;

/* tab helper: code, optional note (HTML), unverified flag */
const T = (code, note, unv) => ({ code, note, unv: !!unv });
const NO = (note, code) => ({ code: code || "", note, warn: true });

/* ============================================================== steps */
export const S = {};

S.apis = {
  title: "Enable the APIs", sub: "Agent Platform, gateway, registry, IAP, Model Armor, Cloud Run…",
  who: ["tf", "Terraform foundation"], time: "1–2 min",
  what: "Each Google Cloud product is an API you switch on per project. This turns on the 21 the build uses (including Compute Engine and Cloud DNS, which the VPC pieces need).",
  why: "Nothing else can be created until its API is on. It's also safe to do far ahead of time: enabling costs nothing by itself.",
  tabs: {
    curl: T(`${TOKEN}
# batchEnable takes at most 20 services per call, so two calls. Each returns a long-running operation.
curl -sS -X POST ${AUTH} \\
  "https://serviceusage.googleapis.com/v1/projects/PROJECT_ID/services:batchEnable" \\
  -d '{"serviceIds": ${JSON.stringify(APIS.slice(0, 20))}}'
curl -sS -X POST ${AUTH} \\
  "https://serviceusage.googleapis.com/v1/projects/PROJECT_ID/services:batchEnable" \\
  -d '{"serviceIds": ${JSON.stringify(APIS.slice(20))}}'`),
    gcloud: T(`# The API takes at most 20 services per call and gcloud doesn't split the list, so two commands.
gcloud services enable \\
  ${APIS.slice(0, 20).join(" \\\n  ")} \\
  --project=PROJECT_ID
gcloud services enable ${APIS.slice(20).join(" ")} --project=PROJECT_ID`),
    tf: T(`locals {
  apis = ${JSON.stringify(APIS, null, 4).replace(/\n\]/, "\n  ]")}
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  project            = "PROJECT_ID"
  service            = each.value
  disable_on_destroy = false # other teams' resources in the project may need them
}`),
    py: T(`from google.cloud import service_usage_v1  # pip install google-cloud-service-usage

APIS = ${JSON.stringify(APIS, null, 4).replace(/\n\]/, "\n]")}
client = service_usage_v1.ServiceUsageClient()
for i in range(0, len(APIS), 20):            # at most 20 per call
    op = client.batch_enable_services(request={"parent": "projects/PROJECT_ID", "service_ids": APIS[i:i + 20]})
    op.result(timeout=900)`, "The demo enables APIs through REST and Terraform; this client call wasn't run.", true),
  },
};

S.sa = {
  title: "Service account, bucket, image repo", sub: "Identity for Cloud Run services; staging for agent code",
  who: ["tf", "Terraform foundation"], time: "< 1 min",
  what: "A <b>service account</b> is a non-human identity that a deployed service runs as; the Cloud Run destinations run as one. Agent Runtime needs a <b>Cloud Storage bucket</b> in the agent's region to stage the agent package, and Cloud Run needs an <b>Artifact Registry</b> repo for images.",
  why: "Later deploys reference all three, so they come first. They don't depend on the gateway at all.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} "https://iam.googleapis.com/v1/projects/PROJECT_ID/serviceAccounts" \\
  -d '{"accountId": "targets-sa", "serviceAccount": {"displayName": "Cloud Run destinations"}}'
curl -sS -X POST ${AUTH} "https://storage.googleapis.com/storage/v1/b?project=PROJECT_ID" \\
  -d '{"name": "PROJECT_ID-staging", "location": "REGION", "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": true}}}'
curl -sS -X POST ${AUTH} \\
  "https://artifactregistry.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/repositories?repositoryId=demo" \\
  -d '{"format": "DOCKER"}'
# Project roles for the service account: getIamPolicy -> add bindings -> setIamPolicy
# (the gcloud tab's add-iam-policy-binding does that read-modify-write for you).`, null, true),
    gcloud: T(`gcloud iam service-accounts create targets-sa --project=PROJECT_ID --display-name="Cloud Run destinations"
for ROLE in roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding PROJECT_ID --condition=None \\
    --member="serviceAccount:targets-sa@PROJECT_ID.iam.gserviceaccount.com" --role="$ROLE"
done

# Staging bucket for Agent Runtime packages (same region as the agent)
gcloud storage buckets create gs://PROJECT_ID-staging --project=PROJECT_ID --location=REGION \\
  --uniform-bucket-level-access

# Docker repository for the Cloud Run images
gcloud artifacts repositories create demo --project=PROJECT_ID --location=REGION --repository-format=docker`),
    tf: T(`resource "google_service_account" "targets" {
  project      = "PROJECT_ID"
  account_id   = "targets-sa"
  display_name = "Cloud Run destinations"
}

resource "google_project_iam_member" "targets" {
  for_each = toset(["roles/aiplatform.user", "roles/logging.logWriter"])
  project  = "PROJECT_ID"
  role     = each.value
  member   = google_service_account.targets.member # additive _member, never _policy
}

resource "google_storage_bucket" "staging" {
  project                     = "PROJECT_ID"
  name                        = "PROJECT_ID-staging"
  location                    = "REGION"
  uniform_bucket_level_access = true
}

resource "google_artifact_registry_repository" "repo" {
  project       = "PROJECT_ID"
  location      = "REGION"
  repository_id = "demo"
  format        = "DOCKER"
}`),
    py: T(`from google.cloud import iam_admin_v1, storage  # pip install google-cloud-iam google-cloud-storage

iam = iam_admin_v1.IAMClient()
iam.create_service_account(request={
    "name": "projects/PROJECT_ID",
    "account_id": "targets-sa",
    "service_account": {"display_name": "Cloud Run destinations"},
})

bucket = storage.Client(project="PROJECT_ID").bucket("PROJECT_ID-staging")
bucket.iam_configuration.uniform_bucket_level_access_enabled = True
bucket.create(location="REGION")
# Project roles and the Artifact Registry repo: most teams use gcloud or Terraform for these.`, "There's no single client for this step. Not run in this demo.", true),
  },
};

const egwBody = (tmpl) => `{
    "googleManaged": {"governedAccessPath": "AGENT_TO_ANYWHERE"},
    "registries": ["//agentregistry.googleapis.com/projects/PROJECT_ID/locations/REGION"],${tmpl ? `
    "agentConnectivityTemplate": "projects/PROJECT_NUMBER/locations/REGION/agentConnectivityTemplates/TMPL",` : ""}
    "labels": {"app": "agent-gateway-demo"}
  }`;
S.egw = {
  title: "Create the egress gateway", sub: "AGENT_TO_ANYWHERE, linked to the region's registry",
  who: ["tf", "Terraform foundation"], time: "~2.5–4 min",
  what: "The <b>checkpoint</b> every outbound call from a bound agent goes through. It's linked to the region's <b>Agent Registry</b>, its address book of known destinations. On its own it doesn't block anything yet.",
  why: "Agents bind to it later, so it must exist first. Only <b>one egress gateway per project and region</b> can serve agents, and every agent in that region shares it: choose the region now. Whether you register destinations before or after this doesn't matter; both must exist before an agent binds.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentGateways?agentGatewayId=EGRESS_GW" \\
  -d '${egwBody(false)}'
# Long-running operation: about 2.5-4 minutes.`),
    gcloud: T(`cat > egress.yaml <<'EOF'
name: EGRESS_GW
protocols: [MCP]   # required by gcloud's import schema
googleManaged:
  governedAccessPath: AGENT_TO_ANYWHERE
registries:
  - //agentregistry.googleapis.com/projects/PROJECT_ID/locations/REGION
EOF
gcloud network-services agent-gateways import EGRESS_GW --source=egress.yaml \\
  --location=REGION --project=PROJECT_ID`, "gcloud has no flag-based create: YAML <code>import</code> only. The console can also do it (Agent Gateway → Add gateway → Agent-to-Anywhere)."),
    tf: T(`resource "google_network_services_agent_gateway" "egress" {
  project    = "PROJECT_ID"
  location   = "REGION"
  name       = "EGRESS_GW"
  registries = ["//agentregistry.googleapis.com/projects/PROJECT_ID/locations/REGION"]

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  lifecycle {
    ignore_changes = [protocols] # deprecated since google 7.37.0; leave unset
  }
}`),
    py: T(`from google.cloud import network_services_v1  # pip install google-cloud-network-services

client = network_services_v1.NetworkServicesClient()
gw = network_services_v1.AgentGateway(
    google_managed=network_services_v1.AgentGateway.GoogleManaged(governed_access_path="AGENT_TO_ANYWHERE"),
    registries=["//agentregistry.googleapis.com/projects/PROJECT_ID/locations/REGION"],
)
op = client.create_agent_gateway(parent="projects/PROJECT_ID/locations/REGION",
                                 agent_gateway_id="EGRESS_GW", agent_gateway=gw)
print(op.result(timeout=1800).name)`, "The client class exists (checked); the demo itself sends the REST body.", true),
  },
};

S.iapext = {
  title: "Add the IAP decision-maker", sub: "IAP authz extension; enforce or dry run lives here",
  who: ["tf", "Terraform foundation"], time: "seconds",
  what: "A plug-in (a Service Extensions <b>authz extension</b>) that makes the gateway ask <b>IAP</b>, Google's access check, \"does this agent hold <code>roles/iap.egressor</code> on this destination?\". <code>iapPolicyVersion: V1</code> means plain IAM allow policies on registry entries.",
  why: "The gateway has no on/off switch of its own: <b>enforce vs dry run</b> is set here (<code>failOpen</code> + <code>iamEnforcementMode</code>), so it comes before the policy that points at it. Start in dry run if you want to bind agents before everything is registered. A flip takes about 2–3 minutes, and changes every gateway that shares this extension.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://networkservices.googleapis.com/v1beta1/projects/PROJECT_ID/locations/REGION/authzExtensions?authzExtensionId=EGRESS_GW-iap-authz" \\
  -d '{
    "service": "iap.googleapis.com",
    "failOpen": false,
    "timeout": "1s",
    "metadata": {"iapPolicyVersion": "V1"}
  }'
# Dry run instead: "failOpen": true and "metadata": {"iapPolicyVersion": "V1", "iamEnforcementMode": "DRY_RUN"}`),
    gcloud: T(`cat > iap-ext.yaml <<'EOF'
name: EGRESS_GW-iap-authz
service: iap.googleapis.com
failOpen: false          # dry run: true, plus metadata.iamEnforcementMode: DRY_RUN
timeout: 1s
metadata:
  iapPolicyVersion: "V1"
EOF
gcloud service-extensions authz-extensions import EGRESS_GW-iap-authz --source=iap-ext.yaml \\
  --location=REGION --project=PROJECT_ID`, "The current console wizard recommends <b>Unified Access Policy</b> (IAP V2). This demo uses V1 (IAM allow policies on registry entries), which is what's verified here."),
    tf: T(`resource "google_network_services_authz_extension" "egress_iap" {
  project   = "PROJECT_ID"
  location  = "REGION"
  name      = "EGRESS_GW-iap-authz"
  service   = "iap.googleapis.com"
  fail_open = false # dry run: true
  timeout   = "1s"
  metadata  = { iapPolicyVersion = "V1" } # dry run: add iamEnforcementMode = "DRY_RUN"

  lifecycle {
    ignore_changes = [fail_open, metadata] # if someone flips enforce/dry run outside Terraform
  }
}`),
    py: T(`import json
from google.cloud import network_services_v1  # pip install google-cloud-network-services

dep = network_services_v1.DepServiceClient()
parent = "projects/PROJECT_ID/locations/REGION"
ext = network_services_v1.AuthzExtension.from_json(json.dumps({
    "name": f"{parent}/authzExtensions/EGRESS_GW-iap-authz",
    "service": "iap.googleapis.com", "failOpen": False, "timeout": "1s",
    "metadata": {"iapPolicyVersion": "V1"},
}))
dep.create_authz_extension(parent=parent, authz_extension_id="EGRESS_GW-iap-authz",
                           authz_extension=ext).result(timeout=900)`, null, true),
  },
};

const polBody = (gw, profile, ext) => `{
    "target": {"resources": ["projects/PROJECT_ID/locations/REGION/agentGateways/${gw}"]},
    "policyProfile": "${profile}",
    "action": "CUSTOM",
    "customProvider": {"authzExtension": {"resources": ["projects/PROJECT_ID/locations/REGION/authzExtensions/${ext}"]}}
  }`;
S.iappol = {
  title: "Turn on default deny", sub: "REQUEST_AUTHZ policy: gateway → IAP extension",
  who: ["tf", "Terraform foundation"], time: "~3 min",
  what: "An <b>authz policy</b> that attaches the IAP extension to the gateway. From now on every request through the gateway is checked: <b>no registry entry and no grant, no entry</b>.",
  why: "It needs both the gateway and the extension. It must be in place before an agent binds, otherwise the agent's traffic passes the gateway unchecked.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://networksecurity.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/authzPolicies?authzPolicyId=EGRESS_GW-iap-policy" \\
  -d '${polBody("EGRESS_GW", "REQUEST_AUTHZ", "EGRESS_GW-iap-authz")}'
# Long-running operation: about 3 minutes.`),
    gcloud: T(`cat > iap-policy.yaml <<'EOF'
name: EGRESS_GW-iap-policy
target:
  resources: [projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW]
policyProfile: REQUEST_AUTHZ
action: CUSTOM
customProvider:
  authzExtension:
    resources: [projects/PROJECT_ID/locations/REGION/authzExtensions/EGRESS_GW-iap-authz]
EOF
gcloud beta network-security authz-policies import EGRESS_GW-iap-policy --source=iap-policy.yaml \\
  --location=REGION --project=PROJECT_ID`),
    tf: T(`resource "google_network_security_authz_policy" "egress_iap" {
  project        = "PROJECT_ID"
  location       = "REGION"
  name           = "EGRESS_GW-iap-policy"
  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"

  target {
    resources = [google_network_services_agent_gateway.egress.id]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.egress_iap.id]
    }
  }
}`),
    py: T(`from google.cloud import network_security_v1  # pip install google-cloud-network-security

ns = network_security_v1.NetworkSecurityClient()
P = network_security_v1.AuthzPolicy
policy = P(
    target=P.Target(resources=["projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW"]),
    policy_profile=P.PolicyProfile.REQUEST_AUTHZ,
    action=P.AuthzAction.CUSTOM,
    custom_provider=P.CustomProvider(authz_extension=P.CustomProvider.AuthzExtension(
        resources=["projects/PROJECT_ID/locations/REGION/authzExtensions/EGRESS_GW-iap-authz"])),
)
ns.create_authz_policy(parent="projects/PROJECT_ID/locations/REGION", authz_policy=policy,
                       authz_policy_id="EGRESS_GW-iap-policy").result(timeout=900)`, null, true),
  },
};

S.plat = {
  title: "Register the Google APIs agents need", sub: "25 platform hostnames: Gemini, sessions, logs, traces…",
  who: ["tf", "Terraform foundation"], time: "~1 min",
  what: "Registry entries (\"endpoints\") for the Google APIs an Agent Runtime agent calls just to run: Gemini and sessions, logging, tracing, monitoring, IAM credentials, Secret Manager, the registry itself. Matching is by <b>exact hostname</b>, so plain, <code>.mtls</code>, regional, <code>.rep</code> and gRPC <code>:443</code> forms are separate entries.",
  why: "Under default deny the agent's own platform calls are blocked too. If these aren't registered and granted before the agent binds, every call fails (<b>498 / 403</b>). They belong with the gateway, not with your destinations.",
  tabs: {
    curl: T(`${TOKEN}
HOSTS=(${PLATFORM_HOSTS.join(" ")})
for H in "\${HOSTS[@]}"; do   # an array: works in zsh and bash
  ID="plat-$(echo "$H" | tr '.:' '--' | cut -c1-58)"
  curl -sS -X POST ${AUTH} \\
    "https://agentregistry.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/services?serviceId=$ID" \\
    -d "{\\"displayName\\": \\"Platform $H\\", \\"endpointSpec\\": {\\"type\\": \\"NO_SPEC\\"},
         \\"interfaces\\": [{\\"url\\": \\"https://$H\\", \\"protocolBinding\\": \\"JSONRPC\\"}]}"
done
# Each create is a long-running operation. The entry's registryResource (used by the grant) appears shortly after.`),
    gcloud: T(`HOSTS=(${PLATFORM_HOSTS.join(" ")})
for H in "\${HOSTS[@]}"; do   # an array: works in zsh and bash
  ID="plat-$(echo "$H" | tr '.:' '--' | cut -c1-58)"
  gcloud agent-registry services create "$ID" --project=PROJECT_ID --location=REGION \\
    --display-name="Platform $H" --endpoint-spec-type=no-spec \\
    --interfaces="protocolBinding=jsonrpc,url=https://$H"
done`),
    tf: T(`locals {
  platform_hosts = [
    ${PLATFORM_HOSTS.map(h => `"${h}"`).join(",\n    ")},
  ]
}

resource "google_agent_registry_service" "platform" {
  for_each     = toset(local.platform_hosts)
  project      = "PROJECT_ID"
  location     = "REGION"
  service_id   = substr("plat-\${replace(replace(each.value, ".", "-"), ":", "-")}", 0, 63)
  display_name = "Platform \${each.value}"

  endpoint_spec {
    type = "NO_SPEC"
  }

  interfaces {
    url              = "https://\${each.value}"
    protocol_binding = "JSONRPC"
  }
}`),
    py: T(PYREST + `
HOSTS = ${JSON.stringify(PLATFORM_HOSTS, null, 4).replace(/\n\]/, "\n]")}
base = "https://agentregistry.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/services"
for h in HOSTS:
    sid = ("plat-" + h.replace(".", "-").replace(":", "-"))[:63]
    r = s.post(base, params={"serviceId": sid}, json={
        "displayName": f"Platform {h}", "endpointSpec": {"type": "NO_SPEC"},
        "interfaces": [{"url": f"https://{h}", "protocolBinding": "JSONRPC"}]})
    r.raise_for_status()   # a long-running operation; poll r.json()["name"] if you need to wait`, "Same REST calls as the demo, through an authorized session (there's also a pre-1.0 google-cloud-agentregistry client)."),
  },
};

S.platgrant = {
  title: "Let every agent reach them", sub: "roles/iap.egressor for all agents in the project",
  who: ["tf", "Terraform foundation"], time: "~2 min to take effect",
  what: "One grant per platform entry, to the <b>principal set</b> \"every Agent Identity agent in this project\". This is the <b>foundation policy</b>: agents can boot, think and log, and reach nothing else.",
  why: "Grants attach to registry entries, so this follows the previous step. It must exist before the first bind. Grants take about 2 minutes to take effect.",
  tabs: {
    curl: T(iapCurl("endpoints", "ENTRY_ID", ALL_AGENTS) + `
# Repeat for each platform entry. ENTRY_ID is the last segment of the service's registryResource
# (projects/PROJECT_NUMBER/locations/REGION/endpoints/ENTRY_ID), not the service id.`),
    gcloud: T(`# ORG_ID: gcloud projects get-ancestors PROJECT_ID (the "organization" row)
${ALL_AGENTS}
for SVC in $(gcloud agent-registry services list --project=PROJECT_ID --location=REGION \\
             --format='value(name)' | awk -F/ '{print $NF}' | grep '^plat-'); do
  ${LOOKUP("$SVC", "ENTRY_ID").replace(/\n/g, "\n  ")}
  gcloud iap web add-iam-policy-binding --project=PROJECT_ID --resource-type=agent-registry --region=REGION \\
    --endpoint="$ENTRY_ID" --member="$ALL_AGENTS" --role=roles/iap.egressor
done`, "The grant command is the verified part; the <code>services list</code> loop around it is a convenience."),
    tf: T(`resource "google_iap_agent_registry_endpoint_iam_member" "platform" {
  for_each    = google_agent_registry_service.platform
  project     = "PROJECT_NUMBER" # the demo's module passes the project number here
  location    = "REGION"
  endpoint_id = basename(each.value.registry_resource)
  role        = "roles/iap.egressor"
  member      = "principalSet://agents.global.org-ORG_ID.system.id.goog/attribute.platformContainer/aiplatform/projects/PROJECT_NUMBER"
}`),
    py: T(PYIAP + `
ALL_AGENTS = "principalSet://agents.global.org-ORG_ID.system.id.goog/attribute.platformContainer/aiplatform/projects/PROJECT_NUMBER"
for entry_id in ["ENTRY_ID_1", "ENTRY_ID_2"]:   # one per platform entry
    grant(f"projects/PROJECT_NUMBER/locations/REGION/iap_web/agentRegistry/endpoints/{entry_id}", ALL_AGENTS)`, null, true),
  },
};

S.igw = {
  title: "Create the ingress gateway", sub: "CLIENT_TO_AGENT: screens calls into the agent",
  who: ["tf", "Terraform foundation"], time: "seconds",
  what: "A second checkpoint, in front of the agent's <code>query</code> / <code>streamQuery</code>. It applies <b>Model Armor only</b>: it does not check who the caller is. Who may call the agent is IAM on Agent Runtime (<code>roles/aiplatform.user</code>).",
  why: "Independent of egress, and optional. Creating it now means attaching it later is just one PATCH. No IAP policy is needed: CLIENT_TO_AGENT doesn't evaluate REQUEST_AUTHZ.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentGateways?agentGatewayId=INGRESS_GW" \\
  -d '{"googleManaged": {"governedAccessPath": "CLIENT_TO_AGENT"}}'`),
    gcloud: T(`cat > ingress.yaml <<'EOF'
name: INGRESS_GW
protocols: [MCP]   # required by gcloud's import schema
googleManaged:
  governedAccessPath: CLIENT_TO_AGENT
EOF
gcloud network-services agent-gateways import INGRESS_GW --source=ingress.yaml \\
  --location=REGION --project=PROJECT_ID`),
    tf: T(`resource "google_network_services_agent_gateway" "ingress" {
  project  = "PROJECT_ID" # must be the agent's project and region
  location = "REGION"
  name     = "INGRESS_GW"

  google_managed {
    governed_access_path = "CLIENT_TO_AGENT"
  }
}`),
    py: T(`from google.cloud import network_services_v1

client = network_services_v1.NetworkServicesClient()
gw = network_services_v1.AgentGateway(
    google_managed=network_services_v1.AgentGateway.GoogleManaged(governed_access_path="CLIENT_TO_AGENT"))
client.create_agent_gateway(parent="projects/PROJECT_ID/locations/REGION",
                            agent_gateway_id="INGRESS_GW", agent_gateway=gw).result(timeout=900)`, null, true),
  },
};

S.deploy = {
  title: "Deploy the destinations", sub: "A2A agents and MCP servers on Cloud Run",
  who: ["cli", "deploy-theme"], time: "~1–2 min each",
  what: "The things your agent will call: remote ADK agents served over <b>A2A</b> and <b>MCP</b> servers (Streamable HTTP). In the demo they're mocks on Cloud Run, running as the service account from step 2.",
  why: "Registration needs a running service: an A2A entry is built from the agent card the service serves, and every URL you'll register comes from the deploy. Each Cloud Run service has <b>two URLs</b>; note both.",
  tabs: {
    curl: T(`${TOKEN}
# Deploying from source is Cloud Build + Cloud Run; with an image that's already built:
curl -sS -X POST ${AUTH} \\
  "https://run.googleapis.com/v2/projects/PROJECT_ID/locations/REGION/services?serviceId=tickets-mcp" \\
  -d '{"template": {"serviceAccount": "targets-sa@PROJECT_ID.iam.gserviceaccount.com",
                    "containers": [{"image": "REGION-docker.pkg.dev/PROJECT_ID/demo/mcp-server:latest"}]}}'`, null, true),
    gcloud: T(`# An ADK agent exposed over A2A (to_a2a()) and a FastMCP server, each from source
# --allow-unauthenticated is the demo's default (fake data; the gateway enforces). With
# --no-allow-unauthenticated, grant the agent roles/run.invoker on each service (see "project roles").
gcloud run deploy kb-agent --source=./a2a_agent --region=REGION --project=PROJECT_ID \\
  --service-account=targets-sa@PROJECT_ID.iam.gserviceaccount.com --allow-unauthenticated --quiet
gcloud run deploy tickets-mcp --source=./mcp_server --region=REGION --project=PROJECT_ID \\
  --service-account=targets-sa@PROJECT_ID.iam.gserviceaccount.com --allow-unauthenticated --quiet
# Each service answers on two URL forms; register both later:
#   https://tickets-mcp-PROJECT_NUMBER.REGION.run.app
#   https://tickets-mcp-HASH-xx.a.run.app`),
    tf: T(`resource "google_cloud_run_v2_service" "tickets_mcp" {
  project  = "PROJECT_ID"
  location = "REGION"
  name     = "tickets-mcp"
  deletion_protection = false # default true would block terraform destroy

  template {
    service_account = "targets-sa@PROJECT_ID.iam.gserviceaccount.com"
    containers {
      image = "REGION-docker.pkg.dev/PROJECT_ID/demo/mcp-server:latest"
    }
  }
}`, "Supported by Terraform, but the demo keeps Cloud Run in the CLI: images are built from code and the URLs feed the registry.", true),
    py: T(`from google.cloud import run_v2  # pip install google-cloud-run

client = run_v2.ServicesClient()
svc = run_v2.Service(template=run_v2.RevisionTemplate(
    service_account="targets-sa@PROJECT_ID.iam.gserviceaccount.com",
    containers=[run_v2.Container(image="REGION-docker.pkg.dev/PROJECT_ID/demo/mcp-server:latest")]))
op = client.create_service(parent="projects/PROJECT_ID/locations/REGION", service=svc, service_id="tickets-mcp")
print(op.result().urls)`, null, true),
  },
};

const TOOLS = `TOOLS='{"tools": [
  {"name": "list_tickets", "description": "List tickets", "inputSchema": {"type": "object"},
   "annotations": {"readOnlyHint": true}},
  {"name": "delete_ticket", "description": "Delete a ticket", "inputSchema": {"type": "object"},
   "annotations": {"readOnlyHint": false, "destructiveHint": true}}
]}'`;
S.reg = {
  title: "Register the destinations", sub: "A2A card, MCP tool spec, API endpoint",
  who: ["cli", "deploy-theme"], time: "4–15 min until the gateway sees it",
  what: "Puts each destination in the <b>address book</b>. The gateway matches the request's host to an entry, so <b>register every hostname form</b> (both run.app URLs). For MCP, register the tool list with <code>readOnlyHint</code>: that's what makes a \"read-only tools\" policy possible. A2A agents are registered from their agent card.",
  why: "Grants attach to registry entries, so entries come before grants. The agent also discovers what it can call from here. Agent Runtime agents register themselves automatically when created.",
  tabs: {
    curl: T(`${TOKEN}
BASE="https://agentregistry.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/services"

# A2A agent: from its agent card (interfaces must be empty; the URL is in the card)
CARD="$(curl -s https://KB_AGENT_URL/.well-known/agent-card.json)"
curl -sS -X POST ${AUTH} "$BASE?serviceId=kb-agent" \\
  -d "{\\"displayName\\": \\"KB Agent\\", \\"agentSpec\\": {\\"type\\": \\"A2A_AGENT_CARD\\", \\"content\\": $CARD}}"

# MCP server: tools/list output (with annotations) and every URL form
${TOOLS}
curl -sS -X POST ${AUTH} "$BASE?serviceId=tickets-mcp" -d "{
  \\"displayName\\": \\"Tickets MCP\\",
  \\"mcpServerSpec\\": {\\"type\\": \\"TOOL_SPEC\\", \\"content\\": $TOOLS},
  \\"interfaces\\": [
    {\\"url\\": \\"https://tickets-mcp-PROJECT_NUMBER.REGION.run.app/mcp\\", \\"protocolBinding\\": \\"JSONRPC\\"},
    {\\"url\\": \\"https://tickets-mcp-HASH-xx.a.run.app/mcp\\", \\"protocolBinding\\": \\"JSONRPC\\"}]}"

# Plain HTTPS API
curl -sS -X POST ${AUTH} "$BASE?serviceId=weather-api" \\
  -d '{"displayName": "Weather API", "endpointSpec": {"type": "NO_SPEC"},
       "interfaces": [{"url": "https://api.example.com", "protocolBinding": "JSONRPC"}]}'`),
    gcloud: T(`# A2A agent, from its agent card
gcloud agent-registry services create kb-agent --project=PROJECT_ID --location=REGION \\
  --display-name="KB Agent" --agent-spec-type=a2a-agent-card \\
  --agent-spec-content="$(curl -s https://KB_AGENT_URL/.well-known/agent-card.json)"

# MCP server, with its tool spec and both URL forms
${TOOLS}
gcloud agent-registry services create tickets-mcp --project=PROJECT_ID --location=REGION \\
  --display-name="Tickets MCP" --mcp-server-spec-type=tool-spec --mcp-server-spec-content="$TOOLS" \\
  --interfaces="protocolBinding=jsonrpc,url=https://tickets-mcp-PROJECT_NUMBER.REGION.run.app/mcp" \\
  --interfaces="protocolBinding=jsonrpc,url=https://tickets-mcp-HASH-xx.a.run.app/mcp"

# Plain HTTPS API
gcloud agent-registry services create weather-api --project=PROJECT_ID --location=REGION \\
  --display-name="Weather API" --endpoint-spec-type=no-spec \\
  --interfaces="protocolBinding=http-json,url=https://api.example.com"

# The id that grants use:
gcloud agent-registry services describe tickets-mcp --project=PROJECT_ID --location=REGION \\
  --format='value(registryResource)'   # projects/PROJECT_NUMBER/locations/REGION/mcpServers/ENTRY_ID`),
    tf: T(`resource "google_agent_registry_service" "tickets_mcp" {
  project      = "PROJECT_ID"
  location     = "REGION"
  service_id   = "tickets-mcp"
  display_name = "Tickets MCP"

  mcp_server_spec {
    type    = "TOOL_SPEC"
    content = jsonencode({ tools = [
      { name = "list_tickets", description = "List tickets", inputSchema = { type = "object" },
        annotations = { readOnlyHint = true } },
      { name = "delete_ticket", description = "Delete a ticket", inputSchema = { type = "object" },
        annotations = { readOnlyHint = false, destructiveHint = true } },
    ] })
  }

  interfaces {
    url              = "https://tickets-mcp-PROJECT_NUMBER.REGION.run.app/mcp"
    protocol_binding = "JSONRPC"
  }
  interfaces {
    url              = "https://tickets-mcp-HASH-xx.a.run.app/mcp"
    protocol_binding = "JSONRPC"
  }
}

resource "google_agent_registry_service" "kb_agent" {
  project      = "PROJECT_ID"
  location     = "REGION"
  service_id   = "kb-agent"
  display_name = "KB Agent"

  agent_spec {
    type    = "A2A_AGENT_CARD"
    content = file("kb-agent-card.json") # fetched from /.well-known/agent-card.json
  }
}
# registry_resource (computed) feeds the IAP grant resources.`, "The demo creates theme entries with the CLI (they depend on deployed code). The resource and its fields come from the provider schema; the demo's Terraform uses it only for platform endpoints.", true),
    py: T(PYREST + `
base = "https://agentregistry.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/services"
import requests
card = requests.get("https://KB_AGENT_URL/.well-known/agent-card.json", timeout=30).json()  # public; no Google token to a third-party host
s.post(base, params={"serviceId": "kb-agent"}, json={
    "displayName": "KB Agent", "agentSpec": {"type": "A2A_AGENT_CARD", "content": card}}).raise_for_status()

tools = [
    {"name": "list_tickets", "description": "List tickets", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "delete_ticket", "description": "Delete a ticket", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": False, "destructiveHint": True}},
]
s.post(base, params={"serviceId": "tickets-mcp"}, json={
    "displayName": "Tickets MCP",
    "mcpServerSpec": {"type": "TOOL_SPEC", "content": {"tools": tools}},
    "interfaces": [{"url": u, "protocolBinding": "JSONRPC"} for u in (
        "https://tickets-mcp-PROJECT_NUMBER.REGION.run.app/mcp",
        "https://tickets-mcp-HASH-xx.a.run.app/mcp")],
}).raise_for_status()
# Then poll GET {base}/tickets-mcp until "registryResource" is filled: that's what grants use.`),
  },
};

const agentPy = (pn) => `import vertexai
from vertexai.agent_engines import AdkApp
from my_agent.agent import root_agent      # your ADK agent

vertexai.init(project="PROJECT_ID", location="REGION", staging_bucket="gs://PROJECT_ID-staging")
client = vertexai.Client(project="PROJECT_ID", location="REGION")
remote = client.agent_engines.create(
    agent=AdkApp(agent=root_agent),
    config={
        "display_name": "${pn ? "payroll-agent" : "helpdesk-agent"}",
        "identity_type": "AGENT_IDENTITY",  # fixed at create time: can't be added later
        "staging_bucket": "gs://PROJECT_ID-staging",
        "requirements": ["google-cloud-aiplatform[agent_engines,adk]", "google-adk"],
        "extra_packages": ["my_agent"],
        "env_vars": {
            # Needed behind a gateway, with or without a connectivity template (else 401 on Google APIs)
            "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": "false",
        },${pn ? `
        # Never also set psc_interface_config: a PSC interface plus a gateway breaks Google API access,
        # and a PSC interface without one is an ungoverned side door into the VPC.` : `
        # Optional: bind at create time instead of the later "Bind" step:
        # "agent_gateway_config": {"agent_to_anywhere_config": {
        #     "agent_gateway": "projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW"}},`}
    },
)
print(remote.api_resource.name)   # projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID`;
const agentTabs = (pn) => ({
  curl: T(`${TOKEN}
# Creating needs a packaged agent, which the Python SDK builds and uploads (see the Python tab).
# REST is for reading it back: the agent's own principal is spec.effectiveIdentity.
curl -sS ${AUTH} \\
  "https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/reasoningEngines/ENGINE_ID" \\
  | jq '{identityType: .spec.identityType, principal: ("principal://" + .spec.effectiveIdentity), env: .spec.deploymentSpec.env}'`, "Create with the Python SDK; REST shown for reading the identity."),
  gcloud: NO(`No gcloud command manages Agent Runtime agents. Use the Python SDK. <span class="bb-unv">unverified</span> The Agent Platform CLI (<code>agents-cli deploy</code>) can also deploy, and has <code>--agent-gateway-egress</code> to bind as part of a deploy.`,
    `# Read it back with a gcloud token:
curl -sS -H "Authorization: Bearer $(gcloud auth print-access-token)" \\
  "https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/reasoningEngines/ENGINE_ID" \\
  | jq -r '.spec.effectiveIdentity'`),
  tf: T(`resource "google_vertex_ai_reasoning_engine" "agent" {
  project      = "PROJECT_ID"
  region       = "REGION"
  display_name = "${pn ? "payroll-agent" : "helpdesk-agent"}"

  spec {
    identity_type = "AGENT_IDENTITY"
    container_spec {
      image_uri = "REGION-docker.pkg.dev/PROJECT_ID/demo/agent:latest"
    }
    deployment_spec {
      env {
        name  = "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES"
        value = "false"
      }
      # agent_gateway_config { agent_to_anywhere_config { agent_gateway = "projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW" } }
    }
  }

  lifecycle {
    # Mandatory if anything else (the demo UI, a PATCH) binds or unbinds it:
    ignore_changes = [spec[0].deployment_spec[0].agent_gateway_config]
  }
}`, "The demo keeps agents out of Terraform: any spec change redeploys the whole engine. A custom container (BYOC) also needs the gateway's root CA baked in, per the docs.", true),
  py: T(agentPy(pn), "This is how the demo creates its agents (vertexai 2.4.0). Newer docs show the same config in the separate <code>agentplatform</code> SDK (<code>google-cloud-agentplatform</code>) as <code>client.runtimes.create</code>."),
});
S.agent = {
  title: "Create the agent with Agent Identity", sub: "ADK agent on Agent Runtime; identity fixed at create",
  who: ["cli", "deploy-theme"], time: "2.5–7 min",
  what: "Deploys your ADK agent to <b>Agent Runtime</b> with <code>identity_type=AGENT_IDENTITY</code>. The agent gets its own principal (its \"badge\"): <code>principal://agents.global.org-ORG_ID.system.id.goog/…/reasoningEngines/ENGINE_ID</code>. It's registered in Agent Registry automatically. It also sets one environment variable: <code>GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false</code>.",
  why: "<span class=\"bb-gotcha\">Without the variable, a new agent bound to a gateway got <b>401 on its own Gemini call</b>, even without a connectivity template; the cause wasn't isolated.</span> Every per-agent grant needs that principal, so the agent comes after the foundation and before any agent policy. <b>Agent Identity can't be added later</b>: an agent created without it must be recreated. The demo creates it unbound (the wide-open starting point) and binds next.",
  tabs: agentTabs(false),
};

S.agentroles = {
  title: "Grant the agent its project roles", sub: "Gemini, registry read, logs, metrics, traces",
  who: ["cli", "deploy-theme"], time: "~1–2 min",
  what: "Ordinary project IAM for the agent's principal: <code>aiplatform.user</code>, <code>agentregistry.viewer</code>, <code>logging.logWriter</code>, <code>monitoring.metricWriter</code>, <code>cloudtrace.agent</code>, <code>browser</code>, <code>serviceusage.serviceUsageConsumer</code>. Plus <code>run.invoker</code> on Cloud Run destinations that require authentication.",
  why: "Two different questions: IAM says what the agent may <i>do</i> in Google APIs; the gateway says <i>where it may connect</i>. It needs both. These only need the principal, so they follow creation.",
  tabs: {
    curl: T(`${TOKEN}
${AGENT}
URL="https://cloudresourcemanager.googleapis.com/v3/projects/PROJECT_ID"
# Read-modify-write of the whole project policy (the etag in it guards against concurrent edits)
# jq adds the member to each role's existing unconditional binding, or adds that binding.
curl -sS -X POST ${AUTH} "\${URL}:getIamPolicy" -d '{"options":{"requestedPolicyVersion":3}}' \\
| jq --arg m "$AGENT" '{policy: (reduce ("roles/aiplatform.user", "roles/agentregistry.viewer",
    "roles/logging.logWriter", "roles/monitoring.metricWriter", "roles/cloudtrace.agent", "roles/browser",
    "roles/serviceusage.serviceUsageConsumer") as $r (.;
  if any(.bindings[]?; .role == $r and (has("condition") | not))
  then .bindings |= map(if .role == $r and (has("condition") | not) then .members = ((.members + [$m]) | unique) else . end)
  else .bindings += [{role: $r, members: [$m]}] end))}' \\
| curl -sS -X POST ${AUTH} "\${URL}:setIamPolicy" -d @-

# Cloud Run destinations that require authentication (--no-allow-unauthenticated): run.invoker on the service
RUN="https://run.googleapis.com/v2/projects/PROJECT_ID/locations/REGION/services/tickets-mcp"
curl -sS ${AUTH} "\${RUN}:getIamPolicy" \\
| jq --arg m "$AGENT" '{policy: (.bindings = ((.bindings // []) + [{role: "roles/run.invoker", members: [$m]}]))}' \\
| curl -sS -X POST ${AUTH} "\${RUN}:setIamPolicy" -d @-`, "gcloud's <code>add-iam-policy-binding</code> does this read-modify-write for you.", true),
    gcloud: T(`${AGENT}
for ROLE in roles/aiplatform.user roles/agentregistry.viewer roles/logging.logWriter roles/monitoring.metricWriter \\
            roles/cloudtrace.agent roles/browser roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding PROJECT_ID --member="$AGENT" --role="$ROLE" --condition=None
done

# Cloud Run destinations that require authentication (--no-allow-unauthenticated):
gcloud run services add-iam-policy-binding tickets-mcp --region=REGION --project=PROJECT_ID \\
  --member="$AGENT" --role=roles/run.invoker`),
    tf: T(`locals {
  agent = "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID"
}

resource "google_project_iam_member" "agent" {
  for_each = toset([
    "roles/aiplatform.user", "roles/agentregistry.viewer", "roles/logging.logWriter",
    "roles/monitoring.metricWriter", "roles/cloudtrace.agent", "roles/browser",
    "roles/serviceusage.serviceUsageConsumer",
  ])
  project = "PROJECT_ID"
  role    = each.value
  member  = local.agent
}`, null, true),
    py: T(`from google.cloud import resourcemanager_v3  # pip install google-cloud-resource-manager
from google.iam.v1 import iam_policy_pb2, policy_pb2

AGENT = "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID"
ROLES = ["roles/aiplatform.user", "roles/agentregistry.viewer", "roles/logging.logWriter",
         "roles/monitoring.metricWriter", "roles/cloudtrace.agent", "roles/browser",
         "roles/serviceusage.serviceUsageConsumer"]
crm = resourcemanager_v3.ProjectsClient()
policy = crm.get_iam_policy(request={"resource": "projects/PROJECT_ID", "options": {"requested_policy_version": 3}})
for role in ROLES:
    policy.bindings.append(policy_pb2.Binding(role=role, members=[AGENT]))
policy.version = 3  # keeps any conditional bindings intact
crm.set_iam_policy(request=iam_policy_pb2.SetIamPolicyRequest(resource="projects/PROJECT_ID", policy=policy))`, null, true),
  },
};

const bindBody = (both) => `{"spec": {"deploymentSpec": {"agentGatewayConfig": {
        "agentToAnywhereConfig": {"agentGateway": "projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW"}${both ? `,
        "clientToAgentConfig": {"agentGateway": "projects/PROJECT_ID/locations/REGION/agentGateways/INGRESS_GW"}` : ""}
      }}}}`;
const bindTabs = (both) => {
  const url = `"https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID?updateMask=spec.deploymentSpec.agentGatewayConfig"`;
  const curl = `curl -sS -X PATCH ${AUTH} \\
  ${url} \\
  -d '${bindBody(both)}'`;
  const pyCfg = both ? `{
        "agentToAnywhereConfig": {"agentGateway": "projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW"},
        "clientToAgentConfig": {"agentGateway": "projects/PROJECT_ID/locations/REGION/agentGateways/INGRESS_GW"},
    }` : `{"agentToAnywhereConfig": {"agentGateway": "projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW"}}`;
  return {
    curl: T(`${TOKEN}
# The PATCH replaces the WHOLE agentGatewayConfig: always send every direction you want to keep.
${curl}
# Long-running: the agent redeploys (~${both ? "2.5" : "5"} min). A GET shows the old config until it's done.
# Unbind = PATCH without that direction ({} for none).`),
    gcloud: NO("No gcloud command manages Agent Runtime agents (checked <code>gcloud ai</code>, <code>beta ai</code>, <code>alpha ai</code>). Use the REST PATCH with a gcloud access token:", `${TOKEN}
${curl}`),
    tf: T(`# No Terraform resource binds an existing engine to a gateway, so this runs the same PATCH.
# (If Terraform owns the whole engine, set spec.deployment_spec.agent_gateway_config on it instead.)
${both ? `# Replaces the "Bind" step's resource (same name): edit that one rather than adding a second.
` : ""}locals {
  bind_body = jsonencode({ spec = { deploymentSpec = { agentGatewayConfig = {
    agentToAnywhereConfig = { agentGateway = "projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW" }${both ? `
    clientToAgentConfig   = { agentGateway = "projects/PROJECT_ID/locations/REGION/agentGateways/INGRESS_GW" }` : ""}
  } } } })
}

resource "terraform_data" "bind" {
  # Provisioners run only on create and destroy, so a changed body must replace the resource
  # (destroy = unbind, create = bind with the new body).
  triggers_replace = [local.bind_body]
  input = {
    url  = ${url}
    body = local.bind_body
    undo = jsonencode({ spec = { deploymentSpec = { agentGatewayConfig = {} } } })
  }

  provisioner "local-exec" {
    command     = "curl -sS --fail-with-body -X PATCH \\"$URL\\" -H \\"Authorization: Bearer $(gcloud auth print-access-token)\\" -H 'Content-Type: application/json' -d \\"$BODY\\""
    environment = { URL = self.input.url, BODY = self.input.body }
  }

  provisioner "local-exec" {
    when        = destroy
    command     = "curl -sS --fail-with-body -X PATCH \\"$URL\\" -H \\"Authorization: Bearer $(gcloud auth print-access-token)\\" -H 'Content-Type: application/json' -d \\"$BODY\\""
    environment = { URL = self.input.url, BODY = self.input.undo }
  }
}`, "This is the pattern the demo's Terraform export emits."),
    py: T(PYREST + `
# The aiplatform GAPIC client has no agent_gateway_config field; a plain authorized PATCH is reliable.
r = s.patch(
    "https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID",
    params={"updateMask": "spec.deploymentSpec.agentGatewayConfig"},
    json={"spec": {"deploymentSpec": {"agentGatewayConfig": ${pyCfg}}}},
)
r.raise_for_status()
print("started", r.json()["name"])   # long-running operation`),
  };
};
S.bind = {
  title: "Bind the agent to the gateway", sub: "One PATCH per agent; ~5 min redeploy",
  who: ["toggle", "Demo toggle"], time: "~5 min",
  what: "Points the agent's outbound traffic at the egress gateway. From the moment it finishes, <b>default deny</b> applies to this agent: only the platform endpoints work; everything else is 403.",
  why: "Last of the build steps, because everything it relies on (gateway, IAP policy, platform endpoints and their grant) must already exist, or the agent's own platform calls fail (498 / 403). One PATCH per agent; it redeploys the agent and archives earlier revisions. All agents in this project and region must use this same gateway.",
  tabs: bindTabs(false),
};

S.allowa2a = {
  title: "Allow one A2A agent", sub: "egressor for this agent on that entry",
  who: ["toggle", "Demo toggle"], time: "~2 min to take effect",
  what: "Gives <b>this agent's</b> principal the permission slip (<code>roles/iap.egressor</code>) on one A2A agent's registry entry. Other agents, and other destinations, stay denied.",
  why: "It needs the agent's principal (from creation) and the destination's entry (from registration). Access follows identity, not network location: a second agent bound to the same gateway gets its own grants.",
  tabs: {
    curl: T(iapCurl("agents", "ENTRY_ID", AGENT)),
    gcloud: T(`${AGENT}
${LOOKUP("kb-agent", "ENTRY_ID")}
gcloud iap web add-iam-policy-binding --project=PROJECT_ID --resource-type=agent-registry --region=REGION \\
  --agent="$ENTRY_ID" --member="$AGENT" --role=roles/iap.egressor
# Revoke: the same flags with remove-iam-policy-binding`),
    tf: T(`resource "google_iap_agent_registry_agent_iam_member" "allow_kb" {
  project  = "PROJECT_NUMBER" # IAP IAM on Agent Registry uses the project number
  location = "REGION"
  agent_id = basename(google_agent_registry_service.kb_agent.registry_resource)
  role     = "roles/iap.egressor"
  member   = "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID"
}`),
    py: T(PYIAP + `
AGENT = "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID"
grant("projects/PROJECT_NUMBER/locations/REGION/iap_web/agentRegistry/agents/ENTRY_ID", AGENT)`, null, true),
  },
};

const mcpTabs = (svc, tools, entryVar) => ({
  curl: T(iapCurl("mcpServers", entryVar, AGENT, `${svc}-readonly`, NAMES_EXPR(tools)) + `
# All tools: leave EXPR empty. Read-only by hint: EXPR="${READONLY_EXPR}"`),
  gcloud: T(`${AGENT}
${LOOKUP(svc, entryVar)}
WHERE=(--project=PROJECT_ID --resource-type=agent-registry --region=REGION --mcp-server="$${entryVar}")   # an array: works in zsh and bash
# Pick ONE of a), b), c): an unconditional grant (a) makes any conditional grant moot.

# a) All tools
# gcloud iap web add-iam-policy-binding "\${WHERE[@]}" --member="$AGENT" --role=roles/iap.egressor

# b) Only these tools. The '' is required: initialize and tools/list carry no tool name.
gcloud iap web add-iam-policy-binding "\${WHERE[@]}" --member="$AGENT" --role=roles/iap.egressor \\
  --condition="^~^title=${svc}-readonly~expression=${NAMES_EXPR(tools)}"

# c) Read-only tools, from readOnlyHint in the registered tool spec (no tool names in the policy)
# gcloud iap web add-iam-policy-binding "\${WHERE[@]}" --member="$AGENT" --role=roles/iap.egressor \\
#   --condition="^~^title=${svc}-readonly-hints~expression=${READONLY_EXPR}"`, "The <code>^~^</code> prefix switches gcloud's list delimiter, because the expressions contain commas."),
  tf: T(`resource "google_iap_agent_registry_mcp_server_iam_member" "${svc.replace(/-/g, "_")}_readonly" {
  project       = "PROJECT_NUMBER" # IAP IAM on Agent Registry uses the project number
  location      = "REGION"
  mcp_server_id = basename(google_agent_registry_service.${svc.replace(/-/g, "_")}.registry_resource)
  role          = "roles/iap.egressor"
  member        = "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID"

  # Leave out the condition block for all tools.
  condition {
    title      = "${svc}-readonly"
    expression = "${NAMES_EXPR(tools)}"
    # read-only by hint instead:
    # expression = "${READONLY_EXPR}"
  }
}`),
  py: T(PYIAP + `
AGENT = "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID"
RES = "projects/PROJECT_NUMBER/locations/REGION/iap_web/agentRegistry/mcpServers/${entryVar}"
# Pick ONE: an unconditional grant (a) makes any conditional grant moot.
# grant(RES, AGENT)                                                   # a) all tools
grant(RES, AGENT, "${svc}-readonly", "${NAMES_EXPR(tools)}")         # b) these tools
# grant(RES, AGENT, "${svc}-readonly-hints", "${READONLY_EXPR}")     # c) read-only hint`, null, true),
});
S.allowmcp = {
  title: "Allow MCP tools", sub: "All tools, named tools, or read-only hint",
  who: ["toggle", "Demo toggle"], time: "~2–5 min to take effect",
  what: "The same permission slip on an MCP server's entry, optionally with an <b>IAM condition</b> the gateway checks on every <code>tools/call</code>: <code>mcp.toolName in ['list_tickets', '']</code>, or <code>mcp.tool.isReadOnly == true || toolName == ''</code>.",
  why: "Same prerequisites as any grant. <span class=\"bb-gotcha\">The <code>''</code> is not optional: <code>initialize</code> and <code>tools/list</code> have no tool name, and without it the MCP session never starts.</span>",
  tabs: mcpTabs("tickets-mcp", ["list_tickets"], "ENTRY_ID"),
};

S.bindin = {
  title: "Put the ingress gateway in front", sub: "Same PATCH, both directions",
  who: ["toggle", "Demo toggle"], time: "~2.5 min",
  what: "Adds <code>clientToAgentConfig</code>, so calls <i>into</i> the agent pass the ingress gateway. Without Model Armor it changes nothing visible; with it, prompt injection and sensitive data are blocked with 403 before the agent sees them.",
  why: "Optional, any time after the agent exists. Because the PATCH replaces the whole config, it re-sends the egress binding. Who may call the agent is still <code>roles/aiplatform.user</code>.",
  tabs: bindTabs(true),
};

S.matmpl = {
  title: "Create a Model Armor template", sub: "Prompt injection, jailbreak, sensitive data",
  who: ["tf", "Terraform foundation"], time: "seconds",
  what: "The screening rules: which filters (prompt injection and jailbreak, sensitive data such as card numbers) at what confidence, and the message returned on a block.",
  why: "The extension in the next steps points at it, so it comes first.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://modelarmor.REGION.rep.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/templates?templateId=shield" \\
  -d '{
    "filterConfig": {
      "piAndJailbreakFilterSettings": {"filterEnforcement": "ENABLED", "confidenceLevel": "MEDIUM_AND_ABOVE"},
      "sdpSettings": {"basicConfig": {"filterEnforcement": "ENABLED"}}
    },
    "templateMetadata": {
      "logSanitizeOperations": true, "logTemplateOperations": true,
      "customPromptSafetyErrorCode": 403, "customPromptSafetyErrorMessage": "blocked by Model Armor",
      "customLlmResponseSafetyErrorCode": 403, "customLlmResponseSafetyErrorMessage": "blocked by Model Armor"
    }
  }'`),
    gcloud: T(`# Model Armor is regional: point gcloud at the region's endpoint for this command.
CLOUDSDK_API_ENDPOINT_OVERRIDES_MODELARMOR=https://modelarmor.REGION.rep.googleapis.com/ \\
gcloud model-armor templates create shield --project=PROJECT_ID --location=REGION \\
  --pi-and-jailbreak-filter-settings-enforcement=enabled \\
  --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above \\
  --basic-config-filter-enforcement=enabled \\
  --template-metadata-custom-prompt-safety-error-code=403 \\
  --template-metadata-custom-prompt-safety-error-message="blocked by Model Armor" \\
  --template-metadata-custom-llm-response-safety-error-code=403 \\
  --template-metadata-custom-llm-response-safety-error-message="blocked by Model Armor" \\
  --template-metadata-log-operations --template-metadata-log-sanitize-operations`, "Flags checked with <code>--help</code>; the demo itself sends the REST body.", true),
    tf: T(`resource "google_model_armor_template" "shield" {
  project     = "PROJECT_ID"
  location    = "REGION"
  template_id = "shield"

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
  }

  template_metadata {
    log_sanitize_operations            = true
    log_template_operations            = true
    custom_prompt_safety_error_code    = 403
    custom_prompt_safety_error_message = "blocked by Model Armor"
  }
}`),
    py: T(`from google.api_core.client_options import ClientOptions
from google.cloud import modelarmor_v1  # pip install google-cloud-modelarmor

ma = modelarmor_v1.ModelArmorClient(client_options=ClientOptions(api_endpoint="modelarmor.REGION.rep.googleapis.com"))
ma.create_template(parent="projects/PROJECT_ID/locations/REGION", template_id="shield", template={
    "filter_config": {
        "pi_and_jailbreak_filter_settings": {"filter_enforcement": "ENABLED", "confidence_level": "MEDIUM_AND_ABOVE"},
        "sdp_settings": {"basic_config": {"filter_enforcement": "ENABLED"}},
    },
})`, null, true),
  },
};

S.maroles = {
  title: "Grant Model Armor service-agent roles", sub: "So the gateways may call Model Armor",
  who: ["tf", "Terraform foundation"], time: "~1 min",
  what: "Google's own identities that make the Model Armor callout need <code>modelarmor.calloutUser</code>, <code>modelarmor.user</code> and <code>serviceusage.serviceUsageConsumer</code>: the egress gateway's own <code>serviceExtensionsServiceAccount</code> (on its gateway card), <code>service-PROJECT_NUMBER@gcp-sa-dep</code> and, for ingress, <code>service-PROJECT_NUMBER@gcp-sa-aiplatform-re</code>.",
  why: "Service-agent roles go before the policy that needs them. The egress gateway's own service account only exists once the gateway does, which is why this sits here and not in project prep (the two project agents could be granted earlier).",
  tabs: {
    curl: T(`${TOKEN}
curl -sS ${AUTH} "https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW" \\
  | jq -r '.agentGatewayCard.serviceExtensionsServiceAccount'
# Then add the three roles for each member to the project policy (getIamPolicy -> add -> setIamPolicy),
# or use the gcloud tab.`),
    gcloud: T(`GW_SA=$(gcloud network-services agent-gateways describe EGRESS_GW --location=REGION --project=PROJECT_ID \\
  --format='value(agentGatewayCard.serviceExtensionsServiceAccount)')
for M in "serviceAccount:$GW_SA" \\
         "serviceAccount:service-PROJECT_NUMBER@gcp-sa-dep.iam.gserviceaccount.com" \\
         "serviceAccount:service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com"; do
  for ROLE in roles/modelarmor.calloutUser roles/modelarmor.user roles/serviceusage.serviceUsageConsumer; do
    gcloud projects add-iam-policy-binding PROJECT_ID --member="$M" --role="$ROLE" --condition=None
  done
done`),
    tf: T(`locals {
  ma_members = {
    gateway = "serviceAccount:\${google_network_services_agent_gateway.egress.agent_gateway_card[0].service_extensions_service_account}"
    dep     = "serviceAccount:service-PROJECT_NUMBER@gcp-sa-dep.iam.gserviceaccount.com"
    ingress = "serviceAccount:service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
  }
  ma_roles = ["roles/modelarmor.calloutUser", "roles/modelarmor.user", "roles/serviceusage.serviceUsageConsumer"]
}

resource "google_project_iam_member" "model_armor" {
  # static keys: the gateway's account is unknown until it exists
  for_each = { for p in setproduct(keys(local.ma_members), local.ma_roles) : "\${p[0]} \${p[1]}" => p }
  project  = "PROJECT_ID"
  role     = each.value[1]
  member   = local.ma_members[each.value[0]]
}`),
    py: T(`from google.cloud import network_services_v1, resourcemanager_v3
from google.iam.v1 import iam_policy_pb2, policy_pb2

gw = network_services_v1.NetworkServicesClient().get_agent_gateway(
    name="projects/PROJECT_ID/locations/REGION/agentGateways/EGRESS_GW")
members = [f"serviceAccount:{gw.agent_gateway_card.service_extensions_service_account}",
           "serviceAccount:service-PROJECT_NUMBER@gcp-sa-dep.iam.gserviceaccount.com",
           "serviceAccount:service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com"]
crm = resourcemanager_v3.ProjectsClient()
policy = crm.get_iam_policy(request={"resource": "projects/PROJECT_ID", "options": {"requested_policy_version": 3}})
for role in ["roles/modelarmor.calloutUser", "roles/modelarmor.user", "roles/serviceusage.serviceUsageConsumer"]:
    policy.bindings.append(policy_pb2.Binding(role=role, members=members))
policy.version = 3  # keeps any conditional bindings intact
crm.set_iam_policy(request=iam_policy_pb2.SetIamPolicyRequest(resource="projects/PROJECT_ID", policy=policy))`, null, true),
  },
};

const MA_SETTINGS = `[{\\"request_template_id\\": \\"projects/PROJECT_ID/locations/REGION/templates/shield\\", \\"response_template_id\\": \\"projects/PROJECT_ID/locations/REGION/templates/shield\\"}]`;
S.maext = {
  title: "Add the Model Armor extension", sub: "modelarmor.REGION.rep.googleapis.com",
  who: ["tf", "Terraform foundation"], time: "seconds",
  what: "A second authz extension: this one sends traffic content to Model Armor with your template. <code>model_armor_settings</code> is a JSON <i>string</i>, not an object.",
  why: "Needs the template; the CONTENT_AUTHZ policies in the next step point at it. Creating it changes nothing yet.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://networkservices.googleapis.com/v1beta1/projects/PROJECT_ID/locations/REGION/authzExtensions?authzExtensionId=ma-authz" \\
  -d '{
    "service": "modelarmor.REGION.rep.googleapis.com",
    "failOpen": false,
    "timeout": "5s",
    "metadata": {"model_armor_settings": "${MA_SETTINGS}"}
  }'`),
    gcloud: T(`cat > ma-ext.yaml <<'EOF'
name: ma-authz
service: modelarmor.REGION.rep.googleapis.com
failOpen: false
timeout: 5s
metadata:
  model_armor_settings: '[{"request_template_id": "projects/PROJECT_ID/locations/REGION/templates/shield", "response_template_id": "projects/PROJECT_ID/locations/REGION/templates/shield"}]'
EOF
gcloud service-extensions authz-extensions import ma-authz --source=ma-ext.yaml --location=REGION --project=PROJECT_ID`),
    tf: T(`resource "google_network_services_authz_extension" "model_armor" {
  project   = "PROJECT_ID"
  location  = "REGION"
  name      = "ma-authz"
  service   = "modelarmor.REGION.rep.googleapis.com"
  fail_open = false
  timeout   = "5s"
  metadata = {
    model_armor_settings = jsonencode([{
      request_template_id  = google_model_armor_template.shield.id
      response_template_id = google_model_armor_template.shield.id
    }])
  }
}`),
    py: T(`import json
from google.cloud import network_services_v1

tmpl = "projects/PROJECT_ID/locations/REGION/templates/shield"
parent = "projects/PROJECT_ID/locations/REGION"
ext = network_services_v1.AuthzExtension.from_json(json.dumps({
    "name": f"{parent}/authzExtensions/ma-authz",
    "service": "modelarmor.REGION.rep.googleapis.com", "failOpen": False, "timeout": "5s",
    "metadata": {"model_armor_settings": json.dumps([{"request_template_id": tmpl, "response_template_id": tmpl}])},
}))
network_services_v1.DepServiceClient().create_authz_extension(
    parent=parent, authz_extension_id="ma-authz", authz_extension=ext).result(timeout=900)`, null, true),
  },
};

S.mapol = {
  title: "Switch Model Armor on", sub: "CONTENT_AUTHZ policy on each gateway",
  who: ["toggle", "Demo checkbox"], time: "~4 min",
  what: "One <b>CONTENT_AUTHZ</b> policy per gateway, pointing at the Model Armor extension. On egress it screens <b>MCP</b> tool calls (not A2A messages); on ingress it screens prompts into the agent. Deleting the policies is \"off\".",
  why: "The last link: template → extension → policy, with the roles granted first. It works on top of IAP: a call can be allowed by policy and still blocked for content. An ingress gateway takes at most one CONTENT_AUTHZ policy.",
  tabs: {
    curl: T(`${TOKEN}
for GW in EGRESS_GW INGRESS_GW; do
  curl -sS -X POST ${AUTH} \\
    "https://networksecurity.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/authzPolicies?authzPolicyId=$GW-ma-policy" \\
    -d "{\\"target\\": {\\"resources\\": [\\"projects/PROJECT_ID/locations/REGION/agentGateways/$GW\\"]},
         \\"policyProfile\\": \\"CONTENT_AUTHZ\\", \\"action\\": \\"CUSTOM\\",
         \\"customProvider\\": {\\"authzExtension\\": {\\"resources\\": [\\"projects/PROJECT_ID/locations/REGION/authzExtensions/ma-authz\\"]}}}"
done
# Off: DELETE .../authzPolicies/EGRESS_GW-ma-policy (and the ingress one). ~4 min each way.`),
    gcloud: T(`for GW in EGRESS_GW INGRESS_GW; do
cat > $GW-ma.yaml <<EOF
name: $GW-ma-policy
target:
  resources: [projects/PROJECT_ID/locations/REGION/agentGateways/$GW]
policyProfile: CONTENT_AUTHZ
action: CUSTOM
customProvider:
  authzExtension:
    resources: [projects/PROJECT_ID/locations/REGION/authzExtensions/ma-authz]
EOF
gcloud beta network-security authz-policies import $GW-ma-policy --source=$GW-ma.yaml --location=REGION --project=PROJECT_ID
done
# Off: gcloud network-security authz-policies delete EGRESS_GW-ma-policy --location=REGION --project=PROJECT_ID --quiet`),
    tf: T(`resource "google_network_security_authz_policy" "model_armor" {
  for_each       = { egress = google_network_services_agent_gateway.egress.id, ingress = google_network_services_agent_gateway.ingress.id }
  project        = "PROJECT_ID"
  location       = "REGION"
  name           = "\${each.key}-ma-policy"
  policy_profile = "CONTENT_AUTHZ"
  action         = "CUSTOM"

  target {
    resources = [each.value]
  }
  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.model_armor.id]
    }
  }
  depends_on = [google_project_iam_member.model_armor]
}`),
    py: T(`from google.cloud import network_security_v1

ns = network_security_v1.NetworkSecurityClient()
P = network_security_v1.AuthzPolicy
for gw in ["EGRESS_GW", "INGRESS_GW"]:
    ns.create_authz_policy(parent="projects/PROJECT_ID/locations/REGION", authz_policy_id=f"{gw}-ma-policy",
        authz_policy=P(
            target=P.Target(resources=[f"projects/PROJECT_ID/locations/REGION/agentGateways/{gw}"]),
            policy_profile=P.PolicyProfile.CONTENT_AUTHZ, action=P.AuthzAction.CUSTOM,
            custom_provider=P.CustomProvider(authz_extension=P.CustomProvider.AuthzExtension(
                resources=["projects/PROJECT_ID/locations/REGION/authzExtensions/ma-authz"])))).result(timeout=900)`, null, true),
  },
};

const LOGF = `logName="projects/PROJECT_ID/logs/networkservices.googleapis.com%2Fgateway_requests"`;
const streamQuery = (msg) => `curl -sS -N -X POST ${AUTH} \\
  "https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/reasoningEngines/ENGINE_ID:streamQuery?alt=sse" \\
  -d '{"class_method": "async_stream_query", "input": {"user_id": "tester", "message": "${msg}"}}'`;
S.verify = {
  title: "Test, then read the gateway log", sub: "403 vs 200, and why",
  who: ["hand", "You, by hand"], time: "seconds",
  what: "Ask the agent to call things, then read the gateway's own decision log (<code>gateway_requests</code>): status, <code>ALLOWED</code> / <code>DENIED</code>, the host and the registry entry it matched.",
  why: "Allow about 2 minutes after a grant before testing. A denial is plain text <code>Egress request is not authorized.</code> (403); a Model Armor block mentions Model Armor. The very first call after a bind or registration sometimes fails TLS once; retry.",
  tabs: {
    curl: T(`${TOKEN}
${streamQuery("List my open tickets, then delete ticket 42")}

# The gateway's decisions (egress only: the ingress gateway writes no request logs)
curl -sS -X POST ${AUTH} "https://logging.googleapis.com/v2/entries:list" \\
  -d '{"resourceNames": ["projects/PROJECT_ID"], "orderBy": "timestamp desc", "pageSize": 20,
       "filter": "${LOGF.replace(/"/g, '\\"')}"}' \\
  | jq '.entries[]? | {t: .timestamp, status: .httpRequest.status, result: .jsonPayload.authzPolicyInfo.result,
                      host: .jsonPayload.enforcedGatewaySecurityPolicy.hostname,
                      entry: .jsonPayload.agentGatewayInfo.agentRegistryResource}'`),
    gcloud: T(`# --freshness: how far back to look (raise it for older tests)
gcloud logging read '${LOGF}' --project=PROJECT_ID --limit=20 --freshness=1d \\
  --format='table(timestamp, httpRequest.status, jsonPayload.authzPolicyInfo.result, jsonPayload.enforcedGatewaySecurityPolicy.hostname)'`, "Calling the agent itself has no gcloud command: use the curl tab (<code>streamQuery</code>)."),
    tf: NO("Nothing to create: verifying is reading, not building. If you want alerts on denials, a log-based metric on the same filter works, but the demo doesn't use one.", `# filter for a log-based metric or alert:
# ${LOGF} AND jsonPayload.authzPolicyInfo.result="DENIED"`),
    py: T(`from google.cloud import logging  # pip install google-cloud-logging

client = logging.Client(project="PROJECT_ID")
for e in client.list_entries(filter_='${LOGF}', order_by=logging.DESCENDING, max_results=20):
    p = e.payload or {}
    print(e.timestamp, e.http_request and e.http_request.get("status"),
          p.get("authzPolicyInfo", {}).get("result"), p.get("enforcedGatewaySecurityPolicy", {}).get("hostname"))`, null, true),
  },
};

/* ---------------------------------------------------------- Private Networking steps */
S.region = {
  title: "Pick a region for private egress", sub: "One egress gateway per project + region",
  who: ["hand", "You, by hand"], time: "—",
  what: "Choose a region where no agents are bound to another egress gateway. The VPC attachment, the template, the gateway and the agent all live in this one region.",
  why: "Every agent in a project and region shares one egress gateway, and an existing gateway can't simply be switched to private egress (it needs two PATCHes, or a delete and recreate that forces every agent to rebind). The demo avoids that by using a second region.",
  tabs: {
    curl: T(`${TOKEN}
# Gateways already in the region
curl -sS ${AUTH} "https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentGateways" \\
  | jq -r '.agentGateways[]? | [.name, .googleManaged.governedAccessPath] | @tsv'
# Agents in the region and the gateway each is bound to
curl -sS ${AUTH} "https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/reasoningEngines" \\
  | jq -r '.reasoningEngines[]? | [.displayName, (.spec.deploymentSpec.agentGatewayConfig.agentToAnywhereConfig.agentGateway // "unbound")] | @tsv'`),
    gcloud: T(`gcloud network-services agent-gateways list --location=REGION --project=PROJECT_ID`, "Agents: use the curl tab (no gcloud command lists Agent Runtime agents)."),
    tf: NO("A decision, not a resource. Put the region in a variable; the demo's module also has a precondition that it differs from the Internet-mode region.", `# Cross-variable validation needs Terraform >= 1.9.
variable "region" {
  type = string # the Internet-mode region
}

variable "pn_region" {
  type = string
  validation {
    condition     = var.pn_region != var.region
    error_message = "Use a region with no other egress gateway serving agents."
  }
}`),
    py: T(PYREST + `
gws = s.get("https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentGateways").json()
for g in gws.get("agentGateways", []):
    print(g["name"], g["googleManaged"]["governedAccessPath"])`),
  },
};

S.vpc = {
  title: "VPC and a small subnet", sub: "/28 with Private Google Access on",
  who: ["tf", "Terraform (private_egress)"], time: "~1 min",
  what: "Your private network, plus a small dedicated subnet the gateway plugs into. A /28 is enough: the gateway's network interface takes one address. <b>Private Google Access</b> lets things in the subnet reach Google APIs without public IPs.",
  why: "The attachment in the next step is built on this subnet, so it comes first.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} "https://compute.googleapis.com/compute/v1/projects/PROJECT_ID/global/networks" \\
  -d '{"name": "VPC", "autoCreateSubnetworks": false}'
curl -sS -X POST ${AUTH} "https://compute.googleapis.com/compute/v1/projects/PROJECT_ID/regions/REGION/subnetworks" \\
  -d '{"name": "SUBNET", "network": "projects/PROJECT_ID/global/networks/VPC",
       "ipCidrRange": "10.70.0.0/28", "privateIpGoogleAccess": true}'`, null, true),
    gcloud: T(`gcloud compute networks create VPC --subnet-mode=custom --project=PROJECT_ID
gcloud compute networks subnets create SUBNET --network=VPC --region=REGION --project=PROJECT_ID \\
  --range=10.70.0.0/28 --enable-private-ip-google-access`),
    tf: T(`resource "google_compute_network" "vpc" {
  project                 = "PROJECT_ID"
  name                    = "VPC"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "att" {
  project                  = "PROJECT_ID"
  region                   = "REGION"
  name                     = "SUBNET"
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = "10.70.0.0/28"
  private_ip_google_access = true
}`),
    py: T(`from google.cloud import compute_v1  # pip install google-cloud-compute

compute_v1.NetworksClient().insert(project="PROJECT_ID", network_resource=compute_v1.Network(
    name="VPC", auto_create_subnetworks=False)).result()
compute_v1.SubnetworksClient().insert(project="PROJECT_ID", region="REGION", subnetwork_resource=compute_v1.Subnetwork(
    name="SUBNET", network="projects/PROJECT_ID/global/networks/VPC",
    ip_cidr_range="10.70.0.0/28", private_ip_google_access=True)).result()`, null, true),
  },
};

S.att = {
  title: "Network attachment", sub: "The gateway's door into your VPC",
  who: ["tf", "Terraform (private_egress)"], time: "seconds",
  what: "The <b>gateway's door into your VPC</b>. Google's side (the gateway's Private Service Connect interface) connects to it automatically with <code>ACCEPT_AUTOMATIC</code> and takes an IP from the /28.",
  why: "The connectivity template names it. <span class=\"bb-gotcha\">It must be in the <b>same region</b> as the gateway: a different region fails at template create with an unhelpful \"internal error\". It can't be changed on a gateway later.</span>",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} "https://compute.googleapis.com/compute/v1/projects/PROJECT_ID/regions/REGION/networkAttachments" \\
  -d '{"name": "ATT", "connectionPreference": "ACCEPT_AUTOMATIC",
       "subnetworks": ["projects/PROJECT_ID/regions/REGION/subnetworks/SUBNET"]}'`, null, true),
    gcloud: T(`gcloud compute network-attachments create ATT --region=REGION --project=PROJECT_ID \\
  --subnets=SUBNET --connection-preference=ACCEPT_AUTOMATIC`),
    tf: T(`resource "google_compute_network_attachment" "att" {
  project               = "PROJECT_ID"
  region                = "REGION" # the gateway's region
  name                  = "ATT"
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [google_compute_subnetwork.att.self_link]
}`),
    py: T(`from google.cloud import compute_v1

compute_v1.NetworkAttachmentsClient().insert(project="PROJECT_ID", region="REGION",
    network_attachment_resource=compute_v1.NetworkAttachment(
        name="ATT", connection_preference="ACCEPT_AUTOMATIC",
        subnetworks=["projects/PROJECT_ID/regions/REGION/subnetworks/SUBNET"])).result()`, null, true),
  },
};

S.psc = {
  title: "PSC endpoint for Google APIs", sub: "A private IP that leads to Google services",
  who: ["tf", "Terraform (private_egress)"], time: "seconds",
  what: "A <b>private IP inside your VPC that leads to Google services</b> such as Cloud Run. Internal-only Cloud Run services accept traffic that arrives this way, so no load balancer and no internet path are needed.",
  why: "The DNS records in the next step point at this IP, so it comes first. <span class=\"bb-gotcha\">The forwarding-rule name allows lowercase letters and digits only: <b>no hyphens, at most 20 characters</b> (e.g. <code>pscapis</code>).</span> It's the main standing cost (about USD 8/month).",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} "https://compute.googleapis.com/compute/v1/projects/PROJECT_ID/global/addresses" \\
  -d '{"name": "psc-ip", "purpose": "PRIVATE_SERVICE_CONNECT", "addressType": "INTERNAL",
       "address": "PSC_IP", "network": "projects/PROJECT_ID/global/networks/VPC"}'
curl -sS -X POST ${AUTH} "https://compute.googleapis.com/compute/v1/projects/PROJECT_ID/global/forwardingRules" \\
  -d '{"name": "pscapis", "target": "all-apis",
       "network": "projects/PROJECT_ID/global/networks/VPC",
       "IPAddress": "projects/PROJECT_ID/global/addresses/psc-ip"}'`, null, true),
    gcloud: T(`# PSC_IP: an unused private IP outside the subnet, e.g. 10.70.250.10
gcloud compute addresses create psc-ip --global --project=PROJECT_ID \\
  --purpose=PRIVATE_SERVICE_CONNECT --addresses=PSC_IP --network=VPC
gcloud compute forwarding-rules create pscapis --global --project=PROJECT_ID \\
  --network=VPC --address=psc-ip --target-google-apis-bundle=all-apis   # name: letters/digits only, <= 20`),
    tf: T(`resource "google_compute_global_address" "psc" {
  project      = "PROJECT_ID"
  name         = "psc-ip"
  purpose      = "PRIVATE_SERVICE_CONNECT"
  address_type = "INTERNAL"
  address      = "PSC_IP"
  network      = google_compute_network.vpc.id
}

resource "google_compute_global_forwarding_rule" "psc" {
  project               = "PROJECT_ID"
  name                  = "pscapis" # ^[a-z][a-z0-9]{0,19}$
  target                = "all-apis"
  network               = google_compute_network.vpc.id
  ip_address            = google_compute_global_address.psc.id
  load_balancing_scheme = ""
}`),
    py: T(`from google.cloud import compute_v1

compute_v1.GlobalAddressesClient().insert(project="PROJECT_ID", address_resource=compute_v1.Address(
    name="psc-ip", purpose="PRIVATE_SERVICE_CONNECT", address_type="INTERNAL",
    address="PSC_IP", network="projects/PROJECT_ID/global/networks/VPC")).result()
compute_v1.GlobalForwardingRulesClient().insert(project="PROJECT_ID", forwarding_rule_resource=compute_v1.ForwardingRule(
    name="pscapis", target="all-apis", load_balancing_scheme="",
    network="projects/PROJECT_ID/global/networks/VPC",
    I_p_address="projects/PROJECT_ID/global/addresses/psc-ip")).result()`, "The compute client names the address field <code>I_p_address</code>.", true),
  },
};

S.dns = {
  title: "Private DNS zone for run.app", sub: "Your VPC's phone book entry",
  who: ["tf", "Terraform (private_egress)"], time: "seconds",
  what: "<b>Your VPC's phone book entry</b>: inside the VPC, <code>run.app</code> and <code>*.run.app</code> resolve to the PSC endpoint's private IP. Both records are needed (apex and wildcard), so both run.app URL forms resolve.",
  why: "The template's DNS peering looks names up in this zone, so it must exist first. Note the side effect: once the gateway peers <code>run.app</code>, <b>every</b> run.app name it calls goes through your VPC, public services included.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} "https://dns.googleapis.com/dns/v1/projects/PROJECT_ID/managedZones" \\
  -d '{"name": "run-app", "dnsName": "run.app.", "description": "run.app -> PSC endpoint", "visibility": "private",
       "privateVisibilityConfig": {"networks": [{"networkUrl": "https://www.googleapis.com/compute/v1/projects/PROJECT_ID/global/networks/VPC"}]}}'
curl -sS -X POST ${AUTH} "https://dns.googleapis.com/dns/v1/projects/PROJECT_ID/managedZones/run-app/changes" \\
  -d '{"additions": [{"name": "run.app.", "type": "A", "ttl": 300, "rrdatas": ["PSC_IP"]},
                     {"name": "*.run.app.", "type": "A", "ttl": 300, "rrdatas": ["PSC_IP"]}]}'`, null, true),
    gcloud: T(`gcloud dns managed-zones create run-app --project=PROJECT_ID --dns-name=run.app. \\
  --visibility=private --networks=VPC --description="run.app -> PSC endpoint"
gcloud dns record-sets create run.app.   --zone=run-app --project=PROJECT_ID --type=A --ttl=300 --rrdatas=PSC_IP
gcloud dns record-sets create '*.run.app.' --zone=run-app --project=PROJECT_ID --type=A --ttl=300 --rrdatas=PSC_IP`),
    tf: T(`resource "google_dns_managed_zone" "run_app" {
  project    = "PROJECT_ID"
  name       = "run-app"
  dns_name   = "run.app."
  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.vpc.id
    }
  }
}

resource "google_dns_record_set" "run_app" {
  for_each     = toset(["run.app.", "*.run.app."])
  project      = "PROJECT_ID"
  managed_zone = google_dns_managed_zone.run_app.name
  name         = each.value
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.psc.address]
}`),
    py: T(PYREST + `
# google-cloud-dns can't create a private zone (and visibility can't be changed later), so use REST.
base = "https://dns.googleapis.com/dns/v1/projects/PROJECT_ID/managedZones"
s.post(base, json={
    "name": "run-app", "dnsName": "run.app.", "description": "run.app -> PSC endpoint",
    "visibility": "private",
    "privateVisibilityConfig": {"networks": [
        {"networkUrl": "https://www.googleapis.com/compute/v1/projects/PROJECT_ID/global/networks/VPC"}]},
}).raise_for_status()
s.post(f"{base}/run-app/changes", json={"additions": [
    {"name": name, "type": "A", "ttl": 300, "rrdatas": ["PSC_IP"]} for name in ("run.app.", "*.run.app.")
]}).raise_for_status()`, null, true),
  },
};

S.saroles = {
  title: "Let the gateway use your network", sub: "gcp-sa-agentgateway: networkAdmin + dns.peer",
  who: ["tf", "Terraform (private_egress)"], time: "~1 min",
  what: "Permission for <b>Google's gateway identity</b> (<code>service-PROJECT_NUMBER@gcp-sa-agentgateway</code>) to use your attachment and to read your DNS (<b>DNS peering</b> lets the gateway look names up in your VPC's phone book).",
  why: "Service-agent roles go before the gateway that needs them; the demo's gateway depends on these grants, so they're removed only after it's gone. <code>roles/compute.networkAdmin</code> + <code>roles/dns.peer</code> were enough; a narrower custom role is untested.",
  unv: "In a brand-new project, whether this service agent exists before the first gateway is open: the demo grants it after its first (Internet-mode) gateway exists.",
  tabs: {
    curl: T(`${TOKEN}
SA="serviceAccount:service-PROJECT_NUMBER@gcp-sa-agentgateway.iam.gserviceaccount.com"
# Project policy read-modify-write (see the gcloud tab for the simple form):
curl -sS -X POST ${AUTH} "https://cloudresourcemanager.googleapis.com/v3/projects/PROJECT_ID:getIamPolicy" -d '{"options":{"requestedPolicyVersion":3}}' \\
| jq --arg m "$SA" '{policy: (.bindings += [{role: "roles/compute.networkAdmin", members: [$m]},
                                           {role: "roles/dns.peer", members: [$m]}])}' \\
| curl -sS -X POST ${AUTH} "https://cloudresourcemanager.googleapis.com/v3/projects/PROJECT_ID:setIamPolicy" -d @-`, null, true),
    gcloud: T(`SA=serviceAccount:service-PROJECT_NUMBER@gcp-sa-agentgateway.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding PROJECT_ID --member=$SA --role=roles/compute.networkAdmin --condition=None
gcloud projects add-iam-policy-binding PROJECT_ID --member=$SA --role=roles/dns.peer --condition=None`),
    tf: T(`resource "google_project_iam_member" "agentgateway" {
  for_each = toset(["roles/compute.networkAdmin", "roles/dns.peer"])
  project  = "PROJECT_ID"
  role     = each.value
  member   = "serviceAccount:service-PROJECT_NUMBER@gcp-sa-agentgateway.iam.gserviceaccount.com"
}`),
    py: T(`from google.cloud import resourcemanager_v3
from google.iam.v1 import iam_policy_pb2, policy_pb2

SA = "serviceAccount:service-PROJECT_NUMBER@gcp-sa-agentgateway.iam.gserviceaccount.com"
crm = resourcemanager_v3.ProjectsClient()
policy = crm.get_iam_policy(request={"resource": "projects/PROJECT_ID", "options": {"requested_policy_version": 3}})
for role in ("roles/compute.networkAdmin", "roles/dns.peer"):
    policy.bindings.append(policy_pb2.Binding(role=role, members=[SA]))
policy.version = 3  # keeps any conditional bindings intact
crm.set_iam_policy(request=iam_policy_pb2.SetIamPolicyRequest(resource="projects/PROJECT_ID", policy=policy))`, null, true),
  },
};

const TMPL_YAML = `accessPath: AGENT_TO_ANYWHERE
deploymentModel: CENTRALIZED
egressNetworkConfig:
  networkAttachment: projects/PROJECT_ID/regions/REGION/networkAttachments/ATT   # project ID
  dnsPeeringConfig:
    domains: ["run.app."]
    targetNetwork: projects/PROJECT_ID/global/networks/VPC                       # project ID
  vpcEgress: PRIVATE_RANGES_ONLY`;
S.tmpl = {
  title: "Connectivity template", sub: "The gateway's private-network settings",
  who: ["tf", "Terraform (private_egress)"], time: "< 1 s",
  what: "One resource holding the gateway's private-network settings: <b>which attachment</b> (door), <b>which domains</b> to look up in your VPC (<code>run.app.</code>), and <b>which traffic goes in</b> (<code>PRIVATE_RANGES_ONLY</code>: only names that resolve to private IPs; Google APIs still leave directly).",
  why: "The gateway points at it, so create it before the gateway. <span class=\"bb-gotcha\">Inside the template use the project <b>ID</b>; the gateway's reference to it uses the project <b>NUMBER</b>. A template in use is frozen: to change it, create a new one and swap (11 s).</span> No console UI for this.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentConnectivityTemplates?agentConnectivityTemplateId=TMPL" \\
  -d '{
    "accessPath": "AGENT_TO_ANYWHERE",
    "deploymentModel": "CENTRALIZED",
    "egressNetworkConfig": {
      "networkAttachment": "projects/PROJECT_ID/regions/REGION/networkAttachments/ATT",
      "dnsPeeringConfig": {"domains": ["run.app."], "targetNetwork": "projects/PROJECT_ID/global/networks/VPC"},
      "vpcEgress": "PRIVATE_RANGES_ONLY"
    }
  }'`),
    gcloud: T(`cat > tmpl.yaml <<'EOF'
${TMPL_YAML}
EOF
gcloud network-services agent-connectivity-templates import TMPL --source=tmpl.yaml \\
  --location=REGION --project=PROJECT_ID`, "<code>agent-connectivity-templates</code> isn't in gcloud 576 (GA, beta or alpha); it may need a newer SDK. The curl and Terraform tabs are the tested forms. No console UI exists for templates.", true),
    tf: T(`resource "google_network_services_agent_connectivity_template" "tmpl" {
  project                        = "PROJECT_ID"
  location                       = "REGION"
  agent_connectivity_template_id = "TMPL" # tip: name it from a hash of its content, so a change creates a new one
  access_path                    = "AGENT_TO_ANYWHERE"
  deployment_model               = "CENTRALIZED" # needs hashicorp/google >= 8.7.0

  egress_network_config {
    network_attachment = "projects/PROJECT_ID/regions/REGION/networkAttachments/ATT" # project ID
    vpc_egress         = "PRIVATE_RANGES_ONLY"
    dns_peering_config {
      domains        = ["run.app."]
      target_network = "projects/PROJECT_ID/global/networks/VPC" # project ID
    }
  }

  lifecycle {
    create_before_destroy = true # frozen while in use: swap, don't edit
  }
  depends_on = [google_compute_network_attachment.att, google_compute_global_forwarding_rule.psc, google_dns_record_set.run_app, google_project_iam_member.agentgateway]
}`),
    py: T(PYREST + `
r = s.post("https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentConnectivityTemplates",
           params={"agentConnectivityTemplateId": "TMPL"}, json={
    "accessPath": "AGENT_TO_ANYWHERE",
    "deploymentModel": "CENTRALIZED",
    "egressNetworkConfig": {
        "networkAttachment": "projects/PROJECT_ID/regions/REGION/networkAttachments/ATT",
        "dnsPeeringConfig": {"domains": ["run.app."], "targetNetwork": "projects/PROJECT_ID/global/networks/VPC"},
        "vpcEgress": "PRIVATE_RANGES_ONLY",
    }})
r.raise_for_status()`),
  },
};

S.pngw = {
  title: "Create the gateway with the template", sub: "AGENT_TO_ANYWHERE + template, project NUMBER",
  who: ["tf", "Terraform (regional)"], time: "~3.5–4 min",
  what: "The same egress checkpoint as in Internet mode, created <b>with</b> <code>agentConnectivityTemplate</code> from the start, so it has a route into your VPC.",
  why: "Create it with the template rather than adding one later: <span class=\"bb-gotcha\">a one-step PATCH from no template to a template fails (\"internal error\", code 13) and drops the gateway's labels. On an existing gateway it takes two PATCHes: inline <code>networkConfig</code> first (~3.5 min), then <code>agentConnectivityTemplate,networkConfig</code> (~3 min).</span>",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentGateways?agentGatewayId=EGRESS_GW" \\
  -d '${egwBody(true)}'
# The template reference must use the project NUMBER: the ID form is rejected with 400.

# Existing gateway instead? Two PATCHes, not one (always keep labels in the mask):
#  1) PATCH ...agentGateways/EGRESS_GW?updateMask=networkConfig,labels
#     {"networkConfig": {"egress": {"networkAttachment": "projects/PROJECT_ID/regions/REGION/networkAttachments/ATT"},
#      "dnsPeeringConfig": {"domains": ["run.app."], "targetProject": "PROJECT_ID",
#                           "targetNetwork": "projects/PROJECT_ID/global/networks/VPC"}}, "labels": {...}}
#  2) PATCH ...?updateMask=agentConnectivityTemplate,networkConfig,labels
#     {"agentConnectivityTemplate": "projects/PROJECT_NUMBER/locations/REGION/agentConnectivityTemplates/TMPL", "labels": {...}}`),
    gcloud: T(`cat > pn-egress.yaml <<'EOF'
name: EGRESS_GW
protocols: [MCP]   # required by gcloud's import schema
googleManaged:
  governedAccessPath: AGENT_TO_ANYWHERE
registries:
  - //agentregistry.googleapis.com/projects/PROJECT_ID/locations/REGION
agentConnectivityTemplate: projects/PROJECT_NUMBER/locations/REGION/agentConnectivityTemplates/TMPL
EOF
gcloud network-services agent-gateways import EGRESS_GW --source=pn-egress.yaml \\
  --location=REGION --project=PROJECT_ID`, "gcloud 576's gateway import schema (GA, beta and alpha) has no <code>agentConnectivityTemplate</code> field, so this may be rejected until a newer SDK. The curl tab is the tested form.", true),
    tf: T(`resource "google_network_services_agent_gateway" "egress" {
  project                     = "PROJECT_ID"
  location                    = "REGION"
  name                        = "EGRESS_GW"
  registries                  = ["//agentregistry.googleapis.com/projects/PROJECT_ID/locations/REGION"]
  agent_connectivity_template = "projects/PROJECT_NUMBER/locations/REGION/agentConnectivityTemplates/\${google_network_services_agent_connectivity_template.tmpl.agent_connectivity_template_id}"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }
  lifecycle {
    ignore_changes = [protocols]
  }
}
# Don't add a template to an existing bare gateway through Terraform: adding network_config plans
# a replace, and setting only agent_connectivity_template sends the failing one-step PATCH.`),
    py: T(PYREST + `
r = s.post("https://networkservices.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/agentGateways",
           params={"agentGatewayId": "EGRESS_GW"}, json={
    "googleManaged": {"governedAccessPath": "AGENT_TO_ANYWHERE"},
    "registries": ["//agentregistry.googleapis.com/projects/PROJECT_ID/locations/REGION"],
    "agentConnectivityTemplate": "projects/PROJECT_NUMBER/locations/REGION/agentConnectivityTemplates/TMPL",
})
r.raise_for_status()   # long-running operation, ~3.5 min`),
  },
};

S.privdeploy = {
  title: "Deploy the private service", sub: "Cloud Run with ingress internal",
  who: ["cli", "deploy-theme"], time: "~1–2 min",
  what: "An MCP server on Cloud Run with <b>ingress = internal</b>: from the internet it answers 404. Traffic that arrives through your VPC's PSC endpoint is accepted.",
  why: "Same as any destination: deploy, then register. Its two URLs come from this deploy.",
  tabs: {
    curl: T(`${TOKEN}
curl -sS -X POST ${AUTH} \\
  "https://run.googleapis.com/v2/projects/PROJECT_ID/locations/REGION/services?serviceId=payroll-mcp" \\
  -d '{"ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY",
       "template": {"serviceAccount": "targets-sa@PROJECT_ID.iam.gserviceaccount.com",
                    "containers": [{"image": "REGION-docker.pkg.dev/PROJECT_ID/demo/mcp-server:latest"}]}}'`, null, true),
    gcloud: T(`gcloud run deploy payroll-mcp --source=./mcp_server --region=REGION --project=PROJECT_ID \\
  --service-account=targets-sa@PROJECT_ID.iam.gserviceaccount.com \\
  --ingress=internal --no-allow-unauthenticated
# Two URL forms, both registered next:
#   https://payroll-mcp-PROJECT_NUMBER.REGION.run.app
#   https://payroll-mcp-HASH-xx.a.run.app`),
    tf: T(`resource "google_cloud_run_v2_service" "payroll_mcp" {
  project  = "PROJECT_ID"
  location = "REGION"
  name     = "payroll-mcp"
  deletion_protection = false # default true would block terraform destroy
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = "targets-sa@PROJECT_ID.iam.gserviceaccount.com"
    containers {
      image = "REGION-docker.pkg.dev/PROJECT_ID/demo/mcp-server:latest"
    }
  }
}`, "The demo deploys Cloud Run with the CLI.", true),
    py: T(`from google.cloud import run_v2

svc = run_v2.Service(ingress=run_v2.IngressTraffic.INGRESS_TRAFFIC_INTERNAL_ONLY,
    template=run_v2.RevisionTemplate(service_account="targets-sa@PROJECT_ID.iam.gserviceaccount.com",
        containers=[run_v2.Container(image="REGION-docker.pkg.dev/PROJECT_ID/demo/mcp-server:latest")]))
print(run_v2.ServicesClient().create_service(parent="projects/PROJECT_ID/locations/REGION",
      service=svc, service_id="payroll-mcp").result().urls)`, null, true),
  },
};

S.privreg = {
  title: "Register it, both URLs", sub: "The registry matches hostnames exactly",
  who: ["cli", "deploy-theme"], time: "4–15 min until the gateway sees it",
  what: "The same registry entry as a public MCP server, with its tool spec, and <b>both run.app URLs</b> as interfaces. The gateway matches the exact host the agent called.",
  why: "A route isn't permission: without an entry and a grant the call is 403 even though the network path exists.",
  tabs: {
    curl: T(`${TOKEN}
TOOLS='{"tools": [
  {"name": "get_payslip", "description": "Get a payslip", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": true}},
  {"name": "update_bank_details", "description": "Change bank details", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": false}}
]}'
curl -sS -X POST ${AUTH} \\
  "https://agentregistry.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/services?serviceId=payroll-mcp" -d "{
  \\"displayName\\": \\"Payroll MCP\\",
  \\"mcpServerSpec\\": {\\"type\\": \\"TOOL_SPEC\\", \\"content\\": $TOOLS},
  \\"interfaces\\": [
    {\\"url\\": \\"https://payroll-mcp-PROJECT_NUMBER.REGION.run.app/mcp\\", \\"protocolBinding\\": \\"JSONRPC\\"},
    {\\"url\\": \\"https://payroll-mcp-HASH-xx.a.run.app/mcp\\", \\"protocolBinding\\": \\"JSONRPC\\"}]}"`),
    gcloud: T(`TOOLS='{"tools": [
  {"name": "get_payslip", "description": "Get a payslip", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": true}},
  {"name": "update_bank_details", "description": "Change bank details", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": false}}
]}'
gcloud agent-registry services create payroll-mcp --project=PROJECT_ID --location=REGION \\
  --display-name="Payroll MCP" --mcp-server-spec-type=tool-spec --mcp-server-spec-content="$TOOLS" \\
  --interfaces="protocolBinding=jsonrpc,url=https://payroll-mcp-PROJECT_NUMBER.REGION.run.app/mcp" \\
  --interfaces="protocolBinding=jsonrpc,url=https://payroll-mcp-HASH-xx.a.run.app/mcp"`),
    tf: T(`resource "google_agent_registry_service" "payroll_mcp" {
  project      = "PROJECT_ID"
  location     = "REGION"
  service_id   = "payroll-mcp"
  display_name = "Payroll MCP"

  mcp_server_spec {
    type    = "TOOL_SPEC"
    content = jsonencode({ tools = [
      { name = "get_payslip", inputSchema = { type = "object" }, annotations = { readOnlyHint = true } },
      { name = "update_bank_details", inputSchema = { type = "object" }, annotations = { readOnlyHint = false } },
    ] })
  }
  interfaces {
    url              = "https://payroll-mcp-PROJECT_NUMBER.REGION.run.app/mcp"
    protocol_binding = "JSONRPC"
  }
  interfaces {
    url              = "https://payroll-mcp-HASH-xx.a.run.app/mcp"
    protocol_binding = "JSONRPC"
  }
}`, "The demo registers theme entries with the CLI.", true),
    py: T(PYREST + `
tools = [
    {"name": "get_payslip", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "update_bank_details", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": False}},
]
s.post("https://agentregistry.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/services",
       params={"serviceId": "payroll-mcp"}, json={
    "displayName": "Payroll MCP",
    "mcpServerSpec": {"type": "TOOL_SPEC", "content": {"tools": tools}},
    "interfaces": [{"url": u, "protocolBinding": "JSONRPC"} for u in (
        "https://payroll-mcp-PROJECT_NUMBER.REGION.run.app/mcp", "https://payroll-mcp-HASH-xx.a.run.app/mcp")],
}).raise_for_status()`),
  },
};

S.pnagent = {
  title: "Create the agent (identity + env var)", sub: "Agent Identity, token-sharing switch, no PSC interface",
  who: ["cli", "deploy-theme"], time: "2.5–7 min",
  what: "The same agent creation as in Internet mode, including the environment variable <code>GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false</code>.",
  why: "<span class=\"bb-gotcha\">Without the variable, an agent behind a gateway with a template got <b>401 on every Google API call</b>; the cause wasn't isolated, and the docs now include it in their examples. Never also give the agent its own PSC interface: create rejects both, and adding a gateway to such an agent breaks it.</span>",
  tabs: agentTabs(true),
};

S.pnbind = Object.assign({}, S.bind, {
  sub: "Same PATCH, to the VPC-enabled gateway", time: "~4.5 min",
  why: "Same rule as Internet mode: foundation first, then bind. Verified live: binding a running agent by PATCH to a template gateway works (about 4m20s), with no 401s thanks to the env var.",
});

S.privgrant = {
  title: "Grant the private MCP server", sub: "All tools or read-only only",
  who: ["toggle", "Demo toggle"], time: "~2 min to take effect",
  what: "Exactly the same permission slip as for a public MCP server. Policy doesn't change in private mode: the VPC only adds a private route.",
  why: "Needs the agent's principal and the entry. With read-only, <code>update_bank_details</code> stays 403 while <code>get_payslip</code> returns 200.",
  tabs: mcpTabs("payroll-mcp", ["get_payslip"], "ENTRY_ID"),
};

S.pnverify = {
  title: "Prove the private path", sub: "404 direct, 403 ungranted, 200 via PSC IP",
  who: ["hand", "You, by hand"], time: "seconds",
  what: "Three checks: the service is 404 from the internet; through the gateway with no grant it's 403; after the grant it's 200, and the gateway log's <code>httpRequest.serverIp</code> is <b>your PSC endpoint's IP</b> (10.x).",
  why: "Cloud Run's own log shows the caller as <code>0.0.0.0</code>, so the gateway log is the proof. Every <code>*.run.app</code> name the gateway calls, public ones included, now shows the PSC IP: run.app is peered into your VPC.",
  tabs: {
    curl: T(`# 1) Direct from the internet: expect 404 (ingress internal)
curl -sS -o /dev/null -w '%{http_code}\\n' https://payroll-mcp-PROJECT_NUMBER.REGION.run.app/mcp

# 2) Through the agent (bound, no grant: 403; ~2 min after the grant: 200)
${TOKEN}
${streamQuery("Show my latest payslip")}

# 3) Which IP did the gateway connect to?
curl -sS -X POST ${AUTH} "https://logging.googleapis.com/v2/entries:list" \\
  -d '{"resourceNames": ["projects/PROJECT_ID"], "orderBy": "timestamp desc", "pageSize": 10,
       "filter": "${LOGF.replace(/"/g, '\\"')} AND jsonPayload.enforcedGatewaySecurityPolicy.hostname:\\"payroll-mcp\\""}' \\
  | jq '.entries[]? | {status: .httpRequest.status, serverIp: .httpRequest.serverIp}'   # expect "PSC_IP:443"`),
    gcloud: T(`gcloud logging read '${LOGF} AND jsonPayload.enforcedGatewaySecurityPolicy.hostname:"payroll-mcp"' \\
  --project=PROJECT_ID --limit=10 --freshness=1d --format='table(timestamp, httpRequest.status, httpRequest.serverIp)'
# Allowed calls show serverIp = PSC_IP:443. Denied entries have no serverIp.`),
    tf: NO("Nothing to create: this step only reads."),
    py: T(`from google.cloud import logging

client = logging.Client(project="PROJECT_ID")
flt = '${LOGF} AND jsonPayload.enforcedGatewaySecurityPolicy.hostname:"payroll-mcp"'
for e in client.list_entries(filter_=flt, order_by=logging.DESCENDING, max_results=10):
    print(e.timestamp, (e.http_request or {}).get("status"), (e.http_request or {}).get("serverIp"))`, null, true),
  },
};

/* ---------------------------------------------------------- clients (sources section) */
S.client = {
  title: "Let an app call the agent", sub: "Cloud Run app or VPC client → Agent Runtime",
  who: ["hand", "You, by hand"], time: "~1–2 min",
  what: "A client calls the agent's <code>streamQuery</code> on <code>REGION-aiplatform.googleapis.com</code> with its own credentials. <b>Who may call</b> is IAM on Agent Runtime: grant <code>roles/aiplatform.user</code> to the client's service account. If the agent is bound to an ingress gateway, Model Armor screens the prompt.",
  why: "This is how Cloud Run and VPC apps are governed today: as <i>clients</i> of a governed agent, not as gateway-bound agents themselves.",
  tabs: {
    curl: T(`# From the client (its own token; on Cloud Run use the metadata server or google-auth)
${TOKEN}
${streamQuery("Hello")}
# With the ingress gateway + Model Armor, an injection gets:
# 403 {"error": {"message": "Model Armor: Prompt violates content security configurations", ...}}`),
    gcloud: T(`# Who may call: the app's service account
gcloud projects add-iam-policy-binding PROJECT_ID --condition=None \\
  --member="serviceAccount:APP_SA@PROJECT_ID.iam.gserviceaccount.com" --role=roles/aiplatform.user`),
    tf: T(`resource "google_project_iam_member" "app_calls_agent" {
  project = "PROJECT_ID"
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:APP_SA@PROJECT_ID.iam.gserviceaccount.com"
}`),
    py: T(PYREST + `
r = s.post("https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/reasoningEngines/ENGINE_ID:streamQuery",
           params={"alt": "sse"}, stream=True,
           json={"class_method": "async_stream_query", "input": {"user_id": "app", "message": "Hello"}})
for line in r.iter_lines():
    print(line.decode())`),
  },
};

S.vpcclient = {
  title: "Reach the agent privately from a VPC", sub: "Private Google Access or PSC for REGION-aiplatform",
  who: ["hand", "You, by hand"], time: "seconds",
  what: "A VM or GKE pod without a public IP reaches Agent Runtime through <b>Private Google Access</b> on its subnet, or through a <b>PSC endpoint for Google APIs</b> plus a private DNS record for <code>REGION-aiplatform.googleapis.com</code>.",
  why: "Only <code>REGION-aiplatform.googleapis.com</code> works for a regional agent's <code>streamQuery</code> (<code>aiplatform.REGION.rep</code> doesn't resolve; the global host returns 404). Ingress Model Armor from private clients was inconsistent in testing (ISSUES #14).",
  tabs: {
    curl: T(`# From the VM: the same call as any client
${TOKEN}
${streamQuery("Hello")}`),
    gcloud: T(`# Option A: Private Google Access on the client's subnet
gcloud compute networks subnets update CLIENT_SUBNET --region=REGION --project=PROJECT_ID --enable-private-ip-google-access

# Option B: the PSC endpoint for Google APIs (as in the Private Networking steps) + a narrow private zone
gcloud dns managed-zones create aiplatform --dns-name=REGION-aiplatform.googleapis.com. \\
  --visibility=private --networks=VPC --description="Agent Runtime via PSC" --project=PROJECT_ID
gcloud dns record-sets create REGION-aiplatform.googleapis.com. --zone=aiplatform \\
  --type=A --ttl=300 --rrdatas=PSC_IP --project=PROJECT_ID`),
    tf: T(`resource "google_dns_managed_zone" "aiplatform" {
  project    = "PROJECT_ID"
  name       = "aiplatform"
  dns_name   = "REGION-aiplatform.googleapis.com."
  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.vpc.id
    }
  }
}

resource "google_dns_record_set" "aiplatform" {
  project      = "PROJECT_ID"
  managed_zone = google_dns_managed_zone.aiplatform.name
  name         = "REGION-aiplatform.googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas      = ["PSC_IP"]
}`, "The live test used gcloud; this is the same zone in Terraform.", true),
    py: T(PYREST + `
# Runs unchanged on the VM: DNS sends REGION-aiplatform.googleapis.com to the private path.
r = s.post("https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT_ID/locations/REGION/reasoningEngines/ENGINE_ID:streamQuery",
           params={"alt": "sse"}, json={"class_method": "async_stream_query", "input": {"user_id": "vm", "message": "Hello"}})
print(r.status_code)`),
  },
};

/* ============================================================== timelines */
export const TL = {
  internet: [
    { name: "Project prep", note: "APIs and identities first.", steps: ["apis", "sa"] },
    { name: "Gateway + foundation policy", note: "Default deny, plus the Google APIs every agent needs.", steps: ["egw", "iapext", "iappol", "plat", "platgrant"] },
    { name: "Destinations", note: "Deploy, then register every hostname.", steps: ["deploy", "reg"] },
    { name: "Agent", note: "Create with identity, then bind.", steps: ["agent", "agentroles", "bind"] },
    { name: "Agent policy", note: "Per-agent permission slips.", steps: ["allowa2a", "allowmcp"] },
    { name: "Optional: ingress gateway", note: "Screens calls into the agent; independent of egress.", optional: true, steps: ["igw", "bindin"] },
    { name: "Optional: Model Armor", note: "Template → roles → extension → policy.", optional: true, steps: ["matmpl", "maroles", "maext", "mapol"] },
    { name: "Verify", note: "Test and read the log.", steps: ["verify"] },
  ],
  pn: [
    { name: "Project prep", note: "Same APIs (Compute Engine and Cloud DNS matter here).", steps: ["apis", "sa", "region"] },
    { name: "Private network", note: "The route into your VPC, built before the gateway.", steps: ["vpc", "att", "psc", "dns", "saroles", "tmpl"] },
    { name: "Gateway + foundation policy", note: "Created with the template from the start.", steps: ["pngw", "iapext", "iappol", "plat", "platgrant"] },
    { name: "Destination", note: "Internal-only Cloud Run.", steps: ["privdeploy", "privreg"] },
    { name: "Agent", note: "Identity + the env var; no PSC interface.", steps: ["pnagent", "agentroles", "pnbind"] },
    { name: "Agent policy", note: "Same slips as Internet mode.", steps: ["privgrant"] },
    { name: "Verify", note: "404 → 403 → 200, and the PSC IP.", steps: ["pnverify"] },
  ],
};


export const MODEL = {
  internet: [
    { k: "agent", t: "Agent", s: "with Agent Identity", what: "Your agent on Agent Runtime. Its <b>Agent Identity</b> is its own badge, set when it's created; every grant is given to it.", why: "Everything is decided by <i>who</i> is calling, not where the call comes from." },
    { a: "→" },
    { k: "gw", t: "Agent Gateway", s: "the checkpoint", what: "A Google-managed checkpoint every call goes through. <b>Egress</b> (Agent-to-Anywhere) checks calls <i>from</i> your agent; <b>ingress</b> (Client-to-Agent) screens calls <i>to</i> it.", why: "An agent is <b>bound</b> to it; all agents in a project and region share the same gateways." },
    { a: "→" },
    { k: "reg", t: "Agent Registry", s: "the address book", what: "The address book of destinations: agents, MCP servers, APIs. Egress only reaches what's registered, matched by exact hostname.", why: "Not in the book? Denied, whatever the grants say." },
    { p: "+" },
    { k: "slip", t: "roles/iap.egressor", s: "the permission slip", what: "An agent may call a registry entry only if it holds this role on it, optionally only for certain MCP tools. <b>IAP</b>, Google's access check, decides.", why: "In the book but no slip? 403 <code>Egress request is not authorized.</code>" },
    { a: "→" },
    { k: "dest", t: "Destination", s: "A2A · MCP · API", what: "Where the call lands: an A2A agent, an MCP server (whole, or certain tools), an HTTPS API or a Google API.", why: "Optionally, Model Armor also screens MCP calls for prompt injection and sensitive data." },
    { rule: "Default deny: no slip, no entry" },
  ],
  pn: [
    { k: "gw", t: "Agent Gateway", s: "VPC-enabled", what: "The same checkpoint, created with an <b>agent connectivity template</b>: one resource holding its private-network settings (which attachment, which domains to look up in your VPC, which traffic goes in).", why: "Policy doesn't change: registry entry + egressor slip, as above." },
    { a: "→" },
    { k: "net", t: "Network attachment", s: "door into your VPC", what: "The gateway's door into your VPC: Google's side connects to it automatically, on a small /28 subnet.", why: "Same region as the gateway, or template creation fails." },
    { a: "→" },
    { k: "net", t: "PSC endpoint", s: "private IP to Google services", what: "A private IP inside your VPC that leads to Google services such as Cloud Run, so internal-only services can be reached without the internet.", why: "A private <code>run.app.</code> DNS zone (your VPC's phone book) points names at it; <b>DNS peering</b> lets the gateway use that phone book." },
    { a: "→" },
    { k: "dest", t: "Private service", s: "Cloud Run, ingress internal", what: "A service that answers 404 from the internet but accepts traffic that arrives through the PSC endpoint.", why: "Proof: the gateway log's <code>serverIp</code> is the PSC endpoint's 10.x address." },
  ],
};

export function orderHtml() {
  return `<details class="bb-card bb-order" open>
    <summary>Your order vs. the corrected order</summary>
    <div class="bb-order-grid">
      <div><div class="bb-eyebrow">Your example</div><ol>
        <li>Add foundational items to Agent Registry (existing agents, MCP servers, APIs…)</li>
        <li>Create agent gateway</li>
        <li>Add foundation policy (existing / common policy)</li>
        <li>Add agent</li>
        <li>Add agent-specific policy</li></ol></div>
      <div><div class="bb-eyebrow">Corrected (Internet)</div><ol>
        <li><b>Project prep:</b> APIs, service account, bucket, repo</li>
        <li><b>Gateway + foundation policy:</b> gateway → IAP extension → REQUEST_AUTHZ (default deny) → platform endpoints → egressor for all project agents</li>
        <li><b>Destinations:</b> deploy your A2A agents / MCP servers → register them (every hostname)</li>
        <li><b>Agent:</b> create with Agent Identity → project roles → bind (~5 min)</li>
        <li><b>Agent policy:</b> per-agent egressor grants (A2A, MCP all / named / read-only)</li>
        <li><b>Optional:</b> ingress gateway, then Model Armor; then <b>verify</b></li></ol></div>
    </div>
    <ul class="bb-order-notes">
      <li><b>Added a step 0.</b> APIs (and, for VPC, the network and the gateway's service-agent roles) have to exist before anything else can be created.</li>
      <li><b>"Foundational registry items" split in two.</b> The <i>Google APIs the agent itself needs</i> (about 25 hostnames) are true foundation: registered and granted to every agent in the project before any agent binds, or its own Gemini and session calls fail (498 / 403). Your <i>existing agents, MCP servers and APIs</i> are destinations: they need the running service first (an A2A entry is read from its agent card), so they come after the gateway in this timeline.</li>
      <li><b>Gateway vs. registry order doesn't matter technically.</b> Entries can be created before or after the gateway; what matters is that the gateway, its IAP policy and the platform entries all exist before the bind.</li>
      <li><b>"Foundation policy" is three things:</b> the IAP extension (where enforce / dry run lives), the REQUEST_AUTHZ policy that turns on default deny, and the all-agents grant on the platform entries.</li>
      <li><b>"Add agent" is three steps.</b> Create it with Agent Identity (it can't be added later), give it project roles, then bind it. Binding is when default deny starts for that agent, so it moved after all the foundation.</li>
      <li><b>Agent-specific policy stays last</b>: it needs the agent's principal and the destination's entry. Allow about 2 minutes per grant.</li>
    </ul>
  </details>`;
}

export function pnIntroHtml() {
  return `<div class="bb-card" style="margin-bottom:18px">
    <h2>What's different with Private Networking</h2>
    <ul class="bb-order-notes" style="margin-top:4px">
      <li><b>The private network is built before the gateway</b>, and the gateway is created <i>with</i> its connectivity template. Adding a template to an existing gateway needs two PATCHes (ISSUES #15), and the template is frozen once in use.</li>
      <li><b>Service-agent roles before the template and gateway:</b> Google's gateway identity needs to use your attachment and read your DNS.</li>
      <li><b>Policy is unchanged.</b> A route into the VPC isn't permission: the destination still needs a registry entry and <code>roles/iap.egressor</code>.</li>
      <li><b>Not in this mode:</b> the ingress gateway and Model Armor (the Internet steps would apply; <span class="bb-unv">unverified</span> in the private region).</li>
    </ul>
  </div>`;
}


export function sourcesHtml() {
  const rows = [
    ["A2A agent", "<code>agentSpec: A2A_AGENT_CARD</code> (from its agent card)", "egressor on <code>agents/ID</code>, no condition", "Verified", "Egress Model Armor does <b>not</b> screen A2A messages (ISSUES #2).", "allowa2a"],
    ["MCP server, all tools", "<code>mcpServerSpec: TOOL_SPEC</code> + every URL", "egressor on <code>mcpServers/ID</code>, no condition", "Verified", "Model Armor screens <code>tools/call</code>.", "allowmcp"],
    ["MCP server, specific tools", "same", "condition <code>mcp.toolName in ['get_ticket', '']</code>", "Verified", "The <code>''</code> keeps <code>initialize</code> / <code>tools/list</code> working.", "allowmcp"],
    ["MCP server, read-only tools", "same, with <code>readOnlyHint</code> in the tool spec", "condition <code>mcp.tool.isReadOnly == true || toolName == ''</code>", "Verified", "No tool names in the policy; follows the registered annotations.", "allowmcp"],
    ["HTTPS API endpoint", "<code>endpointSpec: NO_SPEC</code> + <code>interfaces[].url</code>", "egressor on <code>endpoints/ID</code>", "Pattern verified (platform endpoints)", "Exact host match; no wildcards.", "reg"],
    ["Google APIs", "~25 platform endpoints (plain, .mtls, regional, .rep, :443)", "egressor for the <b>project principal set</b> (all agents)", "Verified", "Without these, a bound agent can't reach Gemini or sessions (498 / 403).", "platgrant"],
    ["Private Cloud Run in the VPC", "MCP or endpoint entry with <b>both</b> run.app URLs", "same grants as public; gateway needs a connectivity template", "Verified (us-west1)", "Proof: gateway log <code>serverIp</code> = PSC IP.", "privgrant"],
  ];
  return `
  <p class="bb-lede">Who is calling decides how the call is governed. Agent Gateway governs the <b>outbound</b> calls of agents bound to it, and screens <b>inbound</b> calls to Agent Runtime agents. Apps that aren't Agent Runtime agents are governed as <i>clients</i> of an agent.</p>
  <div class="bb-src-grid">
    <div class="bb-card bb-src">
      <h3>Agent Runtime agent <span class="bb-verdict bb-v-yes">Governed: egress</span></h3>
      <p class="bb-muted bb-small" style="margin:0">The verified path in this demo.</p>
      <ul>
        <li>Created with <b>Agent Identity</b>, bound to an <code>AGENT_TO_ANYWHERE</code> gateway.</li>
        <li>Every call: registry entry + <code>roles/iap.egressor</code> on it, or 403.</li>
        <li>Per-tool MCP policy; Model Armor on MCP traffic.</li>
        <li>Calls <i>into</i> it: ingress gateway (Model Armor) + <code>roles/aiplatform.user</code>.</li>
      </ul>
      <div class="bb-step-actions" style="margin-top:8px"><button class="bb-btn bb-btn-primary bb-show" data-step="bind">Show code: bind</button></div>
    </div>
    <div class="bb-card bb-src">
      <h3>Cloud Run app <span class="bb-verdict bb-v-part">Governed as a client</span></h3>
      <ul>
        <li>Calling an agent, it's a <b>client</b>: the <code>CLIENT_TO_AGENT</code> ingress gateway applies, <b>Model Armor only</b>.</li>
        <li>The gateway doesn't check the caller's identity. <b>Who may call</b> is Agent Runtime IAM: <code>roles/aiplatform.user</code> for the app's service account.</li>
        <li>Its <i>own</i> outbound calls don't pass Agent Gateway: use Cloud Run IAM (<code>run.invoker</code>), VPC egress controls and firewalls.</li>
      </ul>
      <div class="bb-step-actions" style="margin-top:8px"><button class="bb-btn bb-btn-primary bb-show" data-step="client">Show code</button></div>
    </div>
    <div class="bb-card bb-src">
      <h3>Private app in a VPC <span class="bb-verdict bb-v-part">Client, with a caveat</span></h3>
      <ul>
        <li>Same as the Cloud Run app: a client of the agent, IAM decides who may call.</li>
        <li>Private access: <b>Private Google Access</b> or a <b>PSC endpoint</b> for <code>REGION-aiplatform.googleapis.com</code> (the only host that works).</li>
        <li><span class="bb-unv">caveat</span> Live testing found ingress Model Armor <b>inconsistent</b> for private clients: about 50–70% of identical injections blocked, some unbound agents screened too (ISSUES #14). Don't present it as a guarantee.</li>
      </ul>
      <div class="bb-step-actions" style="margin-top:8px"><button class="bb-btn bb-btn-primary bb-show" data-step="vpcclient">Show code</button></div>
    </div>
  </div>

  <div class="bb-card bb-callout" style="margin-bottom:16px">
    <h3>Can Cloud Run, GKE or VM <i>agents</i> be governed for egress by Agent Gateway? <span class="bb-unv">documented, not tested in this demo</span></h3>
    <p><b>What the docs say (October 2026):</b> the Agent Gateway overview lists two supported runtimes, <b>Agent Runtime</b> (egress and ingress) and <b>Gemini Enterprise</b> (egress only), and the deployment guide says the same ("governs traffic from agents running on Gemini Enterprise and Runtime"). The VPC connectivity guide tells you to set <code>deploymentModel: CENTRALIZED</code>.</p>
    <p><b>What the API has:</b> the <code>agentConnectivityTemplate</code> resource also accepts <code>agentCompute: GKE | CLOUD_RUN | GCE_VM</code> and <code>deploymentModel: AMBIENT</code> (Terraform exposes them as <code>agent_compute</code> and <code>deployment_model</code> from hashicorp/google 8.7.0), and the gateway has a <code>self_managed</code> alternative to <code>google_managed</code>. No public guide explains how a Cloud Run, GKE or VM workload would attach to a gateway through them, and nothing documents identity, registry or IAP behaviour for such workloads.</p>
    <p><b>So, today:</b> treat Cloud Run, GKE and VM agents as <b>not governed for egress</b> by Agent Gateway. Govern them as clients of Agent Runtime agents (above), or move the agent to Agent Runtime. The AMBIENT / agentCompute path looks like a future option; it would need its own test before the demo shows it.</p>
    <p class="bb-small bb-muted">Sources: docs.cloud.google.com → gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview ("Supported agent runtimes"), …/set-up-agent-gateway ("Select your runtime"), …/set-up-vpc-connectivity (template fields); TERRAFORM_PLAN §1.1 for the provider fields.</p>
  </div>

  <h2>Agent Runtime agent → destination, with its policy</h2>
  <div class="bb-tbl-wrap"><table>
    <thead><tr><th>Destination</th><th>Register as</th><th>Policy (grant to the agent's principal)</th><th>Status</th><th>Note</th><th><span class="bb-sr">Code</span></th></tr></thead>
    <tbody>${rows.map(r => `<tr><td><b>${r[0]}</b></td><td>${r[1]}</td><td>${r[2]}</td><td>${r[3]}</td><td>${r[4]}</td>
      <td><button class="bb-btn bb-show" data-step="${r[5]}" aria-label="Show code for ${esc(r[0])}">Code</button></td></tr>`).join("")}</tbody>
  </table></div>
  <div class="bb-section-gap"></div>
  <h2>Other sources → the same destinations</h2>
  <div class="bb-tbl-wrap"><table>
    <thead><tr><th>Source</th><th>Calling an agent</th><th>Calling A2A / MCP / APIs directly</th><th>Status</th></tr></thead>
    <tbody>
      <tr><td><b>Cloud Run app</b></td><td>Ingress gateway (Model Armor) + <code>roles/aiplatform.user</code></td><td>Not via Agent Gateway: <code>run.invoker</code>, network controls</td><td>Ingress path verified from the demo backend</td></tr>
      <tr><td><b>VPC app (VM / GKE)</b></td><td>Same, over PGA or PSC to <code>REGION-aiplatform.googleapis.com</code></td><td>Not via Agent Gateway: firewall, Secure Web Proxy, IAM on the target</td><td>Reached the ingress gateway; Model Armor inconsistent (#14)</td></tr>
      <tr><td><b>Gemini Enterprise app</b></td><td>Calls the agent directly (an ADK registration)</td><td>Egress gateway supported per the docs</td><td><span class="bb-unv">unverified</span> here; the demo binds only Agent Runtime agents</td></tr>
    </tbody>
  </table></div>`;
}

