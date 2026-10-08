"""Generic, config-driven MCP server (Streamable HTTP) for the Agent Gateway demo.

Every tool is generated from COMPONENT_SPEC (a McpServerSpec, see agdemo_core/themes.py):

    {"display_name": "...", "description": "...", "instructions": "...",
     "tools": [{"name": "get_ticket", "description": "...", "read_only": true,
                "params": {"ticket_id": {"type": "string", "description": "...", "required": true}},
                "response": {"id": "{ticket_id}", ...}}]}

A tool call returns the tool's mock `response` with `{param}` placeholders filled in from the
call arguments. `read_only` becomes the MCP annotations readOnlyHint / destructiveHint, which
is what tool-level gateway policies key on.

Env:
  COMPONENT_SPEC       base64 JSON (or raw JSON) McpServerSpec
  COMPONENT_SPEC_FILE  alternative: path to a JSON file (for large specs)
  PORT                 listen port (default 8080)

Endpoints: POST/GET /mcp (Streamable HTTP, stateless, JSON responses) and GET /healthz.
"""
from __future__ import annotations

import base64
import inspect
import json
import os
import re
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

_PY_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def load_spec(env_var: str = "COMPONENT_SPEC") -> dict[str, Any]:
    """Read the component spec from $COMPONENT_SPEC (base64 or raw JSON) or $COMPONENT_SPEC_FILE."""
    if path := os.environ.get(f"{env_var}_FILE"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        raise RuntimeError(f"{env_var} (or {env_var}_FILE) is not set")
    return json.loads(raw if raw.startswith("{") else base64.b64decode(raw))


def fill(template: Any, args: dict[str, Any]) -> Any:
    """Recursively substitute {param} placeholders in strings with call arguments.

    A string that is exactly "{param}" takes the argument's value unchanged (keeps its type);
    unknown placeholders are left as-is.
    """
    if isinstance(template, str):
        whole = _PLACEHOLDER.fullmatch(template)
        if whole and whole.group(1) in args:
            return args[whole.group(1)]
        return _PLACEHOLDER.sub(lambda m: str(args.get(m.group(1), m.group(0))), template)
    if isinstance(template, list):
        return [fill(v, args) for v in template]
    if isinstance(template, dict):
        return {k: fill(v, args) for k, v in template.items()}
    return template


def make_tool_fn(tool: dict[str, Any]):
    """Build an async function whose signature mirrors the tool's `params`.

    MCPServer derives the tool's JSON input schema from the function signature, so we
    synthesize one: required params have no default, optional params default to None.
    """
    response = tool.get("response")

    async def _tool(**kwargs: Any) -> Any:
        args = {k: v for k, v in kwargs.items() if v is not None}
        return fill(response, args)

    params = []
    for pname, p in (tool.get("params") or {}).items():
        py_type = _PY_TYPES.get(p.get("type", "string"), str)
        if not p.get("required", False):
            py_type = py_type | None
        annotation = Annotated[py_type, Field(description=p.get("description", ""))]
        default = inspect.Parameter.empty if p.get("required", False) else None
        params.append(inspect.Parameter(pname, inspect.Parameter.KEYWORD_ONLY,
                                        annotation=annotation, default=default))
    _tool.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    _tool.__name__ = tool["name"]
    return _tool


def build_server(spec: dict[str, Any]) -> MCPServer:
    server = MCPServer(
        name=spec.get("display_name", "mcp-server"),
        description=spec.get("description") or None,
        instructions=spec.get("instructions") or None,
    )
    for tool in spec["tools"]:
        read_only = bool(tool.get("read_only", True))
        server.add_tool(
            make_tool_fn(tool),
            name=tool["name"],
            description=tool.get("description", ""),
            annotations=ToolAnnotations(
                readOnlyHint=read_only,
                destructiveHint=not read_only,
                idempotentHint=read_only,
                openWorldHint=False,
            ),
            # Mock responses are arbitrary JSON (lists, dicts); return them as JSON text content.
            structured_output=False,
        )

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": spec.get("display_name"),
                             "tools": [t["name"] for t in spec["tools"]]})

    return server


def create_app():
    """ASGI app. Stateless + JSON responses so any replica (Cloud Run, behind a gateway) can serve any call.

    host="0.0.0.0" disables the SDK's localhost-only DNS-rebinding guard, which would otherwise
    reject the Cloud Run / gateway Host header.
    """
    return build_server(load_spec()).streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
