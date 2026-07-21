"""Crisis agent: DeepSeek-R1 on demand; analyze + propose via ActionGate (D-10/D-12)."""
from __future__ import annotations

import logging
from typing import Any, Callable

from pipeline.cost_logger import record_usage, usage_from_crewai_result
from pipeline.crisis_keywords import build_escalate_notify_text, escalate_reasons
from pipeline.llms import make_llm
from pipeline.models import RouterDecision
from pipeline.tools import build_all_tools
from pipeline.worker import _result_text, json_safe

logger = logging.getLogger(__name__)


def build_notify_text(decision: RouterDecision, message: str) -> str:
    """D-12 notify-then-run text (before DeepSeek work)."""
    try:
        reasons = escalate_reasons(decision, message)
    except Exception:
        logger.exception("escalate_reasons failed")
        reasons = ["unknown"]
    return build_escalate_notify_text(reasons)


def _http_crisis_analysis(message: str, decision: RouterDecision, model_name: str) -> str:
    """Analyze via LiteLLM chat.completions without crewAI tools (resilient path)."""
    from pipeline.router_http import chat_json

    system = (
        "You are a Homelab Crisis Analyst for a Proxmox lab. "
        "Give concise plain-text diagnosis and recommended catalog actions only "
        "(list_vms, vm_status, vm_start/stop/reboot, snapshot_create/list, service_uptime). "
        "Never invent VMIDs. Writes always need human approval. No free-shell/SSH. "
        "Do not output JSON unless helpful as a short bullet list."
    )
    user = (
        f"Operator crisis message: {message!r}\n"
        f"Router intent={decision.intent} conf={decision.confidence} "
        f"severity={decision.severity} missing={decision.missing_params}\n"
        f"Params: {json_safe(decision.extracted_params)}\n"
        "Provide short findings + next safe steps."
    )
    return chat_json(
        system=system,
        user=user,
        model=model_name,
        temperature=0.2,
        timeout_sec=90.0,
    )


def run_crisis(
    message: str,
    decision: RouterDecision,
    *,
    gate: Any,
    actor: str,
    pending_collector: list[Any] | None = None,
    model_name: str | None = None,
    crisis_call: Callable[..., str] | None = None,
) -> tuple[str, str]:
    """Run crisis path.

    Returns (escalate_notify_text, analysis_reply_text).
    Notify text is always produced first (D-12) and never blocked by analysis failures.
    """
    notify = build_notify_text(decision, message)
    model = model_name or "deepseek-r1"

    if crisis_call is not None:
        try:
            analysis = crisis_call(message, decision, None)
            return notify, analysis
        except Exception as e:
            logger.exception("crisis_call failed")
            return notify, f"Crisis error: {type(e).__name__}: {e}"

    # Prefer crewAI+tools, then HTTP DeepSeek, then static guidance — never raise.
    try:
        tools = build_all_tools(
            gate, actor=actor, pending_collector=pending_collector
        )
        from crewai import Agent

        agent = Agent(
            role="Homelab Crisis Analyst",
            goal="Diagnose structural failures and propose catalog actions only",
            backstory=(
                "Deep analysis agent for outages. Use read tools freely; "
                "propose writes via tools (human approval required). No free-shell."
            ),
            llm=make_llm(model),
            tools=tools,
            allow_delegation=False,
            verbose=False,
            max_iter=4,
        )
        prompt = (
            f"CRISIS analysis request.\n"
            f"Operator message: {message}\n"
            f"Router intent={decision.intent} conf={decision.confidence} "
            f"severity={decision.severity}\n"
            f"Params: {json_safe(decision.extracted_params)}\n"
            "Analyze with tools. Propose catalog writes only when needed. "
            "Plain text findings."
        )
        result = agent.kickoff(prompt)
        try:
            record_usage(usage_from_crewai_result(result, model=model))
        except Exception:
            logger.exception("crisis cost record failed model=%s", model)
        return notify, _result_text(result)
    except ImportError as e:
        logger.warning("crewai unavailable for crisis: %s — HTTP fallback", e)
    except Exception as e:
        logger.warning("crisis crewAI path failed: %s — HTTP fallback", e)

    try:
        analysis = _http_crisis_analysis(message, decision, model)
        if analysis and analysis.strip():
            return notify, analysis.strip()
    except Exception as e:
        logger.exception("crisis HTTP fallback failed")
        return (
            notify,
            "Crisis agent no pudo completar el análisis "
            f"({type(e).__name__}: {e}). "
            "Usa /list_vms, /health o /vm <id> mientras se revisan logs LiteLLM.",
        )

    return (
        notify,
        "Crisis notify enviado; análisis vacío del modelo. "
        "Prueba /list_vms o /health para telemetría inmediata.",
    )
