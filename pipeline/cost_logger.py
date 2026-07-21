"""Insert LLM usage rows into cost_logs (BudgetGate ledger schema).

Also provides:
- USD estimation when LiteLLM omits response_cost
- parsing of OpenAI-compatible / crewAI usage payloads
- optional ContextVar scope so HTTP/crewAI call sites can record without
  threading cost_db_path through every function signature
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# Active ledger path for the current pipeline invocation (optional).
_active_cost_db: ContextVar[str | None] = ContextVar("active_cost_db", default=None)

# Approximate USD per 1M tokens (input, output). Used only when provider
# does not return a real cost. Conservative-ish OpenRouter-class rates.
_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-flash": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-flash": (0.15, 0.60),
    "qwen-coder": (0.20, 0.20),
    "qwen": (0.20, 0.20),
    "deepseek-r1": (0.55, 2.19),
    "deepseek-chat": (0.14, 0.28),
    "gpt-4o-mini": (0.15, 0.60),
}
_DEFAULT_USD_PER_1M: tuple[float, float] = (0.50, 1.50)


@dataclass(frozen=True)
class UsageMetrics:
    """Normalized usage for one LLM call."""

    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    cost_source: str = "unknown"  # provider | estimate | zero


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cost_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            cost REAL NOT NULL,
            model TEXT,
            tokens_in INTEGER,
            tokens_out INTEGER
        )
        """
    )
    conn.commit()


def _normalize_model(model: str | None) -> str:
    name = (model or "unknown").strip()
    # crewAI / LiteLLM may prefix provider: openai/gemini-flash
    if "/" in name:
        name = name.split("/")[-1]
    return name or "unknown"


