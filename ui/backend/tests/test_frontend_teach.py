"""Frontend teaching modules (ui/frontend/src/teach): error decoder, "takes effect in" timing and glossary.

They are plain TypeScript tested with `node --test` (no npm dependencies). This runs them under pytest so
`uv run pytest ui/backend/tests` covers them too; skipped when Node can't run TypeScript (Node < 22.18).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
TESTS = sorted(str(p.relative_to(FRONTEND)) for p in (FRONTEND / "src" / "teach").glob("*.test.ts"))


def _node_runs_ts() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    m = re.match(r"v(\d+)\.(\d+)", out)
    return bool(m) and (int(m[1]), int(m[2])) >= (22, 18)


@pytest.mark.skipif(not _node_runs_ts(), reason="needs Node >= 22.18 (built-in TypeScript type stripping)")
def test_teach_modules():
    assert TESTS, "no src/teach/*.test.ts found"
    r = subprocess.run(["node", "--test", *TESTS], cwd=FRONTEND, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
