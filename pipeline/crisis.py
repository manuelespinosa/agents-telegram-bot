"""Crisis agent: DeepSeek-R1 on demand; analyze + propose via ActionGate (D-10/D-12)."""
from __future__ import annotations

import logging
from typing import Any, Callable

from pipeline.crisis_keywords import build_escalate_notify_text, escalate_reasons
from pipeline.llms import make_llm
from pipeline.models import RouterDecision
from pipeline.tools import build_all_tools
from pipeline.worker import _result_text, json_safe

logger = logging.getLogger(__name__)


def build_notify_text(decision: RouterDecision, message: str) -> str:
    """D-12 notify-then-run text (before DeepSeek work)."""
    reasons = escalate_reasons(decision, message)
    return build_escalate_notify_text(reasons)


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
    Notify text is always produced first (D-12).
    """
    notify = build_notify_text(decision, message)
    tools = build_all_tools(
        gate, actor=actor, pending_collector=pending_collector
    )

    if crisis_call is not None:
        analysis = crisis_call(message, decision, tools)
        return notify, analysis

    try:
        from crewai import Agent

        agent = Agent(
            role="Homelab Crisis Analyst",
            goal="Diagnose structural failures and propose catalog actions only",
            backstory=(
                "Deep analysis agent for outages. Use read tools freely; "
                "propose writes via tools (human approval required). No free-shell."
            ),
            llm=make_llm(model_name or "deepseek-r1"),
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
        return notify, _result_text(result)
    except ImportError as e:
        logger.error("crewai unavailable for crisis: %s", e)
        return notify, "Crisis agent unavailable (crewai not installed)."
    except Exception as e:
        logger.exception("crisis agent failed")
        return notify, f"Crisis error: {type(e).__name__}: {e}"
