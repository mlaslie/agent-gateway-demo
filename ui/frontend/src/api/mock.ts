// In-browser mock of the backend (CONTRACTS §7). Enabled with VITE_MOCK=1 or ?mock=1.
// Simulates §3 rules, Live-mode propagation delays ("pending") and SSE test runs.
import type { Api } from "./client";
import { evaluate } from "./simulate";
import helpdeskJson from "./mockTheme.helpdesk.json";
import retailJson from "./mockTheme.retail.json";
import type { AppConfig, EdgeState, Mode, PolicyStatus, SseEvent, Theme, ThemeState } from "./types";

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
    if (p.type === "gateway_attach") {
      if ((p.params.path === "ingress") === isIngress) return true;
    } else if (p.type === "ingress_allow") {
      if (edge === `ingress:${p.params.caller}`) return true;
    } else if (edge === p.params.target || edge.startsWith(`${p.params.target}:`)) return true;
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
    const pid = theme.policies.find((p) => p.type === "gateway_attach" && p.params.path === path)?.id ?? "";
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

const EXPLAIN: Record<string, (p: Theme["policies"][number]) => string[]> = {
  gateway_attach: (p) =>
    p.params.path === "egress"
      ? [
          "# Bind the Agent Runtime engine to the egress (AGENT_TO_ANYWHERE) gateway",
          "PATCH https://us-east4-aiplatform.googleapis.com/v1beta1/projects/demo/locations/us-east4/reasoningEngines/123?updateMask=spec.deploymentSpec.agentGatewayConfig",
          '{"spec":{"deploymentSpec":{"agentGatewayConfig":{"agentToAnywhereConfig":{"agentGateway":"projects/demo/locations/us-east4/agentGateways/agdemo-egress"}}}}}',
          "# Default deny: only platform endpoints (Gemini, logging, trace, sessions) are allowlisted",
        ]
      : [
          "# Front the engine with the ingress (CLIENT_TO_AGENT) gateway",
          "PATCH .../reasoningEngines/123?updateMask=spec.deploymentSpec.agentGatewayConfig",
          '{"spec":{"deploymentSpec":{"agentGatewayConfig":{"clientToAgentConfig":{"agentGateway":".../agentGateways/agdemo-ingress"}}}}}',
        ],
  a2a_allow: (p) => [
    `# Grant the orchestrator's agent identity egress to ${p.params.target}'s registry endpoint`,
    `gcloud beta iap web add-iam-policy-binding --resource-type=agent-registry-endpoint \\`,
    `  --endpoint=agdemo-helpdesk-${p.params.target} --region=us-east4 \\`,
    `  --member=principal://agents.global.org-123.system.id.goog/.../helpdesk-agent --role=roles/iap.egressor`,
  ],
  mcp_server_allow: (p) => [
    `# Egressor on the whole MCP server ${p.params.target}`,
    `gcloud beta iap web add-iam-policy-binding --endpoint=agdemo-helpdesk-${p.params.target} \\`,
    `  --member=principal://.../helpdesk-agent --role=roles/iap.egressor`,
  ],
  mcp_tool_allow: (p) => [
    `# Conditional egressor binding on ${p.params.target}: read-only tools only`,
    `gcloud beta iap web add-iam-policy-binding --endpoint=agdemo-helpdesk-${p.params.target} \\`,
    `  --member=principal://.../helpdesk-agent --role=roles/iap.egressor \\`,
    `  --condition='expression=request.mcp.tool.annotations.readOnlyHint == true,title=read-only-tools'`,
  ],
  ingress_allow: () => [
    "# Allow only the helpdesk-users principal through the ingress gateway",
    "gcloud beta iap web add-iam-policy-binding --resource-type=reasoning-engine \\",
    "  --member=group:helpdesk-users@example.com --role=roles/iap.httpsResourceAccessor",
  ],
};

function fakeResult(theme: Theme, edge: string): string {
  if (edge.startsWith("ingress:")) return `${theme.orchestrator.display_name} answered the caller.`;
  if (!edge.includes(":")) return `${theme.a2a_agents[edge]?.display_name ?? edge}: (simulated answer)`;
  const [srv, tool] = edge.split(":");
  return `${srv}.${tool} → 200 OK ${tool.startsWith("get") || tool.startsWith("list") || tool.startsWith("lookup") ? "(INC-1042: VPN drops every 30 minutes)" : "(change made!)"}`;
}

async function* runEvents(themeId: string, testId: string, scenarioId: string | undefined, mode: Mode, useLlm: boolean, signal?: AbortSignal): AsyncGenerator<SseEvent> {
  const t = getThemeOrThrow(themeId);
  const test =
    t.scenarios.find((s) => s.id === scenarioId)?.tests.find((x) => x.id === testId) ?? t.scenarios.flatMap((s) => s.tests).find((x) => x.id === testId);
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
  const ingress = !!test.caller;
  yield {
    type: "status",
    text: ingress
      ? `Calling ${t.orchestrator.display_name} as the ${test.caller} caller…`
      : `Sending prompt to ${t.orchestrator.display_name} via Agent Runtime${useLlm ? " (Gemini)" : " (probe mode)"}…`,
  };
  yield { type: "message", role: "user", text: test.prompt };
  await sleep(700, signal);
  const done: Record<string, EdgeState> = {};
  for (const p of test.probes) {
    const kind = p.edge.startsWith("ingress:") ? "ingress gateway" : p.edge.includes(":") ? "MCP tool" : "A2A agent";
    yield { type: "status", text: `Calling ${kind} ${p.edge}…` };
    await sleep(600 + Math.random() * 500, signal);
    let st = result[p.edge];
    if (mode === "live" && edgeTouchedByPending(t, s, p.edge)) {
      st = { state: "pending", governed: true, source: "live", detail: "Policy change still propagating; result may change" };
    }
    done[p.edge] = st;
    yield { type: "edge", edge: p.edge, state: st };
    const text =
      st.state === "denied"
        ? `${p.edge} → ${st.http_status ?? 403} denied by Agent Gateway`
        : st.state === "blocked"
          ? `${p.edge} → blocked by Model Armor`
          : st.state === "pending"
            ? `${p.edge} → policy change pending`
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
          ? "Passwords must be 14+ characters and are rotated every 180 days."
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
    return delay({ edges: stateFor(getThemeOrThrow(id), mode).edges }, 800);
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
    return delay({ lines: EXPLAIN[p.type]?.(p) ?? [] }, 250);
  },
};
