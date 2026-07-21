"""GateTool: BaseTool wrappers that only call ActionGate.propose (T-04-05 / WR-03)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

try:
    from crewai.tools import BaseTool as _CrewAIBaseTool

    CREWAI_TOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover - Python 3.14 local without crewai
    CREWAI_TOOLS_AVAILABLE = False

    class _CrewAIBaseTool(BaseModel, ABC):  # type: ignore[no-redef]
        """Minimal BaseTool shim so unit tests run without crewai installed."""

        model_config = ConfigDict(arbitrary_types_allowed=True)

        name: str
        description: str
        args_schema: type[BaseModel] = Field(default=BaseModel)

        @abstractmethod
        def _run(self, **kwargs: Any) -> str:
            raise NotImplementedError

        def run(self, *args: Any, **kwargs: Any) -> str:
            return self._run(*args, **kwargs)


class GateTool(_CrewAIBaseTool, ABC):
    """BaseTool that injects ActionGate only (HITL path; no direct writes).

    Subclasses set action_id class attribute and implement _run via propose().
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Fixed per subclass (public for tests/builders)
    action_id: str = "unknown"

    # Injected runtime deps — private so they are not tool schema fields
    _gate: Any = PrivateAttr(default=None)
    _actor: str = PrivateAttr(default="pipeline:worker")
    _pending_collector: list[Any] | None = PrivateAttr(default=None)

    def __init__(
        self,
        gate: Any = None,
        actor: str = "pipeline:worker",
        pending_collector: list[Any] | None = None,
        **data: Any,
    ):
        super().__init__(**data)
        self._gate = gate
        self._actor = actor
        self._pending_collector = pending_collector

    @property
    def gate(self) -> Any:
        return self._gate

    @property
    def actor(self) -> str:
        return self._actor

    @property
    def pending_collector(self) -> list[Any] | None:
        return self._pending_collector

    def format_propose_result(self, result: Any) -> str:
        """Map ProposeResult → marker string for agent + optional collector."""
        needs = bool(getattr(result, "needs_approval", False))
        status = getattr(result, "status", "") or ""
        message = getattr(result, "message", "") or ""
        request_id = (
            getattr(result, "request_id", None)
            or getattr(result, "hitl_request_id", None)
            or ""
        )

        if needs or status == "pending":
            if self._pending_collector is not None:
                self._pending_collector.append(result)
            return f"HITL_PENDING:{request_id}:{message}"

        if status == "blocked":
            return f"BLOCKED:{message}"

        return (
            getattr(result, "execution_result", None)
            or getattr(result, "result", None)
            or message
            or ""
        )

    def propose(self, params: dict[str, Any], reason: str) -> str:
        """Sole side-effect path: ActionGate.propose (HITL for writes)."""
        if self._gate is None:
            return "BLOCKED:ActionGate not injected"
        result = self._gate.propose(
            self.action_id,
            params,
            reason=reason,
            actor=self._actor,
        )
        return self.format_propose_result(result)
