// Builds React Flow nodes + edges from the theme, the current scenario and the edge states.
import type { Edge, Node } from "@xyflow/react";
import type { EdgeState, EdgeStateName, GatewayLogEntry, Scenario, Theme, ThemeState } from "../api/types";

export type NodeKind = "user" | "gateway" | "orchestrator" | "registry" | "a2a" | "mcp";

export interface ToolRow {
  name: string;
  readOnly: boolean;
  edgeId: string;
  state: EdgeStateName;
}

export interface DiagramNodeData extends Record<string, unknown> {
  kind: NodeKind;
  label: string;
  sublabel?: string;
  badge?: string;
  faded?: boolean;
  chips?: { text: string; tone: "neutral" | "blue" | "red" | "green" | "amber" | "orange" }[];
  shield?: boolean;
  skills?: string[];
  tools?: ToolRow[];
  state?: EdgeStateName; // for single-edge targets (A2A agents, the ingress user)
  inFlight?: boolean;
  direction?: "egress" | "ingress";
}

export interface DiagramEdgeData extends Record<string, unknown> {
  state: EdgeStateName | "neutral" | "registry";
  detail?: string;
  httpStatus?: number | null;
  inFlight?: boolean;
  label?: string;
  expected?: EdgeStateName;
  replayed?: boolean;
  bus?: { x: number; y: number };
  /** Denied/blocked edge with an Agent Gateway log entry: click opens it. */
  log?: { summary: string; open: () => void };
}

export type DNode = Node<DiagramNodeData>;
export type DEdge = Edge<DiagramEdgeData>;

const W: Record<NodeKind, number> = { user: 200, gateway: 220, orchestrator: 240, registry: 220, a2a: 270, mcp: 290 };

export function nodeHeight(d: DiagramNodeData): number {
  switch (d.kind) {
    case "mcp":
      return 70 + (d.tools?.length ?? 0) * 31 + 6;
    case "a2a":
      return 70 + (d.skills?.length ?? 0) * 26;
    case "gateway":
      return 112;
    case "orchestrator":
      return 108;
    default:
      return 76;
  }
}

/** The single ingress edge: user → (ingress gateway →) orchestrator. */
export const INGRESS_EDGE = "ingress:user";

export const EGRESS_DEFAULT = ["orchestrator", "egress_gateway", "registry"];

export function visibleNodeIds(theme: Theme, sc: Scenario): string[] {
  if (sc.nodes.length) return sc.nodes;
  if (sc.flow === "ingress") return ["user", "ingress_gateway", "orchestrator"];
  return [...EGRESS_DEFAULT, ...theme.components.map((c) => c.id)];
}

export function edgeStateOf(edges: Record<string, EdgeState | undefined>, id: string): EdgeState {
  return edges[id] ?? { state: "unknown" };
}

interface BuildArgs {
  theme: Theme;
  scenario: Scenario;
  state: ThemeState | null;
  edgeStates: Record<string, EdgeState>;
  inFlight: Set<string>;
  /** Newest gateway log entry per edge id (CONTRACTS §9). */
  edgeLogs?: Record<string, GatewayLogEntry>;
  onOpenLog?: (e: GatewayLogEntry) => void;
}

