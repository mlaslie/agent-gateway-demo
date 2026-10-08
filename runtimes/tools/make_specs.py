"""Build runtime specs (docs/CONTRACTS.md §5) from a theme pack.

The CLI (`./agdemo deploy-theme`) uses the same functions:

    encode_spec(obj)                      -> base64(JSON) string for the COMPONENT_SPEC / ORCHESTRATOR_SPEC env var
    component_spec(theme, component_id)   -> McpServerSpec / A2AAgentSpec as a plain dict
    topology(theme, urls)                 -> the orchestrator's `topology` list
    orchestrator_spec(theme, urls)        -> Orchestrator fields + `topology` + resolved `model`

`urls` maps component id -> base URL of its Cloud Run service (no trailing slash, no /mcp);
MCP entries get "/mcp" appended here.

CLI usage (prints shell `export` lines):
    uv run python runtimes/tools/make_specs.py helpdesk kb-agent=http://localhost:8101 ...
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for agdemo_core

from agdemo_core.themes import Theme, load_theme  # noqa: E402


def encode_spec(obj: Any) -> str:
    """Compact JSON, then standard base64 (no newlines). Runtimes also accept raw JSON."""
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()


def component_spec(theme: Theme, component_id: str) -> dict[str, Any]:
    kind = theme.component(component_id).kind
    src = theme.mcp_servers if kind == "mcp_server" else theme.a2a_agents
    return src[component_id].model_dump(mode="json")


def topology(theme: Theme, urls: dict[str, str]) -> list[dict[str, Any]]:
    """What the orchestrator may try to call. probe_message / probe_args are extensions used by probe mode."""
    out: list[dict[str, Any]] = []
    for c in theme.components:
        base = urls[c.id].rstrip("/")
        if c.kind == "a2a_agent":
            a = theme.a2a_agents[c.id]
            out.append({"id": c.id, "kind": c.kind, "display_name": a.display_name, "url": base,
                        "description": a.description, "skills": [s.model_dump() for s in a.skills],
                        "probe_message": a.probe_message})
        else:
            m = theme.mcp_servers[c.id]
            out.append({"id": c.id, "kind": c.kind, "display_name": m.display_name, "url": f"{base}/mcp",
                        "description": m.description,
                        "tools": [{"name": t.name, "read_only": t.read_only, "description": t.description,
                                   "params": {k: p.model_dump() for k, p in t.params.items()},
                                   "probe_args": t.probe_args} for t in m.tools]})
    return out


def orchestrator_spec(theme: Theme, urls: dict[str, str], default_model: str = "gemini-2.5-flash") -> dict[str, Any]:
    spec = theme.orchestrator.model_dump(mode="json")
    spec["model"] = spec.get("model") or default_model
    spec["topology"] = topology(theme, urls)
    return spec


def _env_name(cid: str) -> str:
    return cid.upper().replace("-", "_")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    th = load_theme(sys.argv[1])
    url_map = dict(arg.split("=", 1) for arg in sys.argv[2:])
    for comp in th.components:
        print(f"export SPEC_{_env_name(comp.id)}={encode_spec(component_spec(th, comp.id))}")
    if all(c.id in url_map for c in th.components):
        print(f"export ORCHESTRATOR_SPEC={encode_spec(orchestrator_spec(th, url_map))}")
