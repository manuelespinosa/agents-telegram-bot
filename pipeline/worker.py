"""Worker agent: Qwen tools via ActionGate (max tool iterations capped)."""
from __future__ import annotations

import logging
from typing import Any, Callable

from pipeline.llms import make_llm
from pipeline.models import RouterDecision
from pipeline.tools import build_worker_tools

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITER = 4


def run_worker(
    message: str,
    decision: RouterDecision,
    *,
    gate: Any,
    actor: str,
    pending_collector: list[Any] | None = None,
    model_name: str | None = None,
    max_iter: int = DEFAULT_MAX_ITER,
    worker_call: Callable[..., str] | None = None,
) -> str:
    """Run Worker agent with catalog GateTools. Returns plain-text summary.

    worker_call: injectable for tests — (message, decision, tools) -> text.
    """
    tools = build_worker_tools(
        gate, actor=actor, pending_collector=pending_collector
    )

    if worker_call is not None:
        return worker_call(message, decision, tools)

    try:
        from crewai import Agent

        agent = Agent(
            role="Homelab DevOps Worker",
            goal="Execute routine Proxmox and uptime operations via catalog tools only",
            backstory=(
                "You operate a Proxmox homelab. Use tools for reads and write proposals. "
                "Never invent targets. Writes require human approval after propose."
            ),
            llm=make_llm(model_name or "qwen-coder"),
            tools=tools,
            allow_delegation=False,
            verbose=False,
            max_iter=max_iter,
        )
        params_hint = json_safe(decision.extracted_params)
        prompt = (
            f"Operator request: {message}\n"
            f"Intent: {decision.intent}\n"
            f"Extracted params: {params_hint}\n"
            "Use tools as needed. Reply in plain text summarizing outcomes."
        )
        result = agent.kickoff(prompt)
        return _result_text(result)
    except ImportError as e:
        logger.error("crewai unavailable for worker: %s", e)
        return "Worker unavailable (crewai not installed)."
    except Exception as e:
        logger.exception("worker failed")
        return f"Worker error: {type(e).__name__}: {e}"


def json_safe(obj: Any) -> str:
    try:
        import json

        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def _result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    raw = getattr(result, "raw", None)
    if raw is not None:
        return str(raw)
    return str(result)
