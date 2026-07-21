"""Router agent: Gemini Flash structured classification → RouterDecision."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from pipeline.intent_heuristics import try_deterministic_decision
from pipeline.llms import make_llm
from pipeline.models import RouterDecision
from pipeline.router_http import chat_json, router_user_payload

logger = logging.getLogger(__name__)

ROUTER_SYSTEM = (
    "You are a Homelab Request Router for a Proxmox home lab.\n"
    "Classify the operator message. Never invent VM IDs or write targets.\n"
    "If required params are missing for a write, set route=clarify and list missing_params.\n"
    "Read-only inventory requests (list VMs/CTs) → route=worker, intent=list_vms, "
    "missing_params=[] — never clarify for list-only reads.\n"
    "Prefer route=worker for routine ops; route=crisis only for structural failures.\n"
    "Return JSON matching RouterDecision fields only."
)


def _extract_json_object(text: str) -> str:
    """Best-effort extract of a JSON object from model prose."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty router output")
    # strip markdown fences
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start : end + 1]
    return raw


def parse_router_decision(raw: Any) -> RouterDecision:
    """Validate RouterDecision from pydantic model, dict, or JSON string."""
    if isinstance(raw, RouterDecision):
        return raw
    if hasattr(raw, "pydantic") and raw.pydantic is not None:
        p = raw.pydantic
        if isinstance(p, RouterDecision):
            return p
        if isinstance(p, dict):
            return RouterDecision.model_validate(p)
    if hasattr(raw, "raw") and raw.raw is not None and not isinstance(raw, (str, dict)):
        return parse_router_decision(raw.raw)
    if isinstance(raw, dict):
        return RouterDecision.model_validate(raw)
    if isinstance(raw, str):
        return RouterDecision.model_validate_json(_extract_json_object(raw))
    raise ValueError(f"unparseable router output type: {type(raw)!r}")


def fail_safe_decision(message: str, error: str) -> RouterDecision:
    """Unparseable router output → low-confidence clarify (never write).

    Prefer deterministic heuristics before this when possible (see classify).
    """
    return RouterDecision(
        intent="unknown",
        confidence=0.0,
        severity="info",
        route="clarify",
        missing_params=[],
        extracted_params={"router_error": str(error)[:200]},
        rationale=f"router_parse_failed: {error}",
    )


def classify(
    message: str,
    *,
    model_name: str | None = None,
    llm_call: Callable[[str], Any] | None = None,
    use_heuristics: bool = True,
) -> RouterDecision:
    """Classify NL message into RouterDecision.

    llm_call: optional injectable (prompt) -> raw for unit tests.
    Live path uses crewAI Agent + response_format when available, else LLM JSON.
    High-confidence read heuristics run first (and again on LLM failure).
    """
    if use_heuristics:
        hinted = try_deterministic_decision(message)
        if hinted is not None:
            logger.info("router heuristic hit intent=%s", hinted.intent)
            return hinted

    prompt = (
        f"{ROUTER_SYSTEM}\n\n"
        f"Operator message: {message!r}\n"
        "Respond with JSON fields: intent, confidence, severity, route, "
        "missing_params, extracted_params, rationale."
    )

    def _after_llm_failure(err: str) -> RouterDecision:
        if use_heuristics:
            hinted = try_deterministic_decision(message)
            if hinted is not None:
                logger.warning(
                    "router LLM failed (%s); using heuristic intent=%s",
                    err,
                    hinted.intent,
                )
                return hinted
        return fail_safe_decision(message, err)

    if llm_call is not None:
        try:
            return parse_router_decision(llm_call(prompt))
        except Exception as e:
            logger.warning("router llm_call parse failed: %s", e)
            # one repair retry
            try:
                repaired = llm_call(
                    prompt
                    + "\nPREVIOUS OUTPUT INVALID. Return ONLY valid JSON for RouterDecision."
                )
                return parse_router_decision(repaired)
            except Exception as e2:
                logger.warning("router repair failed: %s", e2)
                return _after_llm_failure(str(e2))

    # 1) Direct LiteLLM HTTP (preferred — no crewAI Agent parse quirks)
    try:
        raw = chat_json(
            system=ROUTER_SYSTEM + "\nRespond with a single JSON object only.",
            user=router_user_payload(message),
            model=model_name or "gemini-flash",
        )
        return parse_router_decision(raw)
    except Exception as e_http:
        logger.warning("router HTTP classify failed: %s", e_http)

    # 2) crewAI Agent fallback
    try:
        from crewai import Agent

        agent = Agent(
            role="Homelab Request Router",
            goal=(
                "Classify ops requests; never invent VM IDs; "
                "list/inventory reads go to worker with empty missing_params"
            ),
            backstory="Cost-aware router for a Proxmox homelab. Routes worker vs crisis.",
            llm=make_llm(model_name or "gemini-flash"),
            allow_delegation=False,
            verbose=False,
        )
        try:
            result = agent.kickoff(prompt, response_format=RouterDecision)
            return parse_router_decision(result)
        except TypeError:
            # older/newer crewAI without response_format kw
            result = agent.kickoff(prompt)
            return parse_router_decision(result)
        except Exception as e:
            logger.warning("router kickoff failed, retry once: %s", e)
            try:
                result = agent.kickoff(
                    prompt + "\nReturn ONLY valid JSON for RouterDecision."
                )
                return parse_router_decision(result)
            except Exception as e2:
                return _after_llm_failure(f"http:{e_http}; crewai:{e2}")
    except ImportError as e:
        logger.error("crewai unavailable for router: %s", e)
        return _after_llm_failure(f"http:{e_http}; crewai_missing:{e}")
