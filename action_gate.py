"""ActionGate: single orchestration entry for cataloged ops (HITL-04).

Pure Python safety core — no PTB/Telegram package imports.
Executor is injected for side effects (read/write).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from action_catalog import ActionDef, RiskTier, UnknownActionError, resolve
from budget_gate import BudgetGate
from hitl_store import HITLStore
from hmac_lock import canonical_payload, sign_payload, verify_payload


class ActionExecutor(Protocol):
    def execute_read(self, action_id: str, params: dict[str, Any]) -> str: ...
    def execute_write(self, action_id: str, params: dict[str, Any]) -> str: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    # Accept ...Z or space-separated SQLite-ish forms
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class ProposeResult:
    status: str  # completed | pending | blocked
    needs_approval: bool
    message: str
    hitl_request_id: str | None = None
    request_id: str | None = None  # alias convenience
    action_id: str | None = None
    tier: str | None = None
    target: str | None = None
    expected_impact: str | None = None
    reason: str | None = None
    expires_at: str | None = None
    requires_deepseek: bool = False
    crisis: bool = False
    result: str | None = None
    execution_result: str | None = None

    def __post_init__(self) -> None:
        if self.hitl_request_id and not self.request_id:
            self.request_id = self.hitl_request_id


@dataclass
class DecisionResult:
    status: str  # executed | rejected | expired | denied | blocked | failed | noop | already_processed
    message: str
    request_id: str | None = None
    execution_result: str | None = None


class ActionGate:
    """Catalog → budget → HITL → HMAC-gated execute. Single entry for ops."""

    def __init__(
        self,
        store: HITLStore,
        budget: BudgetGate,
        hmac_secret: bytes,
        executor: ActionExecutor,
        timeout_sec: int = 300,
        now_fn=None,
    ):
        self.store = store
        self.budget = budget
        self.hmac_secret = hmac_secret
        self.executor = executor
        self.timeout_sec = int(timeout_sec)
        self.now_fn = now_fn or _utc_now

    def _now(self) -> datetime:
        now = self.now_fn()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now

    def propose(
        self,
        action_id: str,
        params: dict[str, Any] | None,
        reason: str,
        actor: str,
    ) -> ProposeResult:
        raw_params = dict(params or {})

        # 1) Resolve catalog first for tier-aware budget (reads skip mutation gate)
        try:
            defn = resolve(action_id, raw_params)
        except UnknownActionError as e:
            self.store.audit(
                "unknown_action",
                {"action_id": action_id, "actor": actor, "reason": reason},
            )
            self.store.audit(
                "block",
                {"action_id": action_id, "cause": "unknown_action", "actor": actor},
            )
            return ProposeResult(
                status="blocked",
                needs_approval=False,
                message=f"Blocked unknown action: {e.action_id}",
                action_id=action_id,
            )
        except Exception as e:
            # Invalid params (e.g. pydantic ValidationError) — hard block
            self.store.audit(
                "block",
                {
                    "action_id": action_id,
                    "cause": "invalid_params",
                    "error": type(e).__name__,
                    "actor": actor,
                },
            )
            return ProposeResult(
                status="blocked",
                needs_approval=False,
                message=f"Blocked invalid params for {action_id}: {e}",
                action_id=action_id,
            )

        validated = defn.params_model.model_validate(raw_params)
        clean_params = validated.model_dump(exclude_none=True)

        is_mutation = defn.tier in (RiskTier.WRITE, RiskTier.CRISIS)

        # 2) Budget gate for write|crisis only (D-14)
        if is_mutation:
            alert = self.budget.check_and_trip()
            if alert or not self.budget.mutations_allowed():
                self.store.audit(
                    "block",
                    {
                        "action_id": action_id,
                        "cause": "budget",
                        "actor": actor,
                        "alert": alert,
                    },
                )
                return ProposeResult(
                    status="blocked",
                    needs_approval=False,
                    message=alert
                    or "Mutaciones pausadas por budget kill-switch. Usa /resume-budget.",
                    action_id=action_id,
                    tier=defn.tier.value,
                )

        # 3) READ → execute immediately, no HITL (HITL-04)
        if defn.tier == RiskTier.READ:
            result = self.executor.execute_read(defn.id.value, clean_params)
            self.store.audit(
                "read_executed",
                {"action_id": defn.id.value, "actor": actor},
            )
            return ProposeResult(
                status="completed",
                needs_approval=False,
                message=f"Read completed: {defn.id.value}",
                action_id=defn.id.value,
                tier=defn.tier.value,
                target=self._target(clean_params),
                expected_impact=defn.expected_impact,
                reason=reason,
                result=result,
                execution_result=result,
            )

        # 4) WRITE|CRISIS → sign + pending HITL (never execute here; D-09)
        return self._create_pending(defn, clean_params, reason, actor)

    def _create_pending(
        self,
        defn: ActionDef,
        clean_params: dict[str, Any],
        reason: str,
        actor: str,
    ) -> ProposeResult:
        request_id = uuid.uuid4().hex
        expires_at = _iso(self._now() + timedelta(seconds=self.timeout_sec))
        payload = canonical_payload(
            action_id=defn.id.value,
            params=clean_params,
            request_id=request_id,
            expires_at=expires_at,
        )
        signature = sign_payload(self.hmac_secret, payload)
        requires_deepseek = bool(
            defn.requires_deepseek or defn.tier == RiskTier.CRISIS
        )
        req = self.store.create_request(
            request_id=request_id,
            action_id=defn.id.value,
            params=clean_params,
            risk_tier=defn.tier.value,
            requires_deepseek=requires_deepseek,
            reason=reason,
            payload_canonical=payload.decode("utf-8"),
            payload_hmac=signature,
            expires_at=expires_at,
        )
        crisis = defn.tier == RiskTier.CRISIS or requires_deepseek
        crisis_line = (
            "\n🚨 CRISIS — DeepSeek consultado. Una sola aprobación (Approve/Deny).\n"
            if crisis
            else "\n"
        )
        message = (
            f"🔐 Aprobación requerida\n\n"
            f"Acción: {defn.id.value}\n"
            f"Target: {self._target(clean_params)}\n"
            f"Tier: {defn.tier.value}\n"
            f"Impacto: {defn.expected_impact}\n"
            f"Motivo: {reason}\n"
            f"Caduca: {expires_at} (5 min)\n"
            f"{crisis_line}"
            f"Timeout → se cancela (nunca auto-ejecuta)."
        )
        self.store.audit(
            "hitl_proposed",
            {
                "request_id": request_id,
                "action_id": defn.id.value,
                "actor": actor,
                "crisis": crisis,
            },
        )
        return ProposeResult(
            status="pending",
            needs_approval=True,
            message=message,
            hitl_request_id=req.request_id,
            request_id=req.request_id,
            action_id=defn.id.value,
            tier=defn.tier.value,
            target=self._target(clean_params),
            expected_impact=defn.expected_impact,
            reason=reason,
            expires_at=expires_at,
            requires_deepseek=requires_deepseek,
            crisis=crisis,
        )

    @staticmethod
    def _target(params: dict[str, Any]) -> str:
        if "vmid" in params:
            node = params.get("node")
            if node:
                return f"VM {params['vmid']} @ {node}"
            return f"VM {params['vmid']}"
        return "cluster"

    def approve(self, request_id: str, user_id: int | str) -> DecisionResult:
        req = self.store.get(request_id)
        if req is None:
            return DecisionResult(
                status="denied",
                message=f"Request {request_id} not found",
                request_id=request_id,
            )

        if req.status == "executed":
            return DecisionResult(
                status="already_processed",
                message="Already executed (anti-replay)",
                request_id=request_id,
            )
        if req.status in {"rejected", "expired", "failed"}:
            return DecisionResult(
                status="noop" if req.status != "expired" else "expired",
                message=f"Request already terminal: {req.status}",
                request_id=request_id,
            )
        if req.status == "approved":
            # Approved but not executed — treat second approve as noop
            return DecisionResult(
                status="noop",
                message="Already approved",
                request_id=request_id,
            )

        if req.status != "pending":
            return DecisionResult(
                status="denied",
                message=f"Invalid status for approve: {req.status}",
                request_id=request_id,
            )

        # Expire if past expires_at (D-07)
        if self._now() > _parse_iso(req.expires_at):
            self.store.expire_if_pending(request_id)
            return DecisionResult(
                status="expired",
                message="Request expired; never auto-executed",
                request_id=request_id,
            )

        # Re-verify HMAC over stored canonical fields only
        stored_params = req.params()
        payload = canonical_payload(
            action_id=req.action_id,
            params=stored_params,
            request_id=req.request_id,
            expires_at=req.expires_at,
        )
        # Prefer stored canonical bytes if present; recompute is source for verify
        if not verify_payload(self.hmac_secret, payload, req.payload_hmac):
            self.store.audit(
                "hmac_fail",
                {"request_id": request_id, "user_id": str(user_id)},
            )
            self.store.mark_failed(request_id, result="hmac_verify_failed")
            return DecisionResult(
                status="blocked",
                message="HMAC verification failed; execution aborted",
                request_id=request_id,
            )

        # Budget re-check before mutation
        alert = self.budget.check_and_trip()
        if alert or not self.budget.mutations_allowed():
            self.store.audit(
                "block",
                {
                    "request_id": request_id,
                    "cause": "budget_on_approve",
                    "user_id": str(user_id),
                },
            )
            return DecisionResult(
                status="blocked",
                message=alert
                or "Mutaciones pausadas por budget; no se ejecuta.",
                request_id=request_id,
            )

        if not self.store.mark_approved(request_id, decided_by=str(user_id)):
            return DecisionResult(
                status="noop",
                message="Could not mark approved (race / already processed)",
                request_id=request_id,
            )

        try:
            exec_result = self.executor.execute_write(req.action_id, stored_params)
        except Exception as e:
            self.store.mark_failed(request_id, result=str(e))
            self.store.audit(
                "execute_failed",
                {"request_id": request_id, "error": type(e).__name__},
            )
            return DecisionResult(
                status="failed",
                message=f"Execution failed: {e}",
                request_id=request_id,
                execution_result=str(e),
            )

        if not self.store.mark_executed(request_id, result=str(exec_result)):
            return DecisionResult(
                status="failed",
                message="Executed but failed to mark executed",
                request_id=request_id,
                execution_result=str(exec_result),
            )

        self.store.audit(
            "executed",
            {
                "request_id": request_id,
                "action_id": req.action_id,
                "user_id": str(user_id),
            },
        )
        return DecisionResult(
            status="executed",
            message=f"Executed {req.action_id}",
            request_id=request_id,
            execution_result=str(exec_result),
        )

    def reject(self, request_id: str, user_id: int | str) -> DecisionResult:
        req = self.store.get(request_id)
        if req is None:
            return DecisionResult(
                status="denied",
                message=f"Request {request_id} not found",
                request_id=request_id,
            )
        if req.status != "pending":
            return DecisionResult(
                status="noop",
                message=f"Request not pending ({req.status})",
                request_id=request_id,
            )
        if self.store.mark_rejected(request_id, decided_by=str(user_id)):
            return DecisionResult(
                status="rejected",
                message="Rejected; no execution",
                request_id=request_id,
            )
        return DecisionResult(
            status="noop",
            message="Reject failed (race)",
            request_id=request_id,
        )

    def expire(self, request_id: str) -> bool:
        """Expire pending only — never executes (D-07)."""
        return self.store.expire_if_pending(request_id)
