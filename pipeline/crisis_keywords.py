"""Crisis escalation keywords (ES+EN) and deterministic escalate rules (D-09)."""
from __future__ import annotations

from pipeline.models import RouterDecision

# ES + EN crisis triggers — substring match on casefolded user text
CRISIS_KEYWORDS: list[str] = [
    "crisis",
    "urgente",
    "emergency",
    "emergencia",
    "critical",
    "outage",
    "caída",
    "caida",
    "caído",
    "caido",
    "cluster down",
    "fallo grave",
    "down hard",
    "p0",
]


def keyword_hit(text: str) -> str | None:
    """Return the first matching crisis keyword, or None."""
    t = (text or "").casefold()
    for k in CRISIS_KEYWORDS:
        if k.casefold() in t:
            return k
    return None


def escalate_reasons(decision: RouterDecision, text: str) -> list[str]:
    """List of human-readable escalate reasons (may be empty)."""
    reasons: list[str] = []
    hit = keyword_hit(text)
    if hit:
        reasons.append(f"keyword:{hit}")
    if decision.confidence < 0.6:
        reasons.append(f"confidence:{decision.confidence:.2f}")
    if decision.severity == "critical":
        reasons.append("severity:critical")
    if decision.route == "crisis":
        reasons.append("route:crisis")
    return reasons


def should_escalate(decision: RouterDecision, text: str) -> bool:
    """True if conf < 0.6 OR severity critical OR keyword OR model route=crisis."""
    return bool(escalate_reasons(decision, text))


def needs_clarification(decision: RouterDecision) -> bool:
    """True when router reports missing write/read targets (D-03)."""
    return bool(decision.missing_params) or decision.route == "clarify"


def build_escalate_notify_text(reasons: list[str]) -> str:
    """D-12 short plain-text notify before DeepSeek runs."""
    motive = "|".join(reasons) if reasons else "unknown"
    return f"⚠️ Escalando a Crisis (DeepSeek): motivo={motive}"
