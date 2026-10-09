// In-browser mock of the backend (CONTRACTS §7). Enabled with VITE_MOCK=1 or ?mock=1.
// Simulates §3 rules, Live-mode propagation delays ("pending") and SSE test runs.
import type { Api } from "./client";
import { evaluate } from "./simulate";
import { agentName, edgeLabel, policySource, splitSource } from "../util";
import { mockTerraform } from "./mockTerraform";
import helpdeskJson from "./mockTheme.helpdesk.json";
import retailJson from "./mockTheme.retail.json";
import type { AppConfig, EdgeState, GatewayLogEntry, GatewayLogsResponse, Mode, Policy, PolicyStatus, RegistryAccess, RegistryItem, RegistryView, SseEvent, TerraformExport, Theme, ThemeState } from "./types";

// Regenerate (repo root): uv run python -c "import json; from agdemo_core.themes import load_theme; [json.dump(load_theme(t).model_dump(mode='json'), open(f'ui/frontend/src/api/mockTheme.{t}.json','w'), indent=1) for t in ['helpdesk','retail']]"
const QS = new URLSearchParams(location.search);
// ?typical=N overrides every policy's typical apply/remove time (seconds) so the progress bar and the
// "taking longer than usual" note can be seen within the mock's short pending window.
const TYPICAL_OVERRIDE = Number(QS.get("typical")) || 0;
function withTypical(t: Theme): Theme {
  if (!TYPICAL_OVERRIDE) return t;
  return { ...t, policies: t.policies.map((p) => ({ ...p, typical_seconds: TYPICAL_OVERRIDE, typical_remove_seconds: TYPICAL_OVERRIDE })) };
}
const THEMES: Record<string, Theme> = {
  helpdesk: withTypical(helpdeskJson as unknown as Theme),
  retail: withTypical(retailJson as unknown as Theme),
};
// Live-mode propagation delay in the mock (?pending=<seconds>, default 15 s so the timers are visible).
const LIVE_DELAY_MS = (Number(QS.get("pending")) || 15) * 1000;
const MA_TYPICAL_SECONDS = TYPICAL_OVERRIDE || 240;

interface MaState {
  modelArmor: boolean;
  maPending: { target: boolean; until: number; at: string } | null;
  maChangedAt: string | null;
}
interface Store extends MaState {
  applied: Set<string>;
  pending: Map<string, { target: boolean; until: number; at: string }>;
  changedAt: Map<string, string>;
}
// Model Armor is global (all themes); policy state is per theme and per mode family.
const ma: Record<"demo" | "live", MaState> = {
  demo: { modelArmor: false, maPending: null, maChangedAt: null },
  live: { modelArmor: false, maPending: null, maChangedAt: null },
};
const stores = new Map<string, Store>();
const fam = (mode: Mode) => (mode === "demo" ? "demo" : "live");
function storeFor(mode: Mode, themeId: string): Store {
  const k = `${fam(mode)}:${themeId}`;
  let st = stores.get(k);
  if (!st) {
    const m = ma[fam(mode)];
    st = {
      applied: new Set(),
      pending: new Map(),
      changedAt: new Map(),
      get modelArmor() {
        return m.modelArmor;
      },
      set modelArmor(v: boolean) {
        m.modelArmor = v;
      },
      get maPending() {
        return m.maPending;
      },
      set maPending(v) {
        m.maPending = v;
      },
      get maChangedAt() {
        return m.maChangedAt;
      },
      set maChangedAt(v) {
        m.maChangedAt = v;
      },
    };
    stores.set(k, st);
  }
  return st;
}

function settle(s: Store) {
  const now = Date.now();
  for (const [pid, p] of s.pending) {
    if (now >= p.until) {
      if (p.target) s.applied.add(pid);
      else s.applied.delete(pid);
      s.pending.delete(pid);
    }
  }
  if (s.maPending && now >= s.maPending.until) {
    s.modelArmor = s.maPending.target;
    s.maPending = null;
  }
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((res, rej) => {
    const t = setTimeout(res, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      rej(new DOMException("aborted", "AbortError"));
    });
  });

function getThemeOrThrow(id: string): Theme {
  const t = THEMES[id];
  if (!t) throw new Error(`unknown theme ${id}`);
  return t;
}

