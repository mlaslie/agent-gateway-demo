import { Background, BackgroundVariant, ReactFlow, ReactFlowProvider, useNodesInitialized, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useRef, useState } from "react";
import type { EdgeState, GatewayLogEntry, Scenario, Theme, ThemeState } from "../api/types";
import { buildDiagram, type DNode } from "./build";
import { edgeTypes } from "./edges";
import { nodeTypes } from "./nodes";
import { ClockIcon, CloseIcon, ShieldIcon } from "../components/Icons";

const PAD = { top: "46px", bottom: "48px", left: "24px", right: "56px" } as const;

interface Props {
  theme: Theme;
  scenario: Scenario;
  state: ThemeState | null;
  edgeStates: Record<string, EdgeState>;
  inFlight: Set<string>;
  edgeLogs?: Record<string, GatewayLogEntry>;
  onOpenLog?: (e: GatewayLogEntry) => void;
}

function Fitter({ fitKey, wrap }: { fitKey: string; wrap: React.RefObject<HTMLDivElement | null> }) {
  const rf = useReactFlow();
  const init = useNodesInitialized();
  useEffect(() => {
    if (!init) return;
    // wait for node position animations (see useAnimatedPositions) to finish
    const t = setTimeout(() => rf.fitView({ padding: PAD, duration: 450, maxZoom: 1.15 }), 620);
    return () => clearTimeout(t);
  }, [fitKey, init, rf]);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    let t: ReturnType<typeof setTimeout> | undefined;
    const ro = new ResizeObserver(() => {
      clearTimeout(t);
      t = setTimeout(() => rf.fitView({ padding: PAD, duration: 200, maxZoom: 1.15 }), 120);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      clearTimeout(t);
    };
  }, [rf, wrap]);
  return null;
}

/** Tween node positions so nodes and their edges glide together (e.g. the gateway sliding into the path). */
function useAnimatedPositions(nodes: DNode[], resetKey: string, ms = 550): DNode[] {
  const [shown, setShown] = useState(nodes);
  const cur = useRef(new Map<string, { x: number; y: number }>());
  const lastKey = useRef(resetKey);
  useEffect(() => {
    if (lastKey.current !== resetKey) {
      // new scenario/theme: jump, don't glide
      lastKey.current = resetKey;
      cur.current.clear();
    }
    const from = new Map(cur.current);
    const moving = nodes.some((n) => {
      const p = from.get(n.id);
      return p && (Math.abs(p.x - n.position.x) > 0.5 || Math.abs(p.y - n.position.y) > 0.5);
    });
    if (!moving) {
      nodes.forEach((n) => cur.current.set(n.id, n.position));
      setShown(nodes);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms);
      const e = 1 - Math.pow(1 - k, 3);
      const out = nodes.map((n) => {
        const p = from.get(n.id);
        if (!p) return n;
        const pos = { x: p.x + (n.position.x - p.x) * e, y: p.y + (n.position.y - p.y) * e };
        cur.current.set(n.id, pos);
        return { ...n, position: pos };
      });
      setShown(out);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [nodes, ms, resetKey]);
  return shown;
}

export function Diagram({ theme, scenario, state, edgeStates, inFlight, edgeLogs, onOpenLog }: Props) {
  const { nodes: targetNodes, edges } = useMemo(
    () => buildDiagram({ theme, scenario, state, edgeStates, inFlight, edgeLogs, onOpenLog }),
    [theme, scenario, state, edgeStates, inFlight, edgeLogs, onOpenLog],
  );
  const nodes = useAnimatedPositions(targetNodes, `${theme.id}|${scenario.id}`);
  const wrap = useRef<HTMLDivElement>(null);
  const fitKey = `${theme.id}|${scenario.id}|${state?.gateways.egress.attached}|${state?.gateways.ingress.attached}`;
  return (
    <div className="diagram">
      <div className="diagram-canvas" ref={wrap}>
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          fitView
          fitViewOptions={{ padding: PAD, maxZoom: 1.15 }}
          minZoom={0.3}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          zoomOnDoubleClick={false}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} className="diagram-bg" />
          <Fitter fitKey={fitKey} wrap={wrap} />
        </ReactFlow>
      </ReactFlowProvider>
      </div>
      <Legend />
    </div>
  );
}

function Legend() {
  const items: { cls: string; label: string; icon?: React.ReactNode }[] = [
    { cls: "direct", label: "Direct (no gateway)" },
    { cls: "allowed", label: "Allowed" },
    { cls: "denied", label: "Denied (403)", icon: <CloseIcon size={11} /> },
    { cls: "blocked", label: "Model Armor block", icon: <ShieldIcon size={11} /> },
    { cls: "pending", label: "Pending", icon: <ClockIcon size={11} /> },
    { cls: "unknown", label: "Not tested" },
    { cls: "error", label: "Error" },
  ];
  return (
    <div className="legend">
      {items.map((i) => (
        <span key={i.cls} className="legend-item">
          <svg width="30" height="10" className="legend-line">
            <line x1="1" y1="5" x2="29" y2="5" className={`edge-path st-${i.cls}`} />
          </svg>
          {i.icon && <span className={`legend-icon li-${i.cls}`}>{i.icon}</span>}
          {i.label}
        </span>
      ))}
      <span className="legend-item">
        <svg width="30" height="10" className="legend-line">
          <line x1="1" y1="5" x2="29" y2="5" className="edge-registry" />
        </svg>
        Registry discovery
      </span>
    </div>
  );
}
