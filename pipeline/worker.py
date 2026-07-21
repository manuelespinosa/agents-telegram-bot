"""Worker agent: Qwen tools via ActionGate (max tool iterations capped)."""
from __future__ import annotations

import logging
from typing import Any, Callable

from pipeline.cost_logger import record_usage, usage_from_crewai_result
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

    # Deterministic catalog shortcut (slash-equivalent — no Worker LLM)
    params = decision.extracted_params or {}
    if (
        bool(params.get("skip_llm"))
        and gate is not None
        and worker_call is None
        and decision.intent
    ):
        action_id = str(params.get("action_id") or decision.intent)
        try:
            propose_params: dict[str, Any] = {}
            if "vmid" in params and params["vmid"] is not None:
                propose_params["vmid"] = int(params["vmid"])
            if action_id == "snapshot_create":
                if params.get("snapname"):
                    propose_params["snapname"] = str(params["snapname"])
                else:
                    # catalog requires snapname; deterministic default for NL shortcut
                    from datetime import datetime, timezone

                    propose_params["snapname"] = (
                        f"nl_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                    )
            result = gate.propose(
                action_id,
                propose_params,
                reason=f"nl heuristic {action_id}: {message[:80]}",
                actor=actor,
            )
            needs = bool(getattr(result, "needs_approval", False))
            status = getattr(result, "status", "") or ""
            if (needs or status == "pending") and pending_collector is not None:
                pending_collector.append(result)
            body = (
                getattr(result, "execution_result", None)
                or getattr(result, "result", None)
                or getattr(result, "message", None)
                or ""
            )
            if needs or status == "pending":
                return str(body) or "Propuesta enviada (pendiente de aprobación HITL)."
            return str(body) if body else "Sin datos."
        except Exception as e:
            logger.exception("deterministic %s failed", action_id)
            return f"Error en {action_id}: {type(e).__name__}: {e}"

    if worker_call is not None:
        return worker_call(message, decision, tools)

    model = model_name or "qwen-coder"
    try:
        from crewai import Agent

        agent = Agent(
            role="Homelab DevOps Worker",
            goal="Execute routine Proxmox and uptime operations via catalog tools only",
            backstory=(
                "You operate a Proxmox homelab. Use tools for reads and write proposals. "
                "Never invent targets. Writes require human approval after propose."
            ),
            llm=make_llm(model),
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
        try:
            record_usage(usage_from_crewai_result(result, model=model))
        except Exception:
            logger.exception("worker cost record failed model=%s", model)
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