function policyStatus(s: Store, pid: string): PolicyStatus {
  const p = s.pending.get(pid);
  if (p) return { applied: p.target, status: p.target ? "pending" : "pending_removal", detail: "Waiting for the change to propagate (mock)", changed_at: p.at };
  const applied = s.applied.has(pid);
  return { applied, status: applied ? "applied" : "removed", detail: "", changed_at: s.changedAt.get(pid) ?? null };
}

/** Policies that a given edge depends on (to decide "pending" edges in live mock). */
function edgeTouchedByPending(theme: Theme, s: Store, edge: string): boolean {
  for (const pid of s.pending.keys()) {
    const p = theme.policies.find((x) => x.id === pid);
    if (!p) continue;
    const isIngress = edge.startsWith("ingress:");
    // CONTRACTS §12: a policy only touches the edges of its own orchestrator (params.source).
    const [src, base] = splitSource(edge);
    if (!isIngress && policySource(p) !== src) continue;
    if (p.type === "gateway_attach") {
      if ((p.params.path === "ingress") === isIngress) return true;
    } else if (base === p.params.target || base.startsWith(`${p.params.target}:`)) return true;
  }
  return false;
}

function stateFor(theme: Theme, mode: Mode): ThemeState {
  const s = storeFor(mode, theme.id);
  settle(s);
  const policies: Record<string, PolicyStatus> = {};
  for (const p of theme.policies) policies[p.id] = policyStatus(s, p.id);
  const intended = new Set(s.applied);
  for (const [pid, p] of s.pending) p.target ? intended.add(pid) : intended.delete(pid);
  const expected = evaluate(theme, intended, s.maPending?.target ?? s.modelArmor, null, "simulated");
  let edges: Record<string, EdgeState>;
  if (mode === "demo") edges = expected;
  else {
    edges = evaluate(theme, s.applied, s.modelArmor, null, "live");
    for (const e of Object.keys(edges)) if (edgeTouchedByPending(theme, s, e)) edges[e] = { state: "pending", governed: true, source: "live", detail: "Policy change propagating" };
  }
  const gw = (path: string) => {
    const pid = theme.policies.find((p) => p.type === "gateway_attach" && p.params.path === path && !policySource(p))?.id ?? "";
    const st = policyStatus(s, pid);
    return { attached: st.applied, status: st.status };
  };
  return {
    policies,
    gateways: { egress: gw("egress"), ingress: gw("ingress") },
    model_armor: {
      enabled: s.maPending?.target ?? s.modelArmor,
      status: s.maPending ? "pending" : s.modelArmor ? "applied" : "removed",
      detail: s.maPending ? "Updating gateways (mock)" : "",
      typical_seconds: MA_TYPICAL_SECONDS,
      changed_at: s.maPending?.at ?? s.maChangedAt,
    },
    edges,
    expected,
  };
}

const delay = <T>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

const principalTail = (t: Theme, p: Theme["policies"][number]) => policySource(p) ?? t.orchestrator.id;
const EXPLAIN: Record<string, (p: Theme["policies"][number], t: Theme) => string[]> = {
  gateway_attach: (p, t) =>
    p.params.path === "egress"
      ? [
          `# Bind ${agentName(t, policySource(p))}'s Agent Runtime engine to the egress (AGENT_TO_ANYWHERE) gateway`,
          `PATCH https://us-east4-aiplatform.googleapis.com/v1beta1/projects/demo/locations/us-east4/reasoningEngines/${engineNum(t, policySource(p))}?updateMask=spec.deploymentSpec.agentGatewayConfig`,
          '{"spec":{"deploymentSpec":{"agentGatewayConfig":{"agentToAnywhereConfig":{"agentGateway":"projects/demo/locations/us-east4/agentGateways/agdemo-egress"}}}}}',
          "# Default deny: only platform endpoints (Gemini, logging, trace, sessions) are allowlisted",
        ]
      : [
          "# Front the engine with the ingress (CLIENT_TO_AGENT) gateway",
          "PATCH .../reasoningEngines/123?updateMask=spec.deploymentSpec.agentGatewayConfig",
          '{"spec":{"deploymentSpec":{"agentGatewayConfig":{"clientToAgentConfig":{"agentGateway":".../agentGateways/agdemo-ingress"}}}}}',
        ],
  a2a_allow: (p, t) => [
    `# Grant ${agentName(t, policySource(p))}'s agent identity egress to ${p.params.target}'s registry endpoint`,
    `gcloud beta iap web add-iam-policy-binding --resource-type=agent-registry-endpoint \\`,
    `  --endpoint=agdemo-${t.id}-${p.params.target} --region=us-east4 \\`,
    `  --member=principal://agents.global.org-123.system.id.goog/.../${principalTail(t, p)} --role=roles/iap.egressor`,
  ],
  mcp_server_allow: (p, t) => [
    `# Egressor on the whole MCP server ${p.params.target} for ${agentName(t, policySource(p))}`,
    `gcloud beta iap web add-iam-policy-binding --endpoint=agdemo-${t.id}-${p.params.target} \\`,
    `  --member=principal://.../${principalTail(t, p)} --role=roles/iap.egressor`,
  ],
  mcp_tool_allow: (p, t) => [
    `# Conditional egressor binding on ${p.params.target} for ${agentName(t, policySource(p))}: read-only tools only`,
    `gcloud beta iap web add-iam-policy-binding --endpoint=agdemo-${t.id}-${p.params.target} \\`,
    `  --member=principal://.../${principalTail(t, p)} --role=roles/iap.egressor \\`,
    `  --condition='expression=request.mcp.tool.annotations.readOnlyHint == true,title=read-only-tools'`,
  ],
};

