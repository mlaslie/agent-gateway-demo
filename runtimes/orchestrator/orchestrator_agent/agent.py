"""Generic orchestrator agent for Agent Runtime (Vertex AI Agent Engine), configured by ORCHESTRATOR_SPEC.

Env (read on the server):
  ORCHESTRATOR_SPEC   base64 JSON: theme Orchestrator + topology (see spec.py)
  MODEL               Gemini model (default: spec.model, else gemini-2.5-flash)
  AUTH_MODE           none | id_token

Note on deploy: Agent Engine pickles `root_agent` on the deploy host. The instruction and tools
are module-level functions (pickled by reference, run on the server, read env there), but the
agent `name` and `model` string are captured at import. The deploy helper (deploy_spec.py)
therefore sets the same env vars locally before importing this module.
"""
from __future__ import annotations

import re

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from . import spec
from .probe import probe_callback
from .tools import call_agent, call_mcp_tool, list_capabilities

_TOOL_RULES = """
Tools:
- list_capabilities() lists the remote agents and MCP servers (and their tools) you may try to use.
- call_agent(agent_id, message) talks to a remote agent over A2A.
- call_mcp_tool(server_id, tool, arguments) calls a tool on an MCP server.
Each tool returns an "outcome": ok, denied (blocked by access policy), blocked (blocked by Model Armor)
or error. Report denied/blocked outcomes plainly, naming the agent or tool, and do not retry another way.
"""


def instruction(_ctx: ReadonlyContext) -> str:
    """Instruction provider: resolved per request from the server's ORCHESTRATOR_SPEC."""
    base = spec.load().get("instruction") or "You are a helpful orchestrator agent."
    return base.strip() + "\n" + _TOOL_RULES


def _agent_name() -> str:
    return re.sub(r"\W", "_", spec.load().get("id", "orchestrator")).strip("_") or "orchestrator"


root_agent = LlmAgent(
    name=_agent_name(),
    description=spec.load().get("description", "Orchestrator agent"),
    model=spec.model(),
    instruction=instruction,
    tools=[list_capabilities, call_agent, call_mcp_tool],
    before_model_callback=probe_callback,
)
