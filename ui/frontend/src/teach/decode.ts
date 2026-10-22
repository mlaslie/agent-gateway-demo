// Error decoder: a one-line "why" and "fix" for each known failed-call outcome, shown on the diagram edges, in
// the Activity log / Test results and in the gateway logs. Single source of truth for this wording (frontend only;
// the backend doesn't need it). Wording follows docs/ISSUES.md and docs/TROUBLESHOOTING.md.
// Pure module (types only), so `node --test` can load it: see decode.test.ts.
import type { GatewayLogEntry, Theme } from "../api/types";

export type FailureCode =
  | "model-armor"
  | "token-401"
  | "platform-blocked"
  | "internal-404"
  | "unregistered"
  | "write-denied"
  | "grant-not-in-effect"
  | "egress-denied"
  | "unreachable";

export interface Decoded {
  code: FailureCode;
  /** Short label of the outcome, e.g. "403 · Egress request is not authorized". */
  title: string;
  why: string;
  fix: string;
}

/** What is known about one failed call. Every field is optional; more facts give a more specific answer. */
export interface FailureFacts {
  /** Edge state (denied / blocked / unreachable / error) or gateway-log decision. */
  state?: string | null;
  status?: number | null;
  /** Edge detail, log text or log summary. */
  detail?: string | null;
  host?: string | null;
  /** false: the gateway log entry has no Agent Registry entry for the host. */
  registered?: boolean | null;
  /** The call is an MCP tool that isn't read-only. */
  writeTool?: boolean;
  /** A grant on this MCP server is in place, but only for other (read-only / named) tools. */
  readOnlyGrant?: boolean;
  /** A grant covering this call is applied (or being applied), yet the call was denied. */
  grantApplied?: boolean;
}

export const DECODED: Record<FailureCode, Decoded> = {
  "model-armor": {
    code: "model-armor",
    title: "403 · Blocked by Model Armor",
    why: "Model Armor screened the content and found a prompt injection or sensitive data, so the gateway stopped it.",
    fix: "Expected for a malicious prompt. Send a clean prompt, or turn Model Armor off to compare.",
  },
  "token-401": {
    code: "token-401",
    title: "401 · Google API call behind a gateway",
    why: "A known google-auth bug (being fixed): agents with a newer google-auth get 401 on their own Google API calls once bound to a gateway (ISSUES #16).",
    fix: "Redeploy the agent with google-auth==2.60.0 in its requirements (deploy-theme pins it). Or set GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false, which turns off token binding.",
  },
  "platform-blocked": {
    code: "platform-blocked",
    title: "498 / blocked platform call",
    why: "A Google platform endpoint the agent itself needs (Gemini, sessions, logging, tracing) isn't registered or allowed, so default deny blocks it.",
    fix: "Find the host in the gateway log, add it to platform_endpoints() in agdemo_core/infra/common.py and run ./agdemo tf apply.",
  },
  "internal-404": {
    code: "internal-404",
    title: "404 · Internal Cloud Run service",
    why: "The service has Cloud Run ingress internal. This call came over the internet, not through the VPC path, so the service isn't there.",
    fix: "Call it through the VPC-enabled gateway (network attachment → PSC endpoint) and grant the agent roles/iap.egressor on it.",
  },
  unregistered: {
    code: "unregistered",
    title: "403 · Host not in Agent Registry",
    why: "The gateway found no Agent Registry entry for this host, so default deny applies.",
    fix: "Register the host exactly as it is called (including any .mtls. or :443 form), then grant roles/iap.egressor on it.",
  },
  "write-denied": {
    code: "write-denied",
    title: "Denied · write tool under a read-only grant",
    why: "The grant on this MCP server covers only its read-only tools, so the gateway denies write tools with 403.",
    fix: "Expected. To allow it, apply the policy that grants every tool on this server.",
  },
  "grant-not-in-effect": {
    code: "grant-not-in-effect",
    title: "403 · Grant not in effect yet",
    why: "The grant is in place, but IAP changes take about 2 min (up to about 7) to reach the gateway.",
    fix: "Wait about 2 min and run the test again, or present with Live + fallback.",
  },
  "egress-denied": {
    code: "egress-denied",
    title: "403 · Egress request is not authorized",
    why: "Default deny: the destination isn't in Agent Registry, or the agent's Agent Identity holds no roles/iap.egressor grant on it.",
    fix: "Apply the allow policy for this destination. If you just did, wait about 2 min (up to about 7) and run the test again.",
  },
  unreachable: {
    code: "unreachable",
    title: "Unreachable / timeout",
    why: "The call got no answer: the service is down or still starting, or it was the first call right after an attach (TLS handshake failure, ISSUES #8).",
    fix: "Run the test again. If it keeps failing, check ./agdemo status and the Cloud Run service.",
  },
};