function fakeResult(theme: Theme, fullEdge: string): string {
  if (fullEdge.startsWith("ingress:")) return `${theme.orchestrator.display_name} answered the user.`;
  const [src, edge] = splitSource(fullEdge);
  const who = src ? `${agentName(theme, src)} → ` : "";
  if (!edge.includes(":")) return `${who}${theme.a2a_agents[edge]?.display_name ?? edge}: (simulated answer)`;
  const [srv, tool] = edge.split(":");
  if (src) return `${who}${srv}.${tool} → 200 OK (simulated result)`;
  return `${srv}.${tool} → 200 OK ${tool.startsWith("get") || tool.startsWith("list") || tool.startsWith("lookup") ? "(INC-1042: VPN drops every 30 minutes)" : "(change made!)"}`;
}

// ---------- gateway logs (CONTRACTS §9) ----------
// Synthesized from governed egress edge results (tests and probes), revealed 3–8 s later to mimic
// Cloud Logging ingestion latency. Shape mirrors ui/backend/agdemo_ui/gateway_logs.py.
const GW_POLICY: Record<string, string> = { denied: "agdemo-egress-iap-policy", blocked: "agdemo-egress-ma-policy" };
const GW_PROJECT = "my-demo-project";
const GW_PROJECT_NUM = "123456789012";
const GW_FILTER = (themeId: string) =>
  `logName="projects/${GW_PROJECT}/logs/networkservices.googleapis.com%2Fgateway_requests" resource.type="networkservices.googleapis.com/Gateway" resource.labels.gateway_name="agdemo-egress" httpRequest.requestUrl:"agdemo-${themeId}-"`;
const consoleQueryUrl = (filter: string) =>
  `https://console.cloud.google.com/logs/query;query=${encodeURIComponent(filter)}?project=${GW_PROJECT}`;
const gwBook = new Map<string, { visibleAt: number; e: GatewayLogEntry }[]>();
const hex = (n: number) => Array.from({ length: n }, () => Math.floor(Math.random() * 36).toString(36)).join("");

// ---------- Agent Registry view (CONTRACTS §10) ----------
// Mirrors agdemo_core/gcp/registry_view.simulate: entries from the theme, bindings from the applied policies.
/** Mock reasoning-engine number per orchestrator (primary: fixed per theme; additional: derived from the id). */
function engineNum(theme: Theme, source: string | null = null): string {
  if (!source) return theme.id === "helpdesk" ? "1234567890123456789" : "9876543210987654321";
  let h = 7;
  for (const ch of theme.id + source) h = (h * 131 + ch.charCodeAt(0)) % 1_000_000_007;
  return `55${String(h).padStart(17, "0")}`.slice(0, 19);
}
const REG_PRINCIPAL = (theme: Theme, source: string | null = null) =>
  `principal://agents.global.org-000000000000.system.id.goog/resources/aiplatform/projects/${GW_PROJECT_NUM}/locations/us-east4/reasoningEngines/${engineNum(theme, source)}`;
