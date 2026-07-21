"""Multi-agent pipeline package (Phase 4): Router → Worker / Crisis via ActionGate.

Pure-ish modules — no python-telegram-bot imports. Wire NL handlers in 04-03.
"""
from __future__ import annotations

from pipeline.models import PipelineResult, RouterDecision

__all__ = [
    "PipelineResult",
    "RouterDecision",
]

# Lazy re-export so importing pipeline.models works before orchestrator exists
try:
    from pipeline.orchestrator import run_pipeline  # noqa: F401

    __all__.append("run_pipeline")
except ImportError:  # pragma: no cover
    pass
