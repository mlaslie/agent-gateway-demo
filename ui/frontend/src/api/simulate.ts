// TypeScript port of CONTRACTS §3 (agdemo_core/simulate.py) — used by the mock layer only.
import type { EdgeState, ScenarioTest, Theme } from "./types";

export function edgeIds(theme: Theme): string[] {
  const out: string[] = [];
  for (const c of theme.components) {
    if (c.kind === "a2a_agent") out.push(c.id);
    else out.push(...(theme.mcp_servers[c.id]?.tools ?? []).map((t) => `${c.id}:${t.name}`));
  }
  out.push("ingress:allowed", "ingress:denied");
  return out;
}

export function evaluate(
  theme: Theme,
  applied: Set<string>,
  modelArmor: boolean,
  test: ScenarioTest | null,
  source: EdgeState["source"] = "simulated",
): Record<string, EdgeState> {
  const pol = theme.policies.filter((p) => applied.has(p.id));
  const has = (type: string, pred: (params: Record<string, unknown>) => boolean) =>
    pol.some((p) => p.type === type && pred(p.params));
  const egress = has("gateway_attach", (p) => p.path === "egress");
  const ingress = has("gateway_attach", (p) => p.path === "ingress");
  const malicious = !!(modelArmor && test?.malicious);

  const out: Record<string, EdgeState> = {};
  const governed = (allowed: boolean, what: string, screened = true): EdgeState => {
    if (allowed && malicious && screened)
      return { state: "blocked", governed: true, source, http_status: 403, detail: "Blocked by Model Armor: prompt injection / sensitive data" };
    if (allowed) return { state: "allowed", governed: true, source, http_status: 200, detail: `Allowed by gateway policy (${what})` };
    return { state: "denied", governed: true, source, http_status: 403, detail: `403 from gateway: iap.egressor missing for ${what}` };
  };

  for (const c of theme.components) {
    if (c.kind === "a2a_agent") {
      out[c.id] = egress
        ? // egress Model Armor screens MCP traffic, not A2A messages (verified)
          governed(has("a2a_allow", (p) => p.target === c.id), c.id, false)
        : { state: "direct", governed: false, source, http_status: 200, detail: "Direct call — no gateway in the path" };
    } else {
      for (const t of theme.mcp_servers[c.id]?.tools ?? []) {
        const id = `${c.id}:${t.name}`;
        if (!egress) {
          out[id] = { state: "direct", governed: false, source, http_status: 200, detail: "Direct call — no gateway in the path" };
          continue;
        }
        const ok =
          has("mcp_server_allow", (p) => p.target === c.id) ||
          has(
            "mcp_tool_allow",
            (p) =>
              p.target === c.id &&
              ((p.read_only === true && t.read_only) || (Array.isArray(p.tools) && (p.tools as string[]).includes(t.name))),
          );
        out[id] = governed(ok, id);
      }
    }
  }
  for (const caller of ["allowed", "denied"] as const) {
    const id = `ingress:${caller}`;
    if (!ingress) {
      out[id] = { state: "direct", governed: false, source, http_status: 200, detail: "No ingress gateway: caller has roles/aiplatform.user" };
    } else {
      // Caller policies only count when GCP enforces them (theme marks them enforced).
      const callerAuth = theme.policies.some((p) => p.type === "ingress_allow" && p.enforced !== false);
      const ok = !callerAuth || has("ingress_allow", (p) => p.caller === caller);
      out[id] = ok
        ? malicious
          ? { state: "blocked", governed: true, source, http_status: 403, detail: "Blocked by Model Armor" }
          : { state: "allowed", governed: true, source, http_status: 200, detail: "Allowed by ingress policy" }
        : { state: "denied", governed: true, source, http_status: 403, detail: "403 from ingress gateway: caller not authorized" };
    }
  }
  return out;
}