const REG_PROJECT_SET = `principalSet://agents.global.org-000000000000.system.id.goog/attribute.platformContainer/aiplatform/projects/${GW_PROJECT_NUM}`;
const REG_PLATFORM_HOSTS = ["us-east4-aiplatform.googleapis.com", "aiplatform.googleapis.com", "agentregistry.googleapis.com", "logging.googleapis.com", "telemetry.googleapis.com", "cloudtrace.googleapis.com", "monitoring.googleapis.com", "cloudresourcemanager.googleapis.com", "iamcredentials.googleapis.com", "secretmanager.googleapis.com"];
const regId = (seed: string) => {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const x = h.toString(16).padStart(8, "0");
  return `agentregistry-00000000-0000-0000-${x.slice(0, 4)}-${x}${x.slice(0, 4)}`;
};

function mockCondition(theme: Theme, p: Policy): { title: string; expression: string } | null {
  if (p.type !== "mcp_tool_allow") return null;
  const title = `agdemo ${theme.id} ${p.id}`;
  const target = String(p.params.target);
  if (p.params.read_only && p.params.use_annotation)
    return { title, expression: "api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true || api.getAttribute('iap.googleapis.com/mcp.toolName', '') == ''" };
  const tools = p.params.read_only ? (theme.mcp_servers[target]?.tools ?? []).filter((t) => t.read_only).map((t) => t.name) : ((p.params.tools as string[]) ?? []);
  return { title, expression: `api.getAttribute('iap.googleapis.com/mcp.toolName', '') in [${[...tools, ""].map((t) => `'${t}'`).join(", ")}]` };
}

function mockRegistry(theme: Theme, applied: Set<string>, live: boolean): RegistryView {
  const principal = REG_PRINCIPAL(theme);
  // CONTRACTS §12: each orchestrator has its own Agent Identity principal; allows with params.source bind that one.
  const access = (cid: string): RegistryAccess[] =>
    theme.policies
      .filter((p) => ["a2a_allow", "mcp_server_allow", "mcp_tool_allow"].includes(p.type) && p.params.target === cid && applied.has(p.id))
      .map((p) => ({
        member: REG_PRINCIPAL(theme, policySource(p)),
        member_label: `${agentName(theme, policySource(p))} (Agent Identity)`,
        member_kind: "orchestrator",
        role: "roles/iap.egressor",
        condition: mockCondition(theme, p),
        policy_id: p.id,
        policy_text: p.text,
        note: null,
      }));
  const base = { label: undefined, protocol: null, card: null, access_error: null, error: null };
  const res = (kind: string, id: string) => `projects/${GW_PROJECT}/locations/us-east4/${kind}/${id}`;
  const host = (cid: string) => `https://agdemo-${theme.id}-${cid}-${GW_PROJECT_NUM}.us-east4.run.app`;
  const agents: RegistryItem[] = theme.components
    .filter((c) => c.kind === "a2a_agent")
    .map((c) => {
      const a = theme.a2a_agents[c.id];
      const skills = a.skills.map((s) => ({ id: s.id, name: s.name, description: s.description, tags: (s as { tags?: string[] }).tags ?? [] }));
      const rid = regId(theme.id + c.id);
      return { ...base, id: c.id, kind: "a2a_agent", display_name: a.display_name, resource: res("agents", rid), registry_id: rid, description: a.description, url: host(c.id), protocol: "A2A_AGENT", card: { name: a.display_name, description: a.description, version: "1.0.0", protocol_version: "1.0", skills }, skills, access: access(c.id) };
    });
  const orid = regId(theme.id + "orch");
  agents.push({ ...base, id: "orchestrator", kind: "orchestrator", label: "auto-registered (Agent Runtime)", display_name: `agdemo-${theme.id}-orchestrator`, resource: res("agents", orid), registry_id: orid, description: theme.orchestrator.description ?? "", url: `https://us-east4-aiplatform.googleapis.com/v1/projects/${GW_PROJECT_NUM}/locations/us-east4/reasoningEngines/${engineNum(theme)}:query`, protocol: "CUSTOM", skills: [], access: [] });
  for (const o of theme.additional_orchestrators ?? []) {
    const rid = regId(theme.id + "orch" + o.id);
    agents.push({ ...base, id: `orchestrator:${o.id}`, kind: "orchestrator", label: "auto-registered (Agent Runtime)", display_name: `agdemo-${theme.id}-${o.id}`, resource: res("agents", rid), registry_id: rid, description: o.description ?? "", url: `https://us-east4-aiplatform.googleapis.com/v1/projects/${GW_PROJECT_NUM}/locations/us-east4/reasoningEngines/${engineNum(theme, o.id)}:query`, protocol: "CUSTOM", skills: [], access: [] });
  }
  const mcp_servers: RegistryItem[] = theme.components
    .filter((c) => c.kind === "mcp_server")
    .map((c) => {
      const m = theme.mcp_servers[c.id];
      const rid = regId(theme.id + c.id);
      const tools = m.tools.map((t) => ({ name: t.name, description: t.description, read_only: t.read_only, annotations: t.read_only ? { readOnlyHint: true, idempotentHint: true } : { destructiveHint: true } }));
      return { ...base, id: c.id, kind: "mcp_server", display_name: `agdemo ${theme.id} ${m.display_name}`, resource: res("mcpServers", rid), registry_id: rid, description: m.description ?? "", url: `${host(c.id)}/mcp`, tools, access: access(c.id) };
    });
  const items: RegistryItem[] = REG_PLATFORM_HOSTS.map((h) => {
    const rid = regId(h);
    return { ...base, id: `https://${h}`, kind: "endpoint", display_name: `agdemo ${h}`, resource: res("endpoints", rid), registry_id: rid, description: "agdemo platform endpoint (default-deny allowlist)", url: `https://${h}`, access: [{ member: REG_PROJECT_SET, member_label: "All agents in project (Agent Identity)", member_kind: "project_agents", role: "roles/iap.egressor", condition: null, policy_id: null, policy_text: null, note: "platform allowlist (./agdemo bootstrap)" }] };
  });
  return {
    theme: theme.id,
    source: live ? "live" : "simulated",
    fetched_at: new Date().toISOString(),
    console_url: `https://console.cloud.google.com/agent-platform/agent-registry?project=${GW_PROJECT}`,
    orchestrator: { display_name: theme.orchestrator.display_name, principal },
    agents,
    mcp_servers,
    endpoints: { count: items.length, items },
  };
}

