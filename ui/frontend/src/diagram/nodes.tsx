import { Handle, Position, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import type { AgentState, DNode } from "./build";
import { BookIcon, CloudIcon, GatewayIcon, PencilIcon, PersonIcon, OpenInNewIcon, RegistryIcon, RobotIcon, ServerIcon, ShieldIcon } from "../components/Icons";
import { STATE_LABEL } from "./edges";

const H = ({ type, pos, id, style }: { type: "source" | "target"; pos: Position; id: string; style?: React.CSSProperties }) => (
  <Handle type={type} position={pos} id={id} isConnectable={false} className="hnd" style={style} />
);

function Chips({ chips }: { chips?: DNode["data"]["chips"] }) {
  if (!chips?.length) return null;
  return (
    <div className="node-chips">
      {chips.map((c) => (
        <span key={c.text} className={`chip chip-${c.tone}`}>
          {c.text}
        </span>
      ))}
    </div>
  );
}

/** Egress gateway lane handles (one in/out pair per orchestrator routed through it), 18 px apart around the middle. */
function LaneHandles({ lanes, out = true, gap = 26, center = "50%" }: { lanes?: number; out?: boolean; gap?: number; center?: string }) {
  if (!lanes || lanes < 2) return null;
  return (
    <>
      {Array.from({ length: lanes }, (_, i) => {
        const top = `calc(${center} + ${(i - (lanes - 1) / 2) * gap}px)`;
        return (
          <span key={i}>
            <H type="target" pos={Position.Left} id={`in-${i}`} style={{ top }} />
            {out && <H type="source" pos={Position.Right} id={`out-${i}`} style={{ top }} />}
          </span>
        );
      })}
    </>
  );
}

/** One state dot per orchestrator (ringed in that agent's accent) when several agents call this target. */
function AgentDots({ states, fallback }: { states?: AgentState[]; fallback: string }) {
  if (!states?.length) return <span className={`state-dot st-${fallback}`} />;
  return (
    <span className="agent-dots">
      {states.map((a) => (
        <span key={a.idx} className={`state-dot agent-dot st-${a.state} accent-${a.idx}`} title={`${a.name}: ${STATE_LABEL[a.state] ?? a.state}`} />
      ))}
    </span>
  );
}

const UserNode = memo(({ data }: NodeProps<DNode>) => (
  <div className={`node node-user ${data.state ? `ring-${data.state}` : ""} ${data.inFlight ? "in-flight" : ""}`}>
    <div className="node-head">
      <span className="node-icon icon-person">
        <PersonIcon size={20} />
      </span>
      <div className="node-titles">
        <div className="node-title">{data.label}</div>
        <div className="node-sub">{data.sublabel}</div>
      </div>
    </div>
    <H type="source" pos={Position.Right} id="out" />
  </div>
));

const GatewayNode = memo(({ data }: NodeProps<DNode>) => (
  <div className={`node node-gateway ${data.faded ? "faded" : "attached"}`}>
    <H type="target" pos={Position.Left} id="in" />
    <div className="node-head">
      <span className="node-icon icon-gateway">
        <GatewayIcon size={22} />
      </span>
      <div className="node-titles">
        <div className="node-title">{data.label}</div>
        <div className="node-sub">{data.sublabel}</div>
      </div>
    </div>
    <Chips chips={data.chips} />
    {data.shield && (
      <div className="ma-shield" title="Model Armor screening is on">
        <ShieldIcon size={20} />
      </div>
    )}
    <H type="source" pos={Position.Right} id="out" />
    <LaneHandles lanes={data.lanes} />
  </div>
));

const OrchestratorNode = memo(({ data }: NodeProps<DNode>) => (
  <div className={`node node-orch ${data.accent !== undefined ? `has-accent accent-${data.accent}` : ""}`}>
    <H type="target" pos={Position.Left} id="in" />
    <div className="node-badge badge-runtime">
      <SparkleDot /> {data.badge}
    </div>
    <div className="node-head">
      <span className="node-icon icon-orch">
        <RobotIcon size={22} />
      </span>
      <div className="node-titles">
        <div className="node-title">{data.label}</div>
        <div className="node-sub mono">{data.sublabel}</div>
      </div>
    </div>
    <Chips chips={data.chips} />
    <H type="source" pos={Position.Right} id="out" />
    <H type="target" pos={Position.Bottom} id="reg" />
  </div>
));

const SparkleDot = () => <span className="dot" />;

const RegistryNode = memo(({ data }: NodeProps<DNode>) => (
  <div className={`node node-registry ${data.onOpen ? "clickable" : ""}`} title={data.onOpen ? "Open the Agent Registry browser" : undefined}>
    <H type="source" pos={Position.Left} id="left" />
    <div className="node-head">
      <span className="node-icon icon-registry">
        <RegistryIcon size={20} />
      </span>
      <div className="node-titles">
        <div className="node-title">{data.label}</div>
        <div className="node-sub">{data.sublabel}</div>
      </div>
    </div>
    {data.onOpen && (
      <button
        type="button"
        className="node-open-btn nodrag nopan"
        onClick={(e) => {
          e.stopPropagation();
          data.onOpen?.();
        }}
        aria-label="Open the Agent Registry browser"
        title="Open the Agent Registry browser"
      >
        <OpenInNewIcon size={12} />
      </button>
    )}
    <H type="source" pos={Position.Bottom} id="bottom" />
  </div>
));

const A2ANode = memo(({ data }: NodeProps<DNode>) => (
  <div className={`node node-a2a ring-${data.state ?? "unknown"} ${data.inFlight ? "in-flight" : ""}`} title={data.agentStates ? data.agentStates.map((a) => `${a.name}: ${STATE_LABEL[a.state]}`).join(" · ") : STATE_LABEL[data.state ?? "unknown"]}>
    <H type="target" pos={Position.Left} id="in" />
    <LaneHandles lanes={data.agentStates?.length} out={false} gap={16} />
    <H type="target" pos={Position.Right} id="reg" style={{ top: 30 }} />
    <div className="node-badge badge-cloudrun">
      <CloudIcon size={12} /> {data.badge}
    </div>
    <div className="node-head">
      <span className="node-icon icon-a2a">
        <RobotIcon size={20} />
      </span>
      <div className="node-titles">
        <div className="node-title">{data.label}</div>
        <div className="node-sub mono">{data.sublabel}</div>
      </div>
      <AgentDots states={data.agentStates} fallback={data.state ?? "unknown"} />
    </div>
    <div className="skills">
      {data.skills?.map((s) => (
        <div key={s} className="skill">
          <span className="skill-tag">skill</span> {s}
        </div>
      ))}
    </div>
  </div>
));

const McpNode = memo(({ data }: NodeProps<DNode>) => (
  <div className={`node node-mcp ${data.inFlight ? "in-flight" : ""}`}>
    <H type="target" pos={Position.Right} id="reg" style={{ top: 30 }} />
    <div className="node-badge badge-cloudrun">
      <CloudIcon size={12} /> {data.badge}
    </div>
    <div className="node-head">
      <span className="node-icon icon-mcp">
        <ServerIcon size={20} />
      </span>
      <div className="node-titles">
        <div className="node-title">{data.label}</div>
        <div className="node-sub mono">{data.sublabel}</div>
      </div>
    </div>
    <div className="tools">
      {data.tools?.map((t) => (
        <div
          key={t.name}
          className={`tool-row st-${t.state}`}
          title={`${t.name} — ${t.readOnly ? "read-only" : "write / destructive"} — ${t.agentStates ? t.agentStates.map((a) => `${a.name}: ${STATE_LABEL[a.state]}`).join(" · ") : STATE_LABEL[t.state]}`}
        >
          <H type="target" pos={Position.Left} id={`tool-${t.name}`} />
          <span className={`tool-kind ${t.readOnly ? "ro" : "rw"}`}>{t.readOnly ? <BookIcon size={13} /> : <PencilIcon size={13} />}</span>
          <span className="tool-name mono">{t.name}</span>
          <span className={`tool-access ${t.readOnly ? "ro" : "rw"}`}>{t.readOnly ? "read" : "write"}</span>
          <AgentDots states={t.agentStates} fallback={t.state} />
        </div>
      ))}
    </div>
  </div>
));

export const nodeTypes = {
  user: UserNode,
  gateway: GatewayNode,
  orchestrator: OrchestratorNode,
  registry: RegistryNode,
  a2a: A2ANode,
  mcp: McpNode,
};
