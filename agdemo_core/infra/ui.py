"""./agdemo ui deploy|local.

deploy: Cloud Build (repo root context, -f ui/Dockerfile) -> Artifact Registry -> Cloud Run with IAP
(`gcloud beta run deploy --iap`), running as the <prefix>-ui service account. Config and state are passed as
env vars AGDEMO_CONFIG_YAML / AGDEMO_STATE_JSON (re-run `ui deploy --skip-build` after deploy-theme so the
UI sees new state). Viewers get roles/iap.httpsResourceAccessor on the service (ui.iap_access + admin_access).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import yaml

from ..config import CONFIG_DIR, REPO_ROOT, DemoConfig, config_path
from ..gcp import gcloud, project
from ..gcp.rest import project_number
from ..state import STATE_PATH, load_state, update_state
from . import common as c


def image(cfg: DemoConfig) -> str:
    return f"{cfg.region}-docker.pkg.dev/{cfg.project}/{c.repo_id(cfg)}/ui:latest"


def build(cfg: DemoConfig) -> None:
    gen = CONFIG_DIR / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    cb = gen / "ui-cloudbuild.yaml"
    cb.write_text(yaml.safe_dump({
        "steps": [{"name": "gcr.io/cloud-builders/docker",
                   "args": ["build", "-f", "ui/Dockerfile", "-t", image(cfg), "."]}],
        "images": [image(cfg)],
        "options": {"logging": "CLOUD_LOGGING_ONLY"},
    }))
    c.log(f"  building {image(cfg)} with Cloud Build")
    gcloud.run(["builds", "submit", str(REPO_ROOT), f"--config={cb}", f"--project={cfg.project}",
                f"--region={cfg.region}", "--quiet"], timeout=2400)


def deploy(cfg: DemoConfig, skip_build: bool = False) -> str:
    r = c.client(cfg)
    if not skip_build:
        build(cfg)
    svc = c.ui_service_name(cfg)
    ui_sa = c.sa(cfg, c.ui_sa_id(cfg))
    env = {"AGDEMO_CONFIG_YAML": config_path().read_text(),
           "AGDEMO_STATE_JSON": json.dumps(load_state()),
           "AGDEMO_RECORDINGS_DIR": "/tmp/recordings"}
    envf = CONFIG_DIR / "generated" / "ui.env.yaml"
    envf.write_text(yaml.safe_dump(env))
    labels = ",".join(f"{k}={v}" for k, v in cfg.labels.items())
    c.log(f"  deploying Cloud Run {svc} with IAP")
    gcloud.run(["beta", "run", "deploy", svc, f"--image={image(cfg)}", f"--region={cfg.region}",
                f"--project={cfg.project}", f"--service-account={ui_sa}", f"--env-vars-file={envf}",
                f"--labels={labels}", "--no-allow-unauthenticated", "--iap", "--min-instances=0",
                "--max-instances=2", "--memory=1Gi", "--timeout=900", "--quiet"], timeout=900)
    # IAP service agent must be able to invoke the service
    num = project_number(cfg.project)
    gcloud.run(["run", "services", "add-iam-policy-binding", svc, f"--region={cfg.region}",
                f"--project={cfg.project}", f"--member=serviceAccount:service-{num}@gcp-sa-iap.iam.gserviceaccount.com",
                "--role=roles/run.invoker", "--quiet"])
    for m in sorted(set(cfg.ui.iap_access) | set(cfg.ui.admin_access)):
        gcloud.run(["beta", "iap", "web", "add-iam-policy-binding", "--resource-type=cloud-run",
                    f"--service={svc}", f"--region={cfg.region}", f"--project={cfg.project}",
                    f"--member={m}", "--role=roles/iap.httpsResourceAccessor", "--condition=None", "--quiet"])
        c.log(f"  IAP access for {m}")
    url = gcloud.value(["run", "services", "describe", svc, f"--region={cfg.region}", f"--project={cfg.project}",
                        "--format=value(status.url)"])
    update_state("shared", "ui_url", value=url)
    c.log(f"[green]UI:[/] {url}")
    return url


def local(mode: str | None = None, port: int = 8080, reload: bool = False) -> None:
    env = dict(os.environ)
    if mode:
        env["AGDEMO_DEFAULT_MODE"] = mode
    env.setdefault("AGDEMO_ADMIN_ALL", "1")
    cmd = [sys.executable, "-m", "uvicorn", "agdemo_ui.main:app", "--port", str(port), "--host", "127.0.0.1"]
    if reload:
        cmd.append("--reload")
    c.log(f"UI on http://127.0.0.1:{port}  (mode: {mode or 'from config'})")
    subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, check=False)
