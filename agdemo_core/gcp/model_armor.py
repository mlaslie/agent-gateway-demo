"""Model Armor template (REST, https://modelarmor.<region>.rep.googleapis.com/v1)."""
from __future__ import annotations

from typing import Any

from ..config import DemoConfig
from .rest import GcpError, Rest, modelarmor

# Text the gateway returns to the caller when Model Armor blocks a prompt (custom safety error message).
BLOCK_MESSAGE_MARKER = "agdemo-model-armor-block"


def template_name(cfg: DemoConfig) -> str:
    return f"projects/{cfg.project}/locations/{cfg.region}/templates/{cfg.model_armor_template}"


def template_body(cfg: DemoConfig) -> dict[str, Any]:
    f = set(cfg.model_armor.filters)
    fc: dict[str, Any] = {}
    if "prompt_injection_jailbreak" in f:
        fc["piAndJailbreakFilterSettings"] = {"filterEnforcement": "ENABLED", "confidenceLevel": "MEDIUM_AND_ABOVE"}
    if "sensitive_data" in f:
        fc["sdpSettings"] = {"basicConfig": {"filterEnforcement": "ENABLED"}}
    if "malicious_uri" in f:
        fc["maliciousUriFilterSettings"] = {"filterEnforcement": "ENABLED"}
    if "rai" in f:
        fc["raiSettings"] = {"raiFilters": [
            {"filterType": t, "confidenceLevel": "MEDIUM_AND_ABOVE"}
            for t in ("HATE_SPEECH", "HARASSMENT", "SEXUALLY_EXPLICIT", "DANGEROUS")]}
    return {
        "filterConfig": fc,
        "templateMetadata": {
            "logSanitizeOperations": True,
            "logTemplateOperations": True,
            "customPromptSafetyErrorCode": 403,
            "customPromptSafetyErrorMessage": f"{BLOCK_MESSAGE_MARKER}: prompt blocked by Model Armor",
            "customLlmResponseSafetyErrorCode": 403,
            "customLlmResponseSafetyErrorMessage": f"{BLOCK_MESSAGE_MARKER}: response blocked by Model Armor",
        },
        "labels": cfg.labels,
    }


def get_template(r: Rest, cfg: DemoConfig) -> dict | None:
    return r.get(f"{modelarmor(cfg.region)}/{template_name(cfg)}", ok404=True)


def ensure_template(r: Rest, cfg: DemoConfig) -> str:
    body = template_body(cfg)
    cur = get_template(r, cfg)
    base = f"{modelarmor(cfg.region)}/projects/{cfg.project}/locations/{cfg.region}/templates"
    if cur is None:
        try:
            r.post(base, json=body, params={"templateId": cfg.model_armor_template})
            return "created"
        except GcpError as e:
            if not e.conflict:
                raise
            return "ok"
    if cur.get("filterConfig") != body["filterConfig"]:
        r.patch(f"{modelarmor(cfg.region)}/{template_name(cfg)}", json=body,
                params={"updateMask": "filterConfig,templateMetadata"})
        return "updated"
    return "ok"


def delete_template(r: Rest, cfg: DemoConfig) -> bool:
    try:
        r.delete(f"{modelarmor(cfg.region)}/{template_name(cfg)}")
        return True
    except GcpError as e:
        if e.not_found:
            return False
        raise


def sanitize_prompt(r: Rest, cfg: DemoConfig, text: str) -> dict:
    """Direct template test (no gateway): returns the sanitizationResult."""
    return r.post(f"{modelarmor(cfg.region)}/{template_name(cfg)}:sanitizeUserPrompt",
                  json={"userPromptData": {"text": text}})
