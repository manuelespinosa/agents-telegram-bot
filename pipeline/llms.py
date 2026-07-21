"""LiteLLM OpenAI-compatible LLM factory for crewAI agents."""
from __future__ import annotations

from typing import Any

from config import settings

# Temperature defaults per role (plan interfaces)
_TEMPERATURES: dict[str, float] = {
    "gemini-flash": 0.1,
    "qwen-coder": 0.2,
    "deepseek-r1": 0.2,
}


def make_llm(
    model_name: str | None = None,
    *,
    temperature: float | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> Any:
    """Build crewai.LLM pointed at the LiteLLM proxy (openai/ alias).

    Raises ImportError if crewai is not installed (Docker image must pin crewai).
    """
    try:
        from crewai import LLM
    except ImportError as e:  # pragma: no cover - local Python 3.14 without crewai
        raise ImportError(
            "crewai is required for make_llm; install crewai>=1.15.4,<1.16 "
            "(Python <3.14) or run pipeline in the telegram-bot image."
        ) from e

    name = model_name or settings.pipeline_router_model
    temp = (
        temperature
        if temperature is not None
        else _TEMPERATURES.get(name, 0.2)
    )
    return LLM(
        model=f"openai/{name}",
        base_url=base_url or settings.litellm_url,
        api_key=api_key or settings.litellm_api_key,
        temperature=temp,
    )
