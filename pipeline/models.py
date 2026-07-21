"""Pipeline contracts: RouterDecision + PipelineResult (locked Phase 4 fields)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RouterDecision(BaseModel):
    """Structured router classification — machine-parseable routing input."""

    intent: str = Field(
        ...,
        description="Short verb phrase, e.g. list_vms, snapshot_create, uptime_check, diagnose",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    route: Literal["worker", "crisis", "clarify"] = "worker"
    missing_params: list[str] = Field(default_factory=list)
    extracted_params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(
        default="",
        description="One short sentence for audit/logs only — not dumped to Telegram by default",
    )


class PipelineResult(BaseModel):
    """Orchestrator output for Telegram handlers (04-03)."""

    model_config = {"arbitrary_types_allowed": True}

    reply_text: str = ""
    escalate_notify_text: str | None = None
    pending_hitl: list[Any] = Field(default_factory=list)
    crisis: bool = False
    deepseek_consulted: bool = False
    route: str = "worker"
    decision: RouterDecision | None = None
    budget_warning: str | None = None
