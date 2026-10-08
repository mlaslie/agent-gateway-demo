"""gcloud subprocess helper (used for Cloud Build / Cloud Run deploys and auth checks only)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

_CANDIDATES = [
    "~/google-cloud-sdk/bin/gcloud",
    "~/Downloads/google-cloud-sdk/bin/gcloud",
    "/opt/homebrew/bin/gcloud",
    "/usr/local/bin/gcloud",
    "/usr/bin/gcloud",
    "/snap/bin/gcloud",
]


class GcloudError(Exception):
    pass


@lru_cache(maxsize=1)
def gcloud_path() -> str:
    """$AGDEMO_GCLOUD, then PATH, then common install locations."""
    env = os.environ.get("AGDEMO_GCLOUD")
    if env:
        return env
    found = shutil.which("gcloud")
    if found:
        return found
    root = os.environ.get("CLOUDSDK_ROOT_DIR")
    if root and (Path(root) / "bin/gcloud").exists():
        return str(Path(root) / "bin/gcloud")
    for c in _CANDIDATES:
        p = Path(c).expanduser()
        if p.exists():
            return str(p)
    raise GcloudError("gcloud not found: install the Google Cloud SDK or set AGDEMO_GCLOUD=/path/to/gcloud")


def run(args: list[str], *, check: bool = True, capture: bool = True, json_out: bool = False,
        cwd: str | None = None, timeout: float | None = None) -> Any:
    cmd = [gcloud_path(), *args]
    if json_out:
        cmd.append("--format=json")
    env = {**os.environ, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"}
    p = subprocess.run(cmd, cwd=cwd, env=env, text=True, timeout=timeout,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if check and p.returncode != 0:
        raise GcloudError(f"gcloud {' '.join(args[:4])}... failed ({p.returncode}): "
                          f"{(p.stderr or '').strip()[-1500:]}")
    if json_out:
        try:
            return json.loads(p.stdout or "null")
        except json.JSONDecodeError:
            return None
    return p.stdout if capture else p.returncode


def value(args: list[str]) -> str:
    try:
        return (run(args, check=True) or "").strip()
    except GcloudError:
        return ""