export function buildDiagram({ theme, scenario, state, edgeStates, inFlight, edgeLogs, onOpenLog }: BuildArgs): { nodes: DNode[]; edges: DEdge[] } {
  const vis = new Set(visibleNodeIds(theme, scenario));
  const egressAttached = !!state?.gateways.egress.attached;
  const ingressAttached = !!state?.gateways.ingress.attached;
  const egressPending = /pending/.test(state?.gateways.egress.status ?? "");
  const ingressPending = /pending/.test(state?.gateways.ingress.status ?? "");
  const ma = !!state?.model_armor.enabled;
  const comps = theme.components.filter((c) => vis.has(c.id));
  const hasUser = vis.has("user");

  const nodes: DNode[] = [];
  const edges: DEdge[] = [];
  const st = (id: string) => edgeStateOf(edgeStates, id);
  const expected = (id: string) => state?.expected?.[id]?.state;

  // ---------- target nodes (stacked vertically) ----------
  const targetData: { id: string; data: DiagramNodeData }[] = comps.map((c) => {
    if (c.kind === "a2a_agent") {
      const spec = theme.a2a_agents[c.id];
      return {
        id: c.id,
        data: {
          kind: "a2a",
          label: spec?.display_name ?? c.id,
          sublabel: c.id,
          badge: "A2A · Cloud Run",
          skills: spec?.skills.map((s) => s.name) ?? [],
          state: st(c.id).state,
          inFlight: inFlight.has(c.id),
        },
      };
    }
    const spec = theme.mcp_servers[c.id];
    return {
      id: c.id,
      data: {
        kind: "mcp",
        label: spec?.display_name ?? c.id,
        sublabel: c.id,
        badge: "MCP · Cloud Run",
        tools: (spec?.tools ?? []).map((t) => {
          const eid = `${c.id}:${t.name}`;
          return { name: t.name, readOnly: t.read_only, edgeId: eid, state: st(eid).state };
        }),
        inFlight: (spec?.tools ?? []).some((t) => inFlight.has(`${c.id}:${t.name}`)),
      },
    };
  });

  // ---------- columns ----------
  const GAP_X = 110;
  const cols: { key: string; w: number }[] = [];
  if (hasUser) cols.push({ key: "user", w: W.user });
  if (vis.has("ingress_gateway")) cols.push({ key: "ingress_gateway", w: W.gateway });
  if (vis.has("orchestrator")) cols.push({ key: "orchestrator", w: W.orchestrator });
  if (vis.has("egress_gateway")) cols.push({ key: "egress_gateway", w: W.gateway });
  if (targetData.length) cols.push({ key: "targets", w: Math.max(...targetData.map((t) => W[t.data.kind])) });
  const colX: Record<string, number> = {};
  let x = 0;
  for (const c of cols) {
    colX[c.key] = x;
    x += c.w + GAP_X;
  }

  const GAP_Y = 26;
  const targetHeights = targetData.map((t) => nodeHeight(t.data));
  const totalH = targetHeights.reduce((a, b) => a + b, 0) + GAP_Y * Math.max(0, targetData.length - 1);
  const centerY = 0;
  let y = centerY - totalH / 2;
  const pos = (id: string, px: number, py: number) => {
    const p = theme.layout?.[id];
    return p ? { x: p[0], y: p[1] } : { x: px, y: py };
  };

  targetData.forEach((t, i) => {
    nodes.push({ id: t.id, type: t.data.kind, position: pos(t.id, colX.targets, y), data: t.data, draggable: false });
    y += targetHeights[i] + GAP_Y;
  });
  const targetsBottom = centerY + totalH / 2;
  const targetsTop = centerY - totalH / 2;

  // ---------- orchestrator ----------
  if (vis.has("orchestrator")) {
    const d: DiagramNodeData = {
      kind: "orchestrator",
      label: theme.orchestrator.display_name,
      sublabel: theme.orchestrator.id,
      badge: "Agent Runtime",
      chips: [{ text: "ADK · Agent Identity", tone: "neutral" }],
    };
    nodes.push({ id: "orchestrator", type: "orchestrator", position: pos("orchestrator", colX.orchestrator, centerY - nodeHeight(d) / 2), data: d, draggable: false });
  }

  // ---------- gateways ----------
  const gw = (id: "egress_gateway" | "ingress_gateway", attached: boolean, pending: boolean) => {
    const egress = id === "egress_gateway";
    const chips: DiagramNodeData["chips"] = [];
    if (pending) chips.push({ text: attached ? "Attaching…" : "Detaching…", tone: "amber" });
    // Egress denies by default (IAP egressor); ingress only screens content (Model Armor), no identity checks.
    else if (attached) chips.push(egress ? { text: "Default deny", tone: "red" } : { text: "In the path", tone: "blue" });
    else chips.push({ text: "Not attached", tone: "neutral" });
    if (attached && ma) chips.push({ text: "Model Armor", tone: "orange" });
    const d: DiagramNodeData = {
      kind: "gateway",
      label: "Agent Gateway",
      sublabel: egress ? "Egress · Agent-to-Anywhere" : "Ingress · Client-to-Agent",
      faded: !attached && !pending,
      chips,
      shield: attached && ma,
      direction: egress ? "egress" : "ingress",
    };
    const h = nodeHeight(d);
    // When detached the gateway slides out of the call path (above it).
    // Detached: parked at the top of its column (out of the direct call path), faded.
    const top = Math.min(targetsTop, -h / 2 - 150);
    const py = attached || pending ? centerY - h / 2 : top;
    nodes.push({ id, type: "gateway", position: pos(id, colX[id], py), data: d, draggable: false });
  };
  if (vis.has("egress_gateway")) gw("egress_gateway", egressAttached, egressPending);
  if (vis.has("ingress_gateway")) gw("ingress_gateway", ingressAttached, ingressPending);

  // ---------- user (single ingress caller) ----------
  if (hasUser) {
    const d: DiagramNodeData = {
      kind: "user",
      label: "User",
      sublabel: "Presenter / client app",
      state: st(INGRESS_EDGE).state,
      inFlight: inFlight.has(INGRESS_EDGE),
    };
    nodes.push({ id: "user", type: "user", position: pos("user", colX.user, centerY - nodeHeight(d) / 2), data: d, draggable: false });
  }

  // ---------- registry ----------
  if (vis.has("registry")) {
    const d: DiagramNodeData = {
      kind: "registry",
      label: "Agent Registry",
      sublabel: `${theme.components.filter((c) => c.kind === "a2a_agent").length} A2A agents · ${theme.components.filter((c) => c.kind === "mcp_server").length} MCP servers`,
    };
    // Bottom of the gateway column; a dashed "bus" runs under the targets and up their right side.
    const rx = colX.egress_gateway ?? colX.orchestrator ?? 0;
    const RH = 76;
    const ry = Math.max(targetsBottom - RH, 56 + 50);
    nodes.push({ id: "registry", type: "registry", position: pos("registry", rx, ry), data: d, draggable: false });
    const busY = Math.max(targetsBottom, ry + RH) + 30;
    const busX = (colX.targets ?? rx + W.registry) + (targetData.length ? Math.max(...targetData.map((t) => W[t.data.kind])) : 0) + 34;
    if (vis.has("orchestrator"))
      edges.push({ id: "reg:orchestrator", source: "registry", sourceHandle: "left", target: "orchestrator", targetHandle: "reg", type: "registry", data: { state: "registry", label: "discovers" }, zIndex: 0 });
    comps.forEach((c, i) =>
      edges.push({ id: `reg:${c.id}`, source: "registry", sourceHandle: "bottom", target: c.id, targetHandle: "reg", type: "registry", data: { state: "registry", bus: { y: busY, x: busX }, label: i === 0 ? "registered" : undefined }, zIndex: 0 }),
    );
  }

  // ---------- egress call edges ----------
  const throughEgress = vis.has("egress_gateway") && (egressAttached || egressPending);
  const egressEdgeIds: string[] = [];
  const mkEdge = (edgeId: string, source: string, sourceHandle: string | undefined, target: string, targetHandle: string | undefined): DEdge => {
    const s = st(edgeId);
    // A fully denied MCP server is logged at the session handshake (component-level entry), not per tool.
    const le = edgeLogs?.[edgeId] ?? (edgeId.includes(":") ? edgeLogs?.[edgeId.split(":")[0]] : undefined);
    const logged = (s.state === "denied" || s.state === "blocked") && le && le.decision !== "allowed" && onOpenLog ? le : undefined;
    return {
      id: `e:${edgeId}`,
      source,
      sourceHandle,
      target,
      targetHandle,
      type: "state",
      zIndex: 1,
      data: {
        state: s.state,
        detail: s.detail,
        httpStatus: s.http_status,
        inFlight: inFlight.has(edgeId),
        expected: s.state === "pending" ? expected(edgeId) : undefined,
        replayed: s.source === "replayed",
        log: logged ? { summary: logged.summary, open: () => onOpenLog!(logged) } : undefined,
      },
    };
  };
  if (vis.has("orchestrator")) {
    for (const c of comps) {
      const ids = c.kind === "a2a_agent" ? [c.id] : (theme.mcp_servers[c.id]?.tools ?? []).map((t) => `${c.id}:${t.name}`);
      for (const eid of ids) {
        egressEdgeIds.push(eid);
        const th = c.kind === "a2a_agent" ? "in" : `tool-${eid.split(":")[1]}`;
        edges.push(throughEgress ? mkEdge(eid, "egress_gateway", "out", c.id, th) : mkEdge(eid, "orchestrator", "out", c.id, th));
      }
    }
    if (throughEgress && comps.length)
      edges.push({
        id: "seg:orch-egress",
        source: "orchestrator",
        sourceHandle: "out",
        target: "egress_gateway",
        targetHandle: "in",
        type: "state",
        zIndex: 1,
        data: { state: "neutral", inFlight: egressEdgeIds.some((e) => inFlight.has(e)) },
      });
  }

  // ---------- ingress call edge ----------
  // CLIENT_TO_AGENT only screens content (Model Armor); it does not check caller identity.
  const throughIngress = vis.has("ingress_gateway") && (ingressAttached || ingressPending);
  if (vis.has("orchestrator") && hasUser) {
    edges.push(throughIngress ? mkEdge(INGRESS_EDGE, "user", "out", "ingress_gateway", "in") : mkEdge(INGRESS_EDGE, "user", "out", "orchestrator", "in"));
    if (throughIngress)
      edges.push({
        id: "seg:ingress-orch",
        source: "ingress_gateway",
        sourceHandle: "out",
        target: "orchestrator",
        targetHandle: "in",
        type: "state",
        zIndex: 1,
        data: { state: st(INGRESS_EDGE).state === "allowed" ? "allowed" : "neutral", inFlight: inFlight.has(INGRESS_EDGE) },
      });
  }

  return { nodes, edges };
}
