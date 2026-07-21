"""run_pipeline entry for Telegram NL (no PTB imports)."""
from __future__ import annotations

import logging
from typing import Any, Callable

from pipeline.clarification_store import ClarificationStore
from pipeline.cost_logger import log_usage
from pipeline.flow import SysAdminFlow
from pipeline.models import PipelineResult, RouterDecision
from pipeline.router import classify as default_classify

logger = logging.getLogger(__name__)

_CANCEL_WORDS = frozenset(
    {
        "cancelar",
        "cancel",
        "abortar",
        "abort",
        "stop",
        "nunca mind",
        "olvidalo",
        "olvídalo",
    }
)


def _is_cancel(text: str) -> bool:
    t = (text or "").strip().casefold()
    if t in _CANCEL_WORDS:
        return True
    # single-token cancel phrases
    return t in {"no gracias", "dejalo", "déjalo"}


def _merge_clarification(
    pending_original: str,
    answer: str,
    partial: RouterDecision | None,
) -> str:
    """Combine original request + user answer for re-classification."""
    if partial and partial.missing_params:
        keys = ", ".join(partial.missing_params)
        return (
            f"{pending_original}\n"
            f"(clarification for {keys}: {answer})"
        )
    return f"{pending_original}\n(clarification: {answer})"


def run_pipeline(
    text: str,
    *,
    actor: str,
    chat_id: int,
    gate: Any,
    clarification_store: ClarificationStore | None = None,
    cost_db_path: str | None = None,
    budget: Any | None = None,
    user_id: int = 0,
    classify_fn: Callable[[str], RouterDecision] | None = None,
    worker_fn: Callable[..., str] | None = None,
    crisis_fn: Callable[..., tuple[str, str]] | None = None,
    router_model: str = "gemini-flash",
) -> PipelineResult:
    """Execute Router→Worker|Crisis|Clarify for one NL message.

    - Merges pending clarification answers (D-03/D-04)
    - Cancels on explicit cancelar/cancel
    - Soft budget warning when mutations_allowed is False
    - Logs a router usage row when cost_db_path set (tokens may be 0 if unknown)
    """
    message = (text or "").strip()
    if not message:
        return PipelineResult(
            reply_text="Mensaje vacío.",
            route="clarify",
        )

    # Soft budget advisory (reads still allowed; writes blocked by gate)
    budget_warning: str | None = None
    if budget is not None:
        try:
            if not budget.mutations_allowed():
                budget_warning = (
                    "⚠️ Budget: mutaciones pausadas; solo lecturas/propuestas bloqueadas en gate."
                )
        except Exception:
            logger.exception("budget check failed")

    # Clarification multi-turn
    if clarification_store is not None:
        pending = clarification_store.get(chat_id)
        if pending is not None:
            if _is_cancel(message):
                clarification_store.cancel(chat_id)
                return PipelineResult(
                    reply_text="Cancelado. Envía un nuevo request cuando quieras.",
                    route="clarify",
                )
            partial: RouterDecision | None = None
            if pending.partial_decision_json:
                try:
                    partial = RouterDecision.model_validate_json(
                        pending.partial_decision_json
                    )
                except Exception:
                    partial = None
            message = _merge_clarification(
                pending.original_text, message, partial
            )
            clarification_store.cancel(chat_id)

    flow = SysAdminFlow(
        gate=gate,
        actor=actor,
        chat_id=chat_id,
        user_id=user_id,
        clarification_store=clarification_store,
        classify_fn=classify_fn or default_classify,
        worker_fn=worker_fn,
        crisis_fn=crisis_fn,
        budget_warning=budget_warning,
    )
    result = flow.run(message)

    if cost_db_path:
        try:
            log_usage(
                cost_db_path,
                model=router_model,
                tokens_in=0,
                tokens_out=0,
                cost=0.0,
            )
        except Exception:
            logger.exception("cost log failed")

    return result