def _rates_for(model: str) -> tuple[float, float]:
    key = _normalize_model(model).casefold()
    if key in _USD_PER_1M:
        return _USD_PER_1M[key]
    for alias, rates in _USD_PER_1M.items():
        if alias in key or key in alias:
            return rates
    return _DEFAULT_USD_PER_1M


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate USD from token counts when provider cost is missing."""
    tin = max(0, int(tokens_in or 0))
    tout = max(0, int(tokens_out or 0))
    if tin == 0 and tout == 0:
        return 0.0
    pin, pout = _rates_for(model)
    return (tin * pin + tout * pout) / 1_000_000.0


def usage_from_openai_response(
    data: dict[str, Any] | None,
    *,
    model: str,
) -> UsageMetrics:
    """Extract tokens + USD from a LiteLLM / OpenAI chat.completions payload."""
    model_name = _normalize_model(model)
    if not data or not isinstance(data, dict):
        return UsageMetrics(model=model_name, cost_source="zero")

    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    tokens_out = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )

    cost_raw = usage.get("cost")
    if cost_raw is None:
        cost_raw = usage.get("total_cost")
    if cost_raw is None:
        hidden = data.get("_hidden_params") or data.get("hidden_params") or {}
        if isinstance(hidden, dict):
            cost_raw = hidden.get("response_cost")

    if cost_raw is not None:
        try:
            cost = float(cost_raw)
            return UsageMetrics(
                model=model_name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=max(0.0, cost),
                cost_source="provider",
            )
        except (TypeError, ValueError):
            pass

    if tokens_in or tokens_out:
        return UsageMetrics(
            model=model_name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=estimate_cost(model_name, tokens_in, tokens_out),
            cost_source="estimate",
        )

    return UsageMetrics(model=model_name, cost_source="zero")


def usage_from_crewai_result(result: Any, *, model: str) -> UsageMetrics:
    """Best-effort extract token usage from a crewAI kickoff result."""
    model_name = _normalize_model(model)
    if result is None:
        return UsageMetrics(model=model_name, cost_source="zero")

    token_usage = getattr(result, "token_usage", None)
    if token_usage is None and isinstance(result, dict):
        token_usage = result.get("token_usage")

    if token_usage is None:
        return UsageMetrics(model=model_name, cost_source="zero")

    if isinstance(token_usage, dict):
        tin = int(
            token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0
        )
        tout = int(
            token_usage.get("completion_tokens")
            or token_usage.get("output_tokens")
            or 0
        )
        total = int(token_usage.get("total_tokens") or 0)
        cost_raw = token_usage.get("total_cost") or token_usage.get("cost")
    else:
        tin = int(
            getattr(token_usage, "prompt_tokens", None)
            or getattr(token_usage, "input_tokens", None)
            or 0
        )
        tout = int(
            getattr(token_usage, "completion_tokens", None)
            or getattr(token_usage, "output_tokens", None)
            or 0
        )
        total = int(getattr(token_usage, "total_tokens", None) or 0)
        cost_raw = getattr(token_usage, "total_cost", None) or getattr(
            token_usage, "cost", None
        )

    if tin == 0 and tout == 0 and total > 0:
        # Unknown split — attribute all to input for budget conservatism
        tin = total

    if cost_raw is not None:
        try:
            return UsageMetrics(
                model=model_name,
                tokens_in=tin,
                tokens_out=tout,
                cost=max(0.0, float(cost_raw)),
                cost_source="provider",
            )
        except (TypeError, ValueError):
            pass

    if tin or tout:
        return UsageMetrics(
            model=model_name,
            tokens_in=tin,
            tokens_out=tout,
            cost=estimate_cost(model_name, tin, tout),
            cost_source="estimate",
        )
    return UsageMetrics(model=model_name, cost_source="zero")


def log_usage(
    cost_db_path: str,
    *,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0.0,
    timestamp: str | None = None,
) -> None:
    """Append one cost_logs row. Unknown cost → 0.0 with tokens still recorded."""
    path = Path(cost_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = timestamp or _utc_iso()
    try:
        with sqlite3.connect(str(path)) as conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO cost_logs (timestamp, cost, model, tokens_in, tokens_out)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    float(cost),
                    _normalize_model(model),
                    int(tokens_in),
                    int(tokens_out),
                ),
            )
            conn.commit()
    except Exception:
        logger.exception("cost_logger.log_usage failed for model=%s", model)


def record_usage(
    metrics: UsageMetrics | None = None,
    *,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float | None = None,
    cost_db_path: str | None = None,
) -> UsageMetrics | None:
    """Log usage to explicit path or the active ContextVar scope.

    Returns the metrics written (or None if no ledger path configured).
    Empty zero-token/zero-cost rows are skipped (heuristic path, no LLM).
    """
    path = cost_db_path if cost_db_path is not None else _active_cost_db.get()
    if not path:
        return None

    if metrics is None:
        model_name = _normalize_model(model)
        if cost is None:
            est = estimate_cost(model_name, tokens_in, tokens_out)
            metrics = UsageMetrics(
                model=model_name,
                tokens_in=int(tokens_in or 0),
                tokens_out=int(tokens_out or 0),
                cost=est,
                cost_source="estimate" if est > 0 else "zero",
            )
        else:
            metrics = UsageMetrics(
                model=model_name,
                tokens_in=int(tokens_in or 0),
                tokens_out=int(tokens_out or 0),
                cost=float(cost),
                cost_source="provider",
            )

    if (
        metrics.cost == 0.0
        and metrics.tokens_in == 0
        and metrics.tokens_out == 0
    ):
        logger.debug(
            "cost_logger.record_usage skip empty model=%s", metrics.model
        )
        return metrics

    log_usage(
        path,
        model=metrics.model,
        tokens_in=metrics.tokens_in,
        tokens_out=metrics.tokens_out,
        cost=metrics.cost,
    )
    logger.info(
        "cost_logger recorded model=%s tokens_in=%s tokens_out=%s "
        "cost=%.6f source=%s",
        metrics.model,
        metrics.tokens_in,
        metrics.tokens_out,
        metrics.cost,
        metrics.cost_source,
    )
    return metrics


@contextmanager
def cost_db_scope(cost_db_path: str | None) -> Iterator[str | None]:
    """Bind cost_db_path for nested LLM call sites (router_http, worker, crisis)."""
    token = _active_cost_db.set(cost_db_path)
    try:
        yield cost_db_path
    finally:
        _active_cost_db.reset(token)


def active_cost_db() -> str | None:
    return _active_cost_db.get()
