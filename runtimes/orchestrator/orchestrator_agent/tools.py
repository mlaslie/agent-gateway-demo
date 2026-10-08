"""The orchestrator's three function tools (docs/CONTRACTS.md §6).

No McpToolset / RemoteA2aAgent is bound at import time: if the gateway denies a target, a
pre-bound toolset would break the whole agent. These tools connect per call and never raise.
"""
from __future__ import annotations

from typing import Any

from . import remote, spec


def list_capabilities() -> dict[str, Any]:
    """List the remote A2A agents and MCP servers (with their tools) this agent may try to use.

    Returns:
        {"agents": [{id, display_name, description, skills}], "mcp_servers": [{id, display_name, description, tools}]}
    """
    agents, servers = [], []
    for c in spec.topology():
        if c["kind"] == "a2a_agent":
            agents.append({"id": c["id"], "display_name": c.get("display_name", c["id"]),
                           "description": c.get("description", ""),
                           "skills": [{"id": s["id"], "name": s.get("name", ""), "description": s.get("description", "")}
                                      for s in c.get("skills", [])]})
        else:
            servers.append({"id": c["id"], "display_name": c.get("display_name", c["id"]),
                            "description": c.get("description", ""),
                            "tools": [{"name": t["name"], "description": t.get("description", ""),
                                       "read_only": t.get("read_only", True), "params": t.get("params", {})}
                                      for t in c.get("tools", [])]})
    return {"agents": agents, "mcp_servers": servers}


async def call_agent(agent_id: str, message: str) -> dict[str, Any]:
    """Send a message to a remote agent over A2A and return its reply.

    Args:
        agent_id: Id of the remote agent, as listed by list_capabilities (e.g. "kb-agent").
        message: The request for that agent, in natural language.

    Returns:
        {"edge", "outcome": "ok|denied|blocked|error", "http_status", "detail", "result": reply text, "latency_ms"}
    """
    return await remote.call_a2a(agent_id, message)


async def call_mcp_tool(server_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool on a remote MCP server.

    Args:
        server_id: Id of the MCP server, as listed by list_capabilities (e.g. "tickets-mcp").
        tool: Tool name on that server (e.g. "get_ticket").
        arguments: Tool arguments as an object, e.g. {"ticket_id": "INC-1042"}. Use {} when there are none.

    Returns:
        {"edge", "outcome": "ok|denied|blocked|error", "http_status", "detail", "result": tool output, "latency_ms"}
    """
    return await remote.call_mcp(server_id, tool, arguments)
