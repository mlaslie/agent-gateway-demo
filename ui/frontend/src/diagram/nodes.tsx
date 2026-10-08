import { Handle, Position, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import type { DNode } from "./build";
import { BookIcon, CloudIcon, GatewayIcon, PencilIcon, PersonIcon, RegistryIcon, RobotIcon, ServerIcon, ShieldIcon } from "../components/Icons";
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
  </div>
));

const OrchestratorNode = memo(({ data }: NodeProps<DNode>) => (
  <div className="node node-orch">
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
  <div className="node node-registry">
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
    <H type="source" pos={Position.Bottom} id="bottom" />
  </div>
));

const A2ANode = memo(({ data }: NodeProps<DNode>) => (
  <div className={`node node-a2a ring-${data.state ?? "unknown"} ${data.inFlight ? "in-flight" : ""}`} title={STATE_LABEL[data.state ?? "unknown"]}>
    <H type="target" pos={Position.Left} id="in" />
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
      <span className={`state-dot st-${data.state ?? "unknown"}`} />
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
        <div key={t.name} className={`tool-row st-${t.state}`} title={`${t.name} — ${t.readOnly ? "read-only" : "write / destructive"} — ${STATE_LABEL[t.state]}`}>
          <H type="target" pos={Position.Left} id={`tool-${t.name}`} />
          <span className={`tool-kind ${t.readOnly ? "ro" : "rw"}`}>{t.readOnly ? <BookIcon size={13} /> : <PencilIcon size={13} />}</span>
          <span className="tool-name mono">{t.name}</span>
          <span className={`tool-access ${t.readOnly ? "ro" : "rw"}`}>{t.readOnly ? "read" : "write"}</span>
          <span className={`state-dot st-${t.state}`} />
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
