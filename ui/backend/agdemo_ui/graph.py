"""Diagram nodes and edges for a theme (docs/CONTRACTS.md §1, §2)."""
from __future__ import annotations

from typing import Any

from agdemo_core.config import DemoConfig
from agdemo_core.themes import INGRESS_EDGE, Theme


def _pos(theme: Theme, node_id: str) -> dict[str, Any]:
    p = theme.layout.get(node_id)
    return {"position": {"x": p[0], "y": p[1]}} if p else {}


def build_graph(theme: Theme, cfg: DemoConfig | None = None, state: dict | None = None) -> dict[str, Any]:
    state = state or {}
    egress_name = cfg.egress_gateway if cfg else "egress gateway"
    ingress_name = cfg.ingress_gateway if cfg else "ingress gateway"

    nodes: list[dict[str, Any]] = [
        {"id": "user", "type": "user", "label": "User", "sublabel": "Presenter / client app"},
        {"id": "ingress_gateway", "type": "gateway", "label": "Agent Gateway (ingress)",
         "sublabel": f"CLIENT_TO_AGENT · {ingress_name}"},
        {"id": "orchestrator", "type": "orchestrator", "label": theme.orchestrator.display_name,
         "sublabel": f"Agent Runtime · {theme.orchestrator.id}"},
        {"id": "egress_gateway", "type": "gateway", "label": "Agent Gateway (egress)",
         "sublabel": f"AGENT_TO_ANYWHERE · {egress_name}"},
        {"id": "registry", "type": "registry", "label": "Agent Registry",
         "sublabel": cfg.region if cfg else "regional"},
    ]
    for n in nodes:
        n.update(_pos(theme, n["id"]))

    edges: list[dict[str, Any]] = []
    for c in theme.components:
        if c.kind == "a2a_agent":
            spec = theme.a2a_agents[c.id]
            nodes.append({"id": c.id, "type": "a2a_agent", "label": spec.display_name,
                          "sublabel": c.role_label or "A2A agent", "description": spec.description,
                          "skills": [{"id": s.id, "name": s.name, "description": s.description}
                                     for s in spec.skills], **_pos(theme, c.id)})
            edges.append({"id": c.id, "source": "orchestrator", "target": c.id, "kind": "a2a"})
        else:
            spec = theme.mcp_servers[c.id]
            nodes.append({"id": c.id, "type": "mcp_server", "label": spec.display_name,
                          "sublabel": c.role_label or "MCP server", "description": spec.description,
                          "tools": [{"name": t.name, "read_only": t.read_only, "description": t.description,
                                     "edge": f"{c.id}:{t.name}"} for t in spec.tools],
                          **_pos(theme, c.id)})
            for t in spec.tools:
                edges.append({"id": f"{c.id}:{t.name}", "source": "orchestrator", "target": c.id,
                              "tool": t.name, "kind": "mcp"})
    edges.append({"id": INGRESS_EDGE, "source": "user", "target": "orchestrator", "kind": "ingress"})
    return {"theme": theme.model_dump(mode="json"), "nodes": nodes, "edges": edges}
