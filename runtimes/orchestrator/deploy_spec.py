"""What `./agdemo deploy-theme` needs to deploy the orchestrator to Agent Runtime (Vertex AI Agent Engine).

Usage from the CLI (no GCP calls are made in this module):

    from runtimes.orchestrator import deploy_spec            # or load it by file path
    with deploy_spec.staged(env) as (root_agent, cfg):        # env = env_vars(...) below
        app = vertexai.agent_engines.AdkApp(agent=root_agent)
        cfg.update(display_name=..., staging_bucket=..., labels=..., identity_type=IDENTITY_TYPE)
        engine = client.agent_engines.create(agent=app, config=cfg)   # or .update(name=..., agent=app, config=cfg)

Why the context manager:
- `extra_packages` paths are tarred as given (relative to the CWD) and extracted at the same
  relative path on the server, where the CWD is on sys.path. The package must therefore be
  passed as "orchestrator_agent" with CWD = this directory, so `import orchestrator_agent` works
  on the server and cloudpickle can resolve its functions by reference.
- The agent is pickled on the deploy host. Its instruction and tools read env on the server,
  but `name`/`model` are captured at import, so the same env vars are set locally first.
"""
from __future__ import annotations

import contextlib
import importlib
import os
import sys
from pathlib import Path
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent

PACKAGE = "orchestrator_agent"
ROOT_AGENT = "orchestrator_agent.agent:root_agent"   # module path : attribute
EXTRA_PACKAGES = [PACKAGE]                            # relative to HERE (see `staged`)
IDENTITY_TYPE = "AGENT_IDENTITY"                      # vertexai types.IdentityType.AGENT_IDENTITY

# Pinned to what the runtimes were verified with locally; the server must match for unpickling.
REQUIREMENTS = [
    "google-cloud-aiplatform[agent_engines,adk]==2.4.0",
    "google-adk==2.11.0",
    "google-genai==2.28.0",
    "mcp==2.3.0",
    "httpx==0.28.1",
    "google-auth==2.60.0",
    "requests==2.34.2",
    "cloudpickle==3.1.2",
    "pydantic==2.13.5",
]


def env_vars(orchestrator_spec_b64: str, model: str, auth_mode: str = "none") -> dict[str, str]:
    """Env vars for the engine. GOOGLE_CLOUD_PROJECT / _LOCATION are set by Agent Runtime itself."""
    if auth_mode not in ("none", "id_token"):
        raise ValueError("auth_mode must be 'none' or 'id_token'")
    return {
        "ORCHESTRATOR_SPEC": orchestrator_spec_b64,
        "MODEL": model,
        "AUTH_MODE": auth_mode,
        "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
        "GOOGLE_GENAI_USE_ENTERPRISE": "TRUE",   # newer google-genai name for the same switch
    }


def create_config(env: dict[str, str]) -> dict[str, Any]:
    """Base `config` for client.agent_engines.create/update; the CLI adds display_name, bucket, labels..."""
    return {
        "requirements": list(REQUIREMENTS),
        "extra_packages": list(EXTRA_PACKAGES),
        "env_vars": dict(env),
        "identity_type": IDENTITY_TYPE,
    }


@contextlib.contextmanager
def staged(env: dict[str, str]) -> Iterator[tuple[Any, dict[str, Any]]]:
    """Import `root_agent` with `env` applied and CWD = this directory; yields (root_agent, config).

    Call agent_engines.create(...) inside the `with` block (packaging happens during that call).
    """
    old_cwd, old_env = os.getcwd(), {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.path.insert(0, str(HERE))
    os.chdir(HERE)
    try:
        for name in [m for m in sys.modules if m == PACKAGE or m.startswith(PACKAGE + ".")]:
            del sys.modules[name]          # re-import so the new env is picked up
        module_name, attr = ROOT_AGENT.split(":")
        root_agent = getattr(importlib.import_module(module_name), attr)
        yield root_agent, create_config(env)
    finally:
        os.chdir(old_cwd)
        sys.path.remove(str(HERE))
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
