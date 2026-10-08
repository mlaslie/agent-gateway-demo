"""Generic, config-driven ADK agent served over A2A (Cloud Run) for the Agent Gateway demo.

The agent is built from COMPONENT_SPEC (an A2AAgentSpec, see agdemo_core/themes.py):

    {"display_name": "KB Agent", "description": "...", "instruction": "...", "knowledge": "...",
     "skills": [{"id": "search_kb", "name": "...", "description": "...", "tags": [], "examples": []}],
     "model": null, "probe_message": "..."}

Env:
  COMPONENT_SPEC            base64 JSON (or raw JSON) A2AAgentSpec; or COMPONENT_SPEC_FILE=<path>
  MODEL                     Gemini model (default: spec.model, else gemini-2.5-flash)
  PUBLIC_URL                externally reachable base URL, advertised in the agent card
  GOOGLE_GENAI_USE_VERTEXAI TRUE (Vertex AI), with GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION
  PORT                      listen port (default 8080)

Endpoints: JSON-RPC at "/", agent card at /.well-known/agent-card.json, GET /healthz.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

from google.adk.a2a import _compat as a2a_compat  # version-agnostic AgentCard builder (a2a-sdk 0.3 / 1.x)
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.agents import LlmAgent
from starlette.requests import Request
from starlette.responses import JSONResponse

DEFAULT_MODEL = "gemini-2.5-flash"


def load_spec(env_var: str = "COMPONENT_SPEC") -> dict[str, Any]:
    """Read the component spec from $COMPONENT_SPEC (base64 or raw JSON) or $COMPONENT_SPEC_FILE."""
    if path := os.environ.get(f"{env_var}_FILE"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        raise RuntimeError(f"{env_var} (or {env_var}_FILE) is not set")
    return json.loads(raw if raw.startswith("{") else base64.b64decode(raw))


def build_agent(spec: dict[str, Any]) -> LlmAgent:
    instruction = spec["instruction"].strip()
    if spec.get("knowledge"):
        instruction += "\n\nKnowledge:\n" + spec["knowledge"].strip()
    return LlmAgent(
        # ADK agent names must be Python identifiers.
        name=re.sub(r"\W", "_", spec["display_name"].lower()).strip("_") or "agent",
        description=spec.get("description", ""),
        model=os.environ.get("MODEL") or spec.get("model") or DEFAULT_MODEL,
        # The instruction is static text; disable {state} templating so braces in the
        # knowledge block are not interpreted as session-state placeholders.
        instruction=lambda _ctx, text=instruction: text,
    )


def build_card(spec: dict[str, Any], public_url: str):
    """Agent card whose skills come from the spec and whose RPC url is PUBLIC_URL."""
    skills = [{"id": s["id"], "name": s["name"], "description": s["description"],
               "tags": s.get("tags", []), "examples": s.get("examples", [])} for s in spec["skills"]]
    return a2a_compat.build_agent_card(
        name=spec["display_name"],
        description=spec.get("description", ""),
        version="1.0.0",
        url=public_url,
        protocol_binding=getattr(a2a_compat.TP_JSONRPC, "value", a2a_compat.TP_JSONRPC),
        skills=skills,
    )


def create_app():
    spec = load_spec()
    port = int(os.environ.get("PORT", "8080"))
    public_url = os.environ.get("PUBLIC_URL") or f"http://localhost:{port}"
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", os.environ["GOOGLE_GENAI_USE_VERTEXAI"])

    app = to_a2a(build_agent(spec), agent_card=build_card(spec, public_url))

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "agent": spec["display_name"]})

    app.add_route("/healthz", healthz, methods=["GET"])
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
