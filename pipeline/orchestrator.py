"""run_pipeline entry for Telegram NL (no PTB imports)."""
from __future__ import annotations

import logging
from typing import Any, Callable

from pipeline.clarification_store import ClarificationStore
from pipeline.cost_logger import cost_db_scope
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
    - Binds cost_db_scope so nested LLM calls (router_http / crewAI) record real USD
    - After run, trips BudgetGate when rolling 24h cost exceeds max
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

    # Nested router/worker/crisis LLM calls record via cost_logger.record_usage
    # when cost_db_path is set (no more hardcoded cost=0.0 placeholder rows).
    with cost_db_scope(cost_db_path):
        result = flow.run(message)

    # Kill-switch after live spend is on the ledger
    if budget is not None:
        try:
            alert = budget.check_and_trip()
            if alert:
                # Surface kill-switch on the same reply path
                prefix = f"{alert}\n"
                if result.budget_warning:
                    result = result.model_copy(
                        update={
                            "budget_warning": f"{result.budget_warning}\n{alert}",
                            "reply_text": f"{prefix}{result.reply_text}",
                        }
                    )
                else:
                    result = result.model_copy(
                        update={
                            "budget_warning": alert,
                            "reply_text": f"{prefix}{result.reply_text}",
                        }
                    )
            elif not result.budget_warning:
                warn = budget.soft_warn_if_needed()
                if warn:
                    result = result.model_copy(
                        update={
                            "budget_warning": warn,
                            "reply_text": f"{warn}\n{result.reply_text}",
                        }
                    )
        except Exception:
            logger.exception("budget check_and_trip failed")

    # router_model kept for API compat (call sites / tests may pass it)
    _ = router_model
    return result
