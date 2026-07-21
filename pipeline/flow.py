"""SysAdminFlow: classify → pick_route → clarify | worker | crisis (D-09/D-03)."""
from __future__ import annotations

import logging
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from pipeline.crisis import run_crisis
from pipeline.crisis_keywords import (
    keyword_hit,
    needs_clarification,
    should_escalate,
)
from pipeline.models import PipelineResult, RouterDecision
from pipeline.router import classify as default_classify
from pipeline.worker import run_worker

logger = logging.getLogger(__name__)

RouteName = Literal["clarify", "worker", "crisis"]


class PipelineState(BaseModel):
    """Mutable flow state (crewAI Flow state or pure-Python runner)."""

    model_config = {"arbitrary_types_allowed": True}

    message: str = ""
    actor: str = "pipeline"
    chat_id: int = 0
    decision: RouterDecision | None = None
    escalate_reason: str = ""
    escalate_notify_text: str | None = None
    result_text: str = ""
    pending_hitl: list[Any] = Field(default_factory=list)
    crisis: bool = False
    deepseek_consulted: bool = False
    route: str = "worker"
    budget_warning: str | None = None
    # injected deps (not serialized to LLM)
    gate: Any = None
    clarification_store: Any = None
    user_id: int = 0


def pick_route(decision: RouterDecision, message: str) -> RouteName:
    """Code rules win over model route.

    Order (D-09): crisis **keywords** first (even if router failed → clarify),
    then missing-params clarify, then confidence/severity escalate, else worker.
    """
    if keyword_hit(message):
        return "crisis"
    if needs_clarification(decision):
        return "clarify"
    if should_escalate(decision, message):
        return "crisis"
    return "worker"


def clarification_question(decision: RouterDecision) -> str:
    rationale = decision.rationale or ""
    if rationale.startswith("router_parse_failed") or decision.intent == "unknown":
        return (
            "No pude clasificar el mensaje con el router LLM "
            "(revisa aliases LiteLLM: gemini-flash / qwen-coder). "
            "Prueba un comando slash (/list_vms, /vm 300) o reformula; "
            "para escrituras incluye el VMID. "
            "Responde 'cancelar' si no aplica."
        )
    if decision.missing_params:
        missing = ", ".join(decision.missing_params)
        return (
            f"Necesito un dato más antes de continuar: {missing}. "
            "Responde con el valor (o 'cancelar' para abortar)."
        )
    return (
        "Necesito un dato más antes de continuar. "
        "Responde con el valor (o 'cancelar' para abortar)."
    )


class SysAdminFlow:
    """Pipeline runner with the same classify/pick_route/branch semantics as a crewAI Flow.

    Uses injectable classify/worker/crisis callables for unit tests (no live LiteLLM).
    When crewAI Flow is available, structure mirrors @start/@router/@listen.
    """

    def __init__(
        self,
        *,
        gate: Any,
        actor: str = "pipeline:worker",
        chat_id: int = 0,
        user_id: int = 0,
        clarification_store: Any = None,
        classify_fn: Callable[[str], RouterDecision] | None = None,
        worker_fn: Callable[..., str] | None = None,
        crisis_fn: Callable[..., tuple[str, str]] | None = None,
        budget_warning: str | None = None,
    ):
        self.gate = gate
        self.actor = actor
        self.chat_id = chat_id
        self.user_id = user_id
        self.clarification_store = clarification_store
        self.classify_fn = classify_fn or default_classify
        self.worker_fn = worker_fn
        self.crisis_fn = crisis_fn
        self.budget_warning = budget_warning
        self.state = PipelineState(
            actor=actor,
            chat_id=chat_id,
            user_id=user_id,
            gate=gate,
            clarification_store=clarification_store,
            budget_warning=budget_warning,
        )

    def run(self, message: str) -> PipelineResult:
        self.state.message = message
        decision = self.classify_fn(message)
        self.state.decision = decision
        route = pick_route(decision, message)
        self.state.route = route

        if route == "clarify":
            return self._run_clarify(decision, message)
        if route == "crisis":
            return self._run_crisis(decision, message)
        return self._run_worker(decision, message)

    def _run_clarify(self, decision: RouterDecision, message: str) -> PipelineResult:
        question = clarification_question(decision)
        self.state.result_text = question
        if self.clarification_store is not None and self.chat_id:
            try:
                self.clarification_store.set(
                    chat_id=self.chat_id,
                    user_id=self.user_id or 0,
                    original_text=message,
                    question=question,
                    partial_decision_json=decision.model_dump_json(),
                )
            except Exception:
                logger.exception("clarification_store.set failed")
        reply = question
        if self.budget_warning:
            reply = f"{self.budget_warning}\n{reply}"
        return PipelineResult(
            reply_text=reply,
            escalate_notify_text=None,
            pending_hitl=[],
            crisis=False,
            deepseek_consulted=False,
            route="clarify",
            decision=decision,
            budget_warning=self.budget_warning,
        )

    def _run_worker(self, decision: RouterDecision, message: str) -> PipelineResult:
        pending: list[Any] = []
        if self.worker_fn is not None:
            text = self.worker_fn(
                message,
                decision,
                gate=self.gate,
                actor=self.actor,
                pending_collector=pending,
            )
        else:
            text = run_worker(
                message,
                decision,
                gate=self.gate,
                actor=self.actor,
                pending_collector=pending,
            )
        self.state.result_text = text
        self.state.pending_hitl = pending
        reply = text
        if self.budget_warning:
            reply = f"{self.budget_warning}\n{reply}"
        return PipelineResult(
            reply_text=reply,
            escalate_notify_text=None,
            pending_hitl=list(pending),
            crisis=False,
            deepseek_consulted=False,
            route="worker",
            decision=decision,
            budget_warning=self.budget_warning,
        )

    def _run_crisis(self, decision: RouterDecision, message: str) -> PipelineResult:
        pending: list[Any] = []
        if self.crisis_fn is not None:
            notify, analysis = self.crisis_fn(
                message,
                decision,
                gate=self.gate,
                actor=self.actor,
                pending_collector=pending,
            )
        else:
            notify, analysis = run_crisis(
                message,
                decision,
                gate=self.gate,
                actor=self.actor,
                pending_collector=pending,
            )
        self.state.escalate_notify_text = notify
        self.state.result_text = analysis
        self.state.pending_hitl = pending
        self.state.crisis = True
        self.state.deepseek_consulted = True
        reply = analysis
        if self.budget_warning:
            reply = f"{self.budget_warning}\n{reply}"
        return PipelineResult(
            reply_text=reply,
            escalate_notify_text=notify,
            pending_hitl=list(pending),
            crisis=True,
            deepseek_consulted=True,
            route="crisis",
            decision=decision,
            budget_warning=self.budget_warning,
        )
