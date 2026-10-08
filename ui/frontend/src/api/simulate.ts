// TypeScript port of CONTRACTS §3 (agdemo_core/simulate.py) — used by the mock layer only.
import type { EdgeState, ScenarioTest, Theme } from "./types";

export function edgeIds(theme: Theme): string[] {
  const out: string[] = [];
  for (const c of theme.components) {
    if (c.kind === "a2a_agent") out.push(c.id);
    else out.push(...(theme.mcp_servers[c.id]?.tools ?? []).map((t) => `${c.id}:${t.name}`));
  }
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
  // Ingress (CLIENT_TO_AGENT) only enforces Model Armor: no caller identity checks, never "denied".
  out["ingress:user"] = !ingress
    ? { state: "direct", governed: false, source, http_status: 200, detail: "No ingress gateway: the request goes straight to Agent Runtime" }
    : malicious
      ? { state: "blocked", governed: true, source, http_status: 403, detail: "Model Armor on the ingress gateway blocked the prompt" }
      : { state: "allowed", governed: true, source, http_status: 200, detail: "Through the ingress gateway (Model Armor " + (modelArmor ? "screened" : "off") + ")" };
  return out;
}