function synthEntry(theme: Theme, edge: string, decision: "allowed" | "denied" | "blocked", ts: number): GatewayLogEntry {
  const [comp, tool] = splitSource(edge)[1].split(":");
  const host = `agdemo-${theme.id}-${comp}-${GW_PROJECT_NUM}.us-east4.run.app`;
  const status = decision === "allowed" ? 200 : 403;
  const url = tool ? `https://${host}/mcp` : `https://${host}/`;
  const polName = (n: string) => `projects/${GW_PROJECT_NUM}/locations/us-east4/authzPolicies/${n}`;
  const pols = [{ name: polName(GW_POLICY.denied), result: decision === "denied" ? "DENIED" : "ALLOWED" }];
  if (decision === "blocked") pols.push({ name: polName(GW_POLICY.blocked), result: "DENIED" });
  const insertId = hex(14);
  const iso = new Date(ts).toISOString();
  const raw = {
    httpRequest: { latency: `${(0.02 + Math.random() * 0.25).toFixed(6)}s`, protocol: "HTTP/1.1", requestMethod: "POST", requestUrl: url, status, userAgent: "python-httpx2/2.13.1" },
    insertId,
    jsonPayload: {
      "@type": "type.googleapis.com/google.cloud.loadbalancing.type.LoadBalancerLogEntry",
      agentGatewayInfo: {
        agentRegistryResource: `projects/${GW_PROJECT_NUM}/locations/us-east4/${tool ? "mcpServers" : "agents"}/agentregistry-${theme.id}-${comp}`,
        ...(tool ? { mcpInfo: { method: "tools/call", parameter: tool } } : {}),
      },
      authzPolicyInfo: { policies: pols, result: decision === "allowed" ? "ALLOWED" : "DENIED" },
      enforcedGatewaySecurityPolicy: { hostname: host, matchedRules: [{ action: "ALLOWED", name: "default_denied" }], requestWasTlsIntercepted: true },
    },
    logName: `projects/${GW_PROJECT}/logs/networkservices.googleapis.com%2Fgateway_requests`,
    resource: { labels: { gateway_name: "agdemo-egress", gateway_type: "SECURE_WEB_GATEWAY", location: "us-east4" }, type: "networkservices.googleapis.com/Gateway" },
    severity: status === 200 ? "INFO" : "WARNING",
    timestamp: iso,
  };
  const denying = GW_POLICY[decision] ?? null;
  const what = tool ? `tools/call ${tool}` : "POST /";
  return {
    id: insertId,
    timestamp: iso,
    gateway: "agdemo-egress",
    decision,
    status,
    method: "POST",
    url,
    host,
    mcp_method: tool ? "tools/call" : null,
    mcp_tool: tool ?? null,
    edge,
    component: comp,
    policies: pols.map((p) => ({ name: p.name.split("/").pop()!, kind: p.name.includes("-ma-") ? "model_armor" : "iap", result: p.result })),
    decided_by: denying,
    summary: `${decision.toUpperCase()}${denying ? ` by ${denying}` : ""} · ${what} · ${status}`,
    console_url: consoleQueryUrl(`insertId="${insertId}" timestamp="${iso}"`),
    simulated: true,
    raw,
  };
}

