"""Direct OpenAI-compatible chat.completions against LiteLLM (no crewAI).

More reliable for structured RouterDecision JSON than Agent.kickoff + response_format.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


def chat_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.1,
    timeout_sec: float = 45.0,
) -> str:
    """POST /chat/completions; return assistant message content (string)."""
    import httpx

    base = (settings.litellm_url or "http://litellm:4000/v1").rstrip("/")
    url = f"{base}/chat/completions"
    model_name = model or settings.pipeline_router_model or "gemini-flash"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.litellm_api_key or 'sk-proxy-key'}",
    }
    body: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    # Prefer JSON object mode when proxy/model supports it
    body_json = {**body, "response_format": {"type": "json_object"}}

    with httpx.Client(timeout=timeout_sec) as client:
        try:
            resp = client.post(url, headers=headers, json=body_json)
            if resp.status_code >= 400:
                logger.warning(
                    "router_http json_mode failed status=%s body=%s",
                    resp.status_code,
                    resp.text[:300],
                )
                resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
        except Exception:
            # last attempt without response_format already handled; re-raise
            raise

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("empty choices from LiteLLM")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if content is None:
            # DeepSeek-R1 style: reasoning only
            content = msg.get("reasoning_content") or msg.get("reasoning")
        if not content:
            raise ValueError(f"empty message content keys={list(msg.keys())}")
        if isinstance(content, list):
            # multimodal fragments
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text") or "")
                elif isinstance(part, str):
                    parts.append(part)
            content = "".join(parts)
        return str(content)


def router_user_payload(message: str) -> str:
    schema_hint = {
        "intent": "list_vms|vm_start|vm_stop|vm_reboot|snapshot_create|snapshot_list|uptime|diagnose|unknown",
        "confidence": 0.0,
        "severity": "info|low|medium|high|critical",
        "route": "worker|crisis|clarify",
        "missing_params": ["vmid"],
        "extracted_params": {"vmid": 300},
        "rationale": "short audit note",
    }
    return (
        f"Operator message: {message!r}\n"
        "Return ONLY a JSON object with fields: intent, confidence, severity, "
        "route, missing_params, extracted_params, rationale.\n"
        f"Example shape: {json.dumps(schema_hint, ensure_ascii=False)}\n"
        "Rules: never invent VMIDs; if write needs vmid and none present, "
        "route=clarify and missing_params=[\"vmid\"]; list inventory → "
        "route=worker intent=list_vms missing_params=[]."
    )
