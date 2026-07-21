"""Crisis path must never raise into the NL handler."""
from __future__ import annotations

from pipeline.crisis import run_crisis
from pipeline.flow import SysAdminFlow
from pipeline.models import RouterDecision


def test_run_crisis_survives_tool_build_failure(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("tools exploded")

    monkeypatch.setattr("pipeline.crisis.build_all_tools", boom)

    d = RouterDecision(intent="unknown", confidence=0.0, route="crisis")
    notify, analysis = run_crisis(
        "urgente cluster caido",
        d,
        gate=object(),
        actor="test",
    )
    assert "Escalando" in notify or "Crisis" in notify
    assert analysis  # HTTP may fail in unit env; still non-empty guidance or error text


def test_flow_crisis_keyword_never_raises():
    def bad_crisis(*_a, **_k):
        raise RuntimeError("should be caught by flow")

    flow = SysAdminFlow(
        gate=object(),
        actor="t",
        chat_id=1,
        classify_fn=lambda m: RouterDecision(
            intent="unknown", confidence=0.0, route="clarify"
        ),
        crisis_fn=bad_crisis,
    )
    # keyword forces crisis route
    result = flow.run("URGENTE caida del cluster")
    assert result.route == "crisis"
    assert result.escalate_notify_text
    assert result.reply_text