/** Record gateway log entries for every governed egress edge result (allowed / denied / blocked). */
function logEdges(theme: Theme, edges: Record<string, EdgeState>) {
  const book = gwBook.get(theme.id) ?? [];
  gwBook.set(theme.id, book);
  const now = Date.now();
  for (const [edge, st] of Object.entries(edges)) {
    if (edge.startsWith("ingress:") || !st?.governed) continue; // the ingress gateway writes no request logs
    if (st.state !== "allowed" && st.state !== "denied" && st.state !== "blocked") continue;
    book.push({ visibleAt: now + 3000 + Math.random() * 5000, e: synthEntry(theme, edge, st.state, now) });
  }
  if (book.length > 500) book.splice(0, book.length - 500);
}

async function* runEvents(themeId: string, testId: string, scenarioId: string | undefined, mode: Mode, useLlm: boolean, signal?: AbortSignal): AsyncGenerator<SseEvent> {
  const t = getThemeOrThrow(themeId);
  const sc = t.scenarios.find((s) => s.id === scenarioId && s.tests.some((x) => x.id === testId)) ?? t.scenarios.find((s) => s.tests.some((x) => x.id === testId));
  const test = sc?.tests.find((x) => x.id === testId);
  if (!test) throw new Error(`unknown test ${testId}`);
  const s = storeFor(mode, themeId);
  settle(s);
  let source: EdgeState["source"] = mode === "demo" ? "simulated" : "live";
  const anyPending = test.probes.some((p) => edgeTouchedByPending(t, s, p.edge)) || !!s.maPending;
  let applied = s.applied;
  let ma = s.modelArmor;
  if (mode === "live_with_fallback" && anyPending) {
    yield { type: "fallback", reason: "Policy change still propagating — replaying a recorded run" };
    source = "replayed";
    applied = new Set(s.applied);
    for (const [pid, p] of s.pending) p.target ? applied.add(pid) : applied.delete(pid);
    ma = s.maPending?.target ?? s.modelArmor;
  } else if (mode === "demo") {
    source = "simulated";
  }
  const result = evaluate(t, applied, ma, test, source);
  const ingress = sc?.flow === "ingress" || test.probes.some((p) => p.edge.startsWith("ingress:"));
  // CONTRACTS §12: probes are grouped per orchestrator; a natural-language run goes to the test's agent.
  const agents = [...new Set(test.probes.filter((p) => !p.edge.startsWith("ingress:")).map((p) => splitSource(p.edge)[0]))];
  const who = useLlm || agents.length <= 1 ? agentName(t, test.agent ?? agents[0] ?? null) : agents.map((a) => agentName(t, a)).join(" and ");
  yield {
    type: "status",
    text: ingress
      ? `Sending prompt to ${t.orchestrator.display_name} through its Agent Runtime endpoint…`
      : `Sending prompt to ${who} via Agent Runtime${useLlm ? " (Gemini)" : " (probe mode)"}…`,
  };
  yield { type: "message", role: "user", text: test.prompt };
  await sleep(700, signal);
  const done: Record<string, EdgeState> = {};
  for (const p of test.probes) {
    const [psrc, pbase] = splitSource(p.edge);
    const kind = p.edge.startsWith("ingress:") ? "ingress gateway" : pbase.includes(":") ? "MCP tool" : "A2A agent";
    yield { type: "status", text: `${psrc ? `${agentName(t, psrc)}: c` : "C"}alling ${kind} ${pbase}…` };
    await sleep(600 + Math.random() * 500, signal);
    let st = result[p.edge];
    if (mode === "live" && edgeTouchedByPending(t, s, p.edge)) {
      st = { state: "pending", governed: true, source: "live", detail: "Policy change still propagating; result may change" };
    }
    done[p.edge] = st;
    logEdges(t, { [p.edge]: st });
    yield { type: "edge", edge: p.edge, state: st };
    const text =
      st.state === "denied"
        ? `${edgeLabel(t, p.edge)} → ${st.http_status ?? 403} denied by Agent Gateway`
        : st.state === "blocked"
          ? p.edge.startsWith("ingress:")
            ? `${p.edge} → ${st.http_status ?? 403} — Model Armor on the ingress gateway blocked the prompt`
            : `${edgeLabel(t, p.edge)} → blocked by Model Armor`
          : st.state === "pending"
            ? `${edgeLabel(t, p.edge)} → policy change pending`
            : test.sample_replies?.[p.edge]
              ? `${edgeLabel(t, p.edge)} replied: ${test.sample_replies[p.edge]}`
              : fakeResult(t, p.edge);
    yield { type: "message", role: "tool", text };
  }
  await sleep(500, signal);
  const states = Object.values(done).map((d) => d.state);
  const summary = states.includes("blocked")
    ? "I can't help with that — the request was blocked by Model Armor (prompt injection / sensitive data)."
    : states.every((x) => x === "denied")
      ? "Access was blocked by policy: Agent Gateway denied the call(s)."
      : states.includes("denied")
        ? "I completed part of the request; some agents or tools were blocked by policy."
        : ingress
          ? `${t.orchestrator.display_name}: (simulated answer to your request)`
          : "Done — all calls succeeded.";
  yield { type: "message", role: "agent", text: summary };
  yield { type: "done", edges: done };
}

