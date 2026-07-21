"""Unit tests for D-09 escalation rules and ES+EN crisis keywords."""
from __future__ import annotations

from pipeline.crisis_keywords import (
    CRISIS_KEYWORDS,
    build_escalate_notify_text,
    escalate_reasons,
    keyword_hit,
    needs_clarification,
    should_escalate,
)
from pipeline.models import RouterDecision


def _decision(**kwargs) -> RouterDecision:
    base = {"intent": "ops", "confidence": 0.9, "severity": "info", "route": "worker"}
    base.update(kwargs)
    return RouterDecision(**base)


def test_should_escalate_confidence_below_threshold():
    d = _decision(confidence=0.59)
    assert should_escalate(d, "lista las vms") is True
    assert "confidence:0.59" in escalate_reasons(d, "lista las vms")


def test_should_not_escalate_confidence_at_threshold():
    d = _decision(confidence=0.6)
    assert should_escalate(d, "lista las vms") is False


def test_should_escalate_severity_critical():
    d = _decision(confidence=0.95, severity="critical")
    assert should_escalate(d, "algo") is True


def test_should_escalate_keyword_urgente():
    d = _decision(confidence=0.95)
    assert should_escalate(d, "esto es URGENTE por favor") is True
    assert keyword_hit("esto es URGENTE por favor") == "urgente"


def test_should_escalate_keyword_crisis():
    d = _decision(confidence=0.95)
    assert should_escalate(d, "tenemos una crisis en prod") is True


def test_should_escalate_keyword_cluster_down():
    d = _decision(confidence=0.95)
    assert should_escalate(d, "the cluster down please help") is True


def test_should_escalate_keyword_emergencia():
    d = _decision(confidence=0.99, route="worker")
    assert should_escalate(d, "emergencia total") is True


def test_should_escalate_model_route_crisis():
    d = _decision(confidence=0.99, route="crisis")
    assert should_escalate(d, "lista vms") is True


def test_crisis_keywords_include_es_and_en():
    folded = {k.casefold() for k in CRISIS_KEYWORDS}
    assert "urgente" in folded
    assert "crisis" in folded
    assert "emergency" in folded
    assert "emergencia" in folded
    assert "cluster down" in folded


def test_build_escalate_notify_text():
    text = build_escalate_notify_text(["confidence:0.40", "keyword:urgente"])
    assert "DeepSeek" in text
    assert "confidence:0.40" in text
    assert "keyword:urgente" in text


def test_needs_clarification_from_missing_params():
    d = _decision(missing_params=["vmid"])
    assert needs_clarification(d) is True


def test_needs_clarification_from_route():
    d = _decision(route="clarify")
    assert needs_clarification(d) is True
