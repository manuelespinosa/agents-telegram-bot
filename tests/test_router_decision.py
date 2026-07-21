"""Unit tests for RouterDecision validation and pipeline models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pipeline.models import PipelineResult, RouterDecision


def test_router_decision_accepts_valid_confidence_bounds():
    low = RouterDecision(intent="list_vms", confidence=0.0)
    high = RouterDecision(intent="list_vms", confidence=1.0)
    assert low.confidence == 0.0
    assert high.confidence == 1.0


def test_router_decision_rejects_confidence_below_zero():
    with pytest.raises(ValidationError):
        RouterDecision(intent="x", confidence=-0.01)


def test_router_decision_rejects_confidence_above_one():
    with pytest.raises(ValidationError):
        RouterDecision(intent="x", confidence=1.01)


def test_router_decision_defaults():
    d = RouterDecision(intent="diagnose", confidence=0.8)
    assert d.severity == "info"
    assert d.route == "worker"
    assert d.missing_params == []
    assert d.extracted_params == {}
    assert d.rationale == ""


def test_router_decision_severity_and_route_literals():
    d = RouterDecision(
        intent="outage",
        confidence=0.5,
        severity="critical",
        route="crisis",
        missing_params=["vmid"],
        extracted_params={"hint": "cluster"},
        rationale="audit only",
    )
    assert d.severity == "critical"
    assert d.route == "crisis"
    assert d.missing_params == ["vmid"]


def test_pipeline_result_defaults():
    r = PipelineResult(reply_text="hola")
    assert r.escalate_notify_text is None
    assert r.pending_hitl == []
    assert r.crisis is False
    assert r.deepseek_consulted is False
    assert r.route == "worker"
    assert r.decision is None
