import os

# Instant pacing, no deployment state, demo-only by default.
os.environ["AGDEMO_PACE"] = "0"
os.environ.setdefault("AGDEMO_STATE", "/nonexistent/agdemo-state.json")
os.environ.pop("K_SERVICE", None)
