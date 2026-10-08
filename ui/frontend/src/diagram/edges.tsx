import { BaseEdge, EdgeLabelRenderer, getBezierPath, getSmoothStepPath, type EdgeProps } from "@xyflow/react";
import { memo } from "react";
import type { DEdge } from "./build";
import { ClockIcon, CloseIcon, ShieldIcon } from "../components/Icons";

export const STATE_LABEL: Record<string, string> = {
  direct: "Direct (no gateway)",
  allowed: "Allowed by gateway",
  denied: "Denied by gateway (403)",
  blocked: "Blocked by Model Armor",
  pending: "Policy change pending",
  unknown: "Not tested yet",
  error: "Infrastructure error",
  neutral: "Through gateway",
};

function StateEdgeImpl({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data }: EdgeProps<DEdge>) {
  const state = data?.state ?? "unknown";
  const [path] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, curvature: 0.35 });
  // badge position: ~80% along an approximation of the bezier, so converging edges don't stack badges
  const bt = 0.8;
  const mx = (sourceX + targetX) / 2;
  const bz = (a: number, b: number, c: number, d: number) => (1 - bt) ** 3 * a + 3 * (1 - bt) ** 2 * bt * b + 3 * (1 - bt) * bt ** 2 * c + bt ** 3 * d;
  const bx = bz(sourceX, mx, mx, targetX);
  const by = bz(sourceY, sourceY, targetY, targetY);
  const markerId = `m-${id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  const showArrow = state !== "denied";
  const badge =
    state === "denied" ? (
      <span className="edge-badge badge-denied">
        <CloseIcon size={14} />
      </span>
    ) : state === "blocked" ? (
      <span className="edge-badge badge-blocked">
        <ShieldIcon size={14} />
      </span>
    ) : state === "error" ? (
      <span className="edge-badge badge-error">!</span>
    ) : state === "pending" ? (
      <span className="edge-badge badge-pending">
        <ClockIcon size={13} />
      </span>
    ) : null;
  const title = [STATE_LABEL[state] ?? state, data?.httpStatus ? `HTTP ${data.httpStatus}` : "", data?.detail ?? "", data?.expected ? `expected: ${data.expected}` : "", data?.replayed ? "(replayed)" : ""]
    .filter(Boolean)
    .join(" · ");
  return (
    <>
      <defs>
        <marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className={`edge-arrow st-${state}`} />
        </marker>
      </defs>
      <path d={path} className="edge-halo" />
      <BaseEdge id={id} path={path} className={`edge-path st-${state}`} markerEnd={showArrow ? `url(#${markerId})` : undefined} />
      <path d={path} className="edge-hit" fill="none">
        <title>{title}</title>
      </path>
      {data?.inFlight && (
        <g className={`edge-particles st-${state}`}>
          {[0, 0.4, 0.8].map((d) => (
            <circle key={d} r={d === 0 ? 6 : 4} className="edge-particle">
              <animateMotion dur="1.2s" begin={`${d}s`} repeatCount="indefinite" path={path} />
            </circle>
          ))}
        </g>
      )}
      {badge && (
        <EdgeLabelRenderer>
          <div className="edge-badge-wrap nodrag nopan" style={{ transform: `translate(-50%, -50%) translate(${bx}px, ${by}px)` }} title={title}>
            {badge}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

function RegistryEdgeImpl({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data }: EdgeProps<DEdge>) {
  let path: string, lx: number, ly: number;
  if (data?.bus) {
    // registry bottom -> down to bus -> right -> up the right side -> left into the component's right handle
    const { x: bx, y: by } = data.bus;
    const r = 12;
    path = [
      `M ${sourceX} ${sourceY}`,
      `L ${sourceX} ${by - r}`,
      `Q ${sourceX} ${by} ${sourceX + r} ${by}`,
      `L ${bx - r} ${by}`,
      `Q ${bx} ${by} ${bx} ${by - r}`,
      `L ${bx} ${targetY + r}`,
      `Q ${bx} ${targetY} ${bx - r} ${targetY}`,
      `L ${targetX} ${targetY}`,
    ].join(" ");
    lx = (sourceX + bx) / 2;
    ly = by;
  } else {
    [path, lx, ly] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 14, offset: 26 });
  }
  return (
    <>
      <BaseEdge id={id} path={path} className="edge-registry" />
      {data?.label && (
        <EdgeLabelRenderer>
          <div className="edge-label registry-label" style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}>
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const edgeTypes = { state: memo(StateEdgeImpl), registry: memo(RegistryEdgeImpl) };