const MA_RE = /model armor|agdemo-model-armor-block|content security configuration/i;
const PLATFORM_HOST_RE = /(^|\.)googleapis\.com(:\d+)?$/i;
const UNREACHABLE_RE = /time(?:d)? ?out|deadline|unreachable|connect(?:ion)? ?(?:error|refused|reset)|connecterror|refused|handshake|ssl|tls|name resolution|unavailable|no route|\b50[234]\b/i;

/** Decodes a failed call into a known outcome, or null when it isn't a failure or isn't one we know. */
export function decodeFailure(f: FailureFacts): Decoded | null {
  const state = (f.state ?? "").toLowerCase();
  const status = f.status ?? null;
  const detail = f.detail ?? "";
  if (state === "allowed" || state === "direct" || state === "pending" || state === "unknown") return null;

  if (state === "blocked" || (status === 403 && MA_RE.test(detail))) return DECODED["model-armor"];
  if (status === 401 || /\b401\b|unauthenticated/i.test(detail)) return DECODED["token-401"];
  if (status === 498 || /\b498\b/.test(detail)) return DECODED["platform-blocked"];
  if (state === "unreachable" || status === 404) return DECODED["internal-404"];

  const denied = state === "denied" || status === 403 || /egress request is not authorized/i.test(detail);
  if (denied) {
    if (f.host && PLATFORM_HOST_RE.test(f.host)) return DECODED["platform-blocked"];
    if (f.registered === false) return DECODED.unregistered;
    if (f.writeTool && f.readOnlyGrant) return DECODED["write-denied"];
    if (f.grantApplied) return DECODED["grant-not-in-effect"];
    return DECODED["egress-denied"];
  }

  if (state === "error" || status === 502 || status === 503 || status === 504) {
    if (UNREACHABLE_RE.test(detail) || status === null || status === 0 || status >= 502) return DECODED.unreachable;
  }
  return null;
}

// ---------- facts from the theme and the policy state ----------

const ALLOW_TYPES = new Set(["a2a_allow", "mcp_server_allow", "mcp_tool_allow"]);

/**
 * Grant facts for one edge id ("kb-agent", "tickets-mcp:close_ticket", "hr-assistant/kb-agent"), given the ids of the
 * policies that are applied or being applied.
 */
export function edgeFacts(theme: Theme | null | undefined, active: ReadonlySet<string>, edge: string | null | undefined): Pick<FailureFacts, "writeTool" | "readOnlyGrant" | "grantApplied"> {
  if (!theme || !edge) return {};
  const slash = edge.indexOf("/");
  const src = slash > 0 ? edge.slice(0, slash) : null;
  const base = slash > 0 ? edge.slice(slash + 1) : edge;
  const [comp, tool] = base.split(":");
  const spec = theme.mcp_servers[comp]?.tools.find((t) => t.name === tool);
  const writeTool = !!tool && !!spec && !spec.read_only;
  const grants = theme.policies.filter(
    (p) => ALLOW_TYPES.has(p.type) && p.params.target === comp && (p.params.source ? String(p.params.source) : null) === src && active.has(p.id),
  );
  const covers = (p: (typeof grants)[number]) => {
    if (p.type !== "mcp_tool_allow") return true;
    if (!tool) return true; // component-level entry (MCP session handshake)
    if (p.params.read_only === true) return !!spec?.read_only;
    return Array.isArray(p.params.tools) && (p.params.tools as string[]).includes(tool);
  };
  const grantApplied = grants.some(covers);
  return { writeTool, readOnlyGrant: !grantApplied && grants.length > 0, grantApplied };
}

/** Facts for one gateway log entry (CONTRACTS §9). */
export function logFacts(e: GatewayLogEntry, theme: Theme | null | undefined, active: ReadonlySet<string>): FailureFacts {
  const agw = (e.raw?.jsonPayload as { agentGatewayInfo?: { agentRegistryResource?: string } } | undefined)?.agentGatewayInfo;
  // TROUBLESHOOTING.md: a live entry with no agentRegistryResource means the host is unregistered. Hosts of the theme's
  // own deployed services are registered by deploy-theme, so only an unknown host counts.
  const registered = e.simulated ? null : !!(agw?.agentRegistryResource || e.component);
  return { state: e.decision, status: e.status, detail: e.summary, host: e.host, registered, ...edgeFacts(theme, active, e.edge ?? e.component) };
}
