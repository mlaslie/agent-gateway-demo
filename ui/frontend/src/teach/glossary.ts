// Teaching glossary: one plain line per term, shown on hover / focus (components/Teach.tsx).
// Wording is taken from README "Words you'll see" and the Private Networking "The pieces, in plain words" tables
// (glossary.test.ts checks that it stays in sync). Pure module: no React, no DOM, so `node --test` can load it.

export interface GlossaryEntry {
  id: string;
  /** Display name in the tooltip. */
  term: string;
  /** One plain-language line. */
  text: string;
  /** Where the term appears in UI text (global regexes; first occurrence per text is marked). */
  patterns: RegExp[];
}

// Word edges that also treat "-" and "." as part of the word, so resource names (agdemo-egress, iap.egressor)
// aren't marked as the plain word.
const L = "(?<![\\w.-])";
const R = "(?![\\w-])";

export const GLOSSARY: GlossaryEntry[] = [
  {
    id: "agent-gateway",
    term: "Agent Gateway",
    text: "A Google-managed checkpoint every call goes through. Egress (Agent-to-Anywhere) checks calls from your agent; ingress (Client-to-Agent) screens calls to your agent",
    patterns: [/\bAgent Gateway\b/g],
  },
  {
    id: "egress",
    term: "Egress (Agent-to-Anywhere)",
    text: "Egress (Agent-to-Anywhere) checks calls from your agent",
    patterns: [new RegExp(`${L}egress${R}`, "gi"), /\bAgent-to-Anywhere\b/g],
  },
  {
    id: "ingress",
    term: "Ingress (Client-to-Agent)",
    text: "Ingress (Client-to-Agent) screens calls to your agent",
    // Not Cloud Run's "ingress internal" / "--ingress=internal", which is a different setting.
    patterns: [new RegExp(`${L}ingress${R}(?!\\s*(?:internal|=|\\*\\*internal))`, "gi"), /\bClient-to-Agent\b/g],
  },
  {
    id: "agent-runtime",
    term: "Agent Runtime",
    text: "Google's managed hosting for agents (formerly Agent Engine / reasoning engines)",
    patterns: [/\bAgent Runtime\b/g],
  },
  {
    id: "bound",
    term: "Bound",
    text: "An agent attached to a gateway, so its traffic goes through it. All agents in a project and region share the same gateways",
    patterns: [/\b(?:bound|attached)\b/gi],
  },
  {
    id: "agent-registry",
    term: "Agent Registry",
    text: "The address book of destinations (agents, MCP servers, APIs). Egress only reaches what's registered: default deny",
    patterns: [/\bAgent Registry\b/g],
  },
  {
    id: "egressor",
    term: "roles/iap.egressor",
    text: "The permission slip: an agent may call a registry entry only if it holds this role on it (optionally only certain MCP tools)",
    patterns: [/(?:roles\/)?iap\.egressor\b/g],
  },
  {
    id: "agent-identity",
    term: "Agent Identity",
    text: "The agent's own identity (its \"badge\"), set when it's created. Grants are given to it",
    patterns: [/\bAgent Identit(?:y|ies)\b/g],
  },
  {
    id: "iap",
    term: "IAP",
    text: "Identity-Aware Proxy: Google's access check. It decides egress calls, and signs people in to the demo UI",
    patterns: [/(?<![\w./])IAP\b/g, /\bIdentity-Aware Proxy\b/g],
  },
  {
    id: "model-armor",
    term: "Model Armor",
    text: "Content screening (prompt injection, sensitive data) the gateway can apply",
    patterns: [/\bModel Armor\b/g],
  },
  {
    id: "default-deny",
    term: "Default deny",
    text: "Egress only reaches what's registered: default deny. The agent still needs a registry entry and roles/iap.egressor; anything else gets 403",
    patterns: [/\bdefault[- ]deny\b/gi, /\bdefault: deny everything\b/gi, /\bdenies everything by default\b/gi],
  },
  {
    id: "network-attachment",
    term: "Network attachment",
    text: "The gateway's door into your VPC: Google's side connects to it automatically",
    patterns: [/\bnetwork attachment\b/gi],
  },
  {
    id: "psc-endpoint",
    term: "PSC endpoint for Google APIs",
    text: "A private IP inside your VPC that leads to Google services such as Cloud Run, so internal-only services can be reached without the internet",
    patterns: [/\bPSC endpoint(?: for Google APIs)?\b/gi],
  },
  {
    id: "private-dns",
    term: "Private DNS zone run.app.",
    text: "Your VPC's phone book entry: inside the VPC, *.run.app names resolve to the PSC endpoint's private IP",
    patterns: [/\bprivate DNS(?: zone)?\b/gi],
  },
  {
    id: "dns-peering",
    term: "DNS peering",
    text: "Lets the gateway look names up in your VPC's phone book",
    patterns: [/\bDNS peering\b/gi, /\bpeers DNS\b/gi],
  },
  {
    id: "connectivity-template",
    term: "Agent connectivity template",
    text: "One resource holding the gateway's private-network settings: which attachment (VPC), which domains to look up there, which traffic goes in. The gateway points at it",
    patterns: [/\b(?:agent )?connectivity template\b/gi],
  },
  {
    id: "service-account",
    term: "Service account",
    text: "A non-human identity that a deployed service runs as",
    patterns: [/\bservice accounts?\b/gi],
  },
];

const BY_ID = new Map(GLOSSARY.map((g) => [g.id, g]));

export function glossaryEntry(id: string): GlossaryEntry | undefined {
  return BY_ID.get(id);
}

export type GlossSegment = string | { id: string; text: string };

/**
 * Splits `text` into plain strings and glossary terms. Each term is marked once (its first occurrence);
 * pass a shared `seen` set to mark a term only once across several texts.
 */
export function glossSegments(text: string, seen: Set<string> = new Set()): GlossSegment[] {
  const out: GlossSegment[] = [];
  let i = 0;
  while (i < text.length) {
    let best: { index: number; len: number; id: string } | null = null;
    for (const g of GLOSSARY) {
      if (seen.has(g.id)) continue;
      for (const re of g.patterns) {
        re.lastIndex = i;
        const m = re.exec(text);
        if (!m) continue;
        if (!best || m.index < best.index || (m.index === best.index && m[0].length > best.len)) best = { index: m.index, len: m[0].length, id: g.id };
      }
    }
    if (!best) break;
    if (best.index > i) out.push(text.slice(i, best.index));
    out.push({ id: best.id, text: text.slice(best.index, best.index + best.len) });
    seen.add(best.id);
    i = best.index + best.len;
  }
  if (i < text.length) out.push(text.slice(i));
  return out;
}