export const mockApi: Api = {
  isMock: true,
  async getConfig(): Promise<AppConfig> {
    return delay({
      themes: Object.values(THEMES).map((t) => ({ id: t.id, name: t.name, description: t.description, deployed: t.id === "helpdesk" })),
      default_theme: "helpdesk",
      default_mode: "live_with_fallback",
      environment: { project_id: "my-demo-project", region: "us-east4", prefix: "agdemo" },
      live_available: QS.get("live") !== "0",
      live_unavailable_reason: QS.get("live") === "0" ? "mock: ?live=0 (no GCP credentials)" : "",
      can_admin: QS.get("admin") !== "0",
      // ?ge=0 simulates a deployment without a Gemini Enterprise app configured.
      gemini_enterprise:
        QS.get("ge") === "0"
          ? { app_url: "", configured: false }
          : { app_url: "https://vertexaisearch.cloud.google.com/home/cid/mock-gemini-enterprise-app", configured: true },
    });
  },
  async getTheme(id) {
    return delay({ theme: getThemeOrThrow(id) });
  },
  async getState(id, mode) {
    return delay(stateFor(getThemeOrThrow(id), mode));
  },
  async setPolicy(id, pid, action, mode) {
    getThemeOrThrow(id);
    const s = storeFor(mode, id);
    settle(s);
    const at = new Date().toISOString();
    s.changedAt.set(pid, at);
    const target = action === "apply";
    if (mode === "demo") {
      target ? s.applied.add(pid) : s.applied.delete(pid);
      s.pending.delete(pid);
    } else if (s.applied.has(pid) !== target || s.pending.has(pid)) {
      s.pending.set(pid, { target, until: Date.now() + LIVE_DELAY_MS, at });
    }
    return delay(policyStatus(s, pid), 200);
  },
  async setModelArmor(enabled, mode) {
    const s = ma[fam(mode)];
    const at = new Date().toISOString();
    s.maChangedAt = at;
    if (mode === "demo") s.modelArmor = enabled;
    else s.maPending = { target: enabled, until: Date.now() + LIVE_DELAY_MS, at };
    return delay({ enabled, status: mode === "demo" ? (enabled ? "applied" : "removed") : "pending" });
  },
  async runTest(id, testId, body, onEvent, signal) {
    for await (const e of runEvents(id, testId, body.scenario_id, body.mode, body.use_llm, signal)) onEvent(e);
  },
  async probe(id, mode) {
    const theme = getThemeOrThrow(id);
    const edges = stateFor(theme, mode).edges;
    logEdges(theme, edges);
    return delay({ edges }, 800);
  },
  async reset(id, mode) {
    getThemeOrThrow(id);
    const s = storeFor(mode, id);
    if (mode === "demo") {
      s.applied.clear();
      s.pending.clear();
    } else {
      for (const pid of s.applied) s.pending.set(pid, { target: false, until: Date.now() + LIVE_DELAY_MS, at: new Date().toISOString() });
    }
    return delay({ ok: true }, 300);
  },
  async sync(id, mode) {
    const theme = getThemeOrThrow(id);
    const st = stateFor(theme, mode);
    return delay({
      policies: theme.policies.map((p) => ({ id: p.id, label: p.text, applied: !!st.policies[p.id]?.applied, status: st.policies[p.id]?.status ?? "removed", detail: "mock" })),
      model_armor: { enabled: st.model_armor.enabled, status: st.model_armor.status, detail: "mock" },
    }, 500);
  },
  async verify(id, mode) {
    const theme = getThemeOrThrow(id);
    const st = stateFor(theme, mode);
    const checks = theme.policies.map((p) => {
      const s = st.policies[p.id];
      const ok = !s?.applied && s?.status === "removed";
      return { id: p.id, label: p.text, ok, detail: ok ? "not present" : s?.status ?? "unknown" };
    });
    checks.push({ id: "model-armor", label: "Model Armor is off", ok: !st.model_armor.enabled, detail: st.model_armor.status });
    const bad = Object.entries(st.edges).filter(([e, v]) => !e.startsWith("ingress:") && v.state !== "direct").map(([e]) => e);
    checks.push({ id: "connections", label: "Every connection goes direct (no gateway)", ok: bad.length === 0, detail: bad.join(", ") || "all direct" });
    return delay({ ok: checks.every((c) => c.ok), checks }, 600);
  },
  async record(id, testId, body) {
    let n = 0;
    for await (const e of runEvents(id, testId, body.scenario_id, "live", body.use_llm)) {
      if (e.type === "error") throw new Error(`live run failed, nothing recorded: ${e.text}`);
      n++;
    }
    const s = storeFor("live", id);
    const sig = [...s.applied].sort().join("+") || "none";
    return { saved: `themes/${id}/recordings/${testId}/${sig}+${s.modelArmor ? "ma-on" : "ma-off"}.json (mock: not written)`, signature: `${sig}+${s.modelArmor ? "ma-on" : "ma-off"}`, events: n };
  },
  async explain(id, pid) {
    const t = getThemeOrThrow(id);
    const p = t.policies.find((x) => x.id === pid);
    if (!p) throw new Error("unknown policy");
    return delay({ lines: EXPLAIN[p.type]?.(p, t) ?? [] }, 250);
  },
  async gatewayLogs(id, _mode, q = {}): Promise<GatewayLogsResponse> {
    getThemeOrThrow(id);
    const now = Date.now();
    const since = q.since ? Date.parse(q.since) : now - 15 * 60_000;
    const limit = q.limit ?? 50;
    const entries = (gwBook.get(id) ?? [])
      .filter(({ visibleAt, e }) => visibleAt <= now && Date.parse(e.timestamp) >= since && (!q.denied_only || e.decision !== "allowed"))
      .map(({ e }) => e)
      .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
      .slice(0, limit);
    const filter = GW_FILTER(id);
    return delay({ source: "simulated", filter, console_url: consoleQueryUrl(filter), entries }, 150);
  },
  async registry(id, mode): Promise<RegistryView> {
    const theme = getThemeOrThrow(id);
    const s = storeFor(mode, id);
    settle(s);
    return delay(mockRegistry(theme, s.applied, mode !== "demo"), 200);
  },
  async terraform(id, mode, includeShared): Promise<TerraformExport> {
    const theme = getThemeOrThrow(id);
    const s = storeFor(mode, id);
    settle(s);
    const principal = REG_PRINCIPAL(theme);
    const out = mockTerraform(theme, s.applied, s.modelArmor, includeShared, mode !== "demo", {
      project: GW_PROJECT,
      projectNumber: GW_PROJECT_NUM,
      region: "us-east4",
      prefix: "agdemo",
      principal,
      projectSet: REG_PROJECT_SET,
      engine: `projects/${GW_PROJECT_NUM}/locations/us-east4/reasoningEngines/${engineNum(theme)}`,
      sources: Object.fromEntries(
        (theme.additional_orchestrators ?? []).map((o) => [
          o.id,
          { name: o.display_name, principal: REG_PRINCIPAL(theme, o.id), engine: `projects/${GW_PROJECT_NUM}/locations/us-east4/reasoningEngines/${engineNum(theme, o.id)}` },
        ]),
      ),
      registryId: (cid) => regId(theme.id + cid),
      condition: mockCondition,
    });
    return delay(out, 200);
  },
};
