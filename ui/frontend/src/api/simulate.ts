// TypeScript port of CONTRACTS §3 / §12 (agdemo_core/simulate.py) — used by the mock layer only.
import type { EdgeState, ScenarioTest, Theme } from "./types";
import { sourceEdge } from "../util";

/** Base egress edge ids (no source prefix), in theme order. */
function baseEgressEdges(theme: Theme): string[] {
  const out: string[] = [];
  for (const c of theme.components) {
    if (c.kind === "a2a_agent") out.push(c.id);
    else out.push(...(theme.mcp_servers[c.id]?.tools ?? []).map((t) => `${c.id}:${t.name}`));
  }
  return out;
}

/** CONTRACTS §12: primary egress edges, then each additional orchestrator's (`<id>/<edge>`), then `ingress:user`. */
export function edgeIds(theme: Theme): string[] {
  const base = baseEgressEdges(theme);
  const out = [...base];
  for (const o of theme.additional_orchestrators ?? []) out.push(...base.map((e) => sourceEdge(o.id, e)));
  out.push("ingress:user");
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
  // Each orchestrator is evaluated on its own (CONTRACTS §12): only policies with its `params.source` count.
  const srcOf = (params: Record<string, unknown>) => (params.source ? String(params.source) : null);
  const hasFor = (src: string | null) => (type: string, pred: (params: Record<string, unknown>) => boolean) =>
    pol.some((p) => p.type === type && srcOf(p.params) === src && pred(p.params));
  const ingress = hasFor(null)("gateway_attach", (p) => p.path === "ingress");
  const malicious = !!(modelArmor && test?.malicious);

  const out: Record<string, EdgeState> = {};
  const governed = (allowed: boolean, what: string, screened = true): EdgeState => {
    if (allowed && malicious && screened)
      return { state: "blocked", governed: true, source, http_status: 403, detail: "Blocked by Model Armor: prompt injection / sensitive data" };
    if (allowed) return { state: "allowed", governed: true, source, http_status: 200, detail: `Allowed by gateway policy (${what})` };
    return { state: "denied", governed: true, source, http_status: 403, detail: `403 from gateway: iap.egressor missing for ${what}` };
  };
  const direct = (): EdgeState => ({ state: "direct", governed: false, source, http_status: 200, detail: "Direct call — no gateway in the path" });

  for (const src of [null, ...(theme.additional_orchestrators ?? []).map((o) => o.id)]) {
    const has = hasFor(src);
    const egress = has("gateway_attach", (p) => p.path === "egress");
    const key = (e: string) => sourceEdge(src, e);
    for (const c of theme.components) {
      if (c.kind === "a2a_agent") {
        out[key(c.id)] = egress
          ? // egress Model Armor screens MCP traffic, not A2A messages (verified)
            governed(has("a2a_allow", (p) => p.target === c.id), key(c.id), false)
          : direct();
      } else {
        for (const t of theme.mcp_servers[c.id]?.tools ?? []) {
          const id = `${c.id}:${t.name}`;
          if (!egress) {
            out[key(id)] = direct();
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
          out[key(id)] = governed(ok, key(id));
        }
      }
    }
  }
  // Ingress (CLIENT_TO_AGENT) only enforces Model Armor: no caller identity checks, never "denied". Primary only.
  out["ingress:user"] = !ingress
    ? { state: "direct", governed: false, source, http_status: 200, detail: "No ingress gateway: the request goes straight to Agent Runtime" }
    : malicious
      ? { state: "blocked", governed: true, source, http_status: 403, detail: "Model Armor on the ingress gateway blocked the prompt" }
      : { state: "allowed", governed: true, source, http_status: 200, detail: "Through the ingress gateway (Model Armor " + (modelArmor ? "screened" : "off") + ")" };
  return out;
}
