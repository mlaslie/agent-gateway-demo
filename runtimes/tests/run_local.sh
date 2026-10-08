#!/usr/bin/env bash
# Local end-to-end check of runtimes/ (MCP servers, A2A agents, orchestrator tools + probe protocol).
# Needs ADC with Vertex AI access (gcloud auth application-default login). See test_runtimes.py.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run pytest runtimes/tests -v "$@"
