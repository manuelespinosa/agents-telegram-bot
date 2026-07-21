"""Docker log crawler + structured summarize (MON-01 / MON-02).

MON-01 v1 scope (intentional partial):
  - Proxmox API health (other modules) + Docker container logs via docker-py
  - journalctl / host syslog are DEFERRED (CONTEXT D-05) — not collected here

Pipeline (D-06, D-08):
  docker-py logs → CRITICAL_PATTERNS filter → structured list[dict]
  → summarize (counts + short samples) → optional llm_polish on aggregate only
  Never send raw multi-KB log buffers to LiteLLM. On LLM failure, return
  the deterministic structured string.

Security (T-02-11): only containers.list / get / logs — no start/stop/create/remove.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, defaultdict
from typing import Any

import docker
import httpx

from config import settings

logger = logging.getLogger(__name__)

# Critical log patterns (case-insensitive). OOM / disk full map to dedicated levels.
CRITICAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bFATAL\b", re.I), "FATAL"),
    (re.compile(r"\bERROR\b", re.I), "ERROR"),
    (re.compile(r"out of memory|oom|Kill process", re.I), "OOM"),
    (re.compile(r"no space left on device|disk full|ENOSPC", re.I), "DISK"),
    (re.compile(r"\bCRITICAL\b", re.I), "CRITICAL"),
]

_TS_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T[^\s]+)\s+(?P<body>.*)$"
)
_LEVEL_IN_BODY = re.compile(
    r"\b(?P<lvl>FATAL|ERROR|CRITICAL|WARN|WARNING|INFO|DEBUG)\b", re.I
)

# Budget for sample lines embedded in structured summary (no raw dump)
_MAX_SAMPLES = 5
_MAX_SAMPLE_LEN = 160
_MAX_SUMMARY_CHARS = 1800
_MAX_LLM_INPUT = 4000


class LogCrawler:
    """Read Docker logs (docker-py) and extract critical structured events.

    Optional ``client`` injects a docker-py client (tests); default uses
    ``docker.from_env()`` against the mounted socket (read-only usage).
    """

    def __init__(self, client: Any | None = None):
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    async def get_critical_events(
        self, container: str = "", lines: int = 100
    ) -> list[dict]:
        """Return critical events from Docker logs.

        Args:
            container: Container name/id (empty = all running).
            lines: Tail lines per container.

        Returns:
            list[dict] with keys: timestamp, level, message, container
        """
        return await asyncio.to_thread(
            self._get_critical_events_sync, container, lines
        )

    def _get_critical_events_sync(
        self, container: str, lines: int
    ) -> list[dict]:
        client = self._get_client()
        events: list[dict] = []
        try:
            if container:
                targets = [client.containers.get(container)]
            else:
                # Read-only list of running containers
                targets = list(client.containers.list())
        except Exception as e:
            logger.error("LogCrawler: docker list/get failed: %s", e)
            return []

        for c in targets:
            name = getattr(c, "name", None) or (
                c.attrs.get("Name", "unknown").lstrip("/")
                if getattr(c, "attrs", None)
                else "unknown"
            )
            try:
                raw = c.logs(
                    tail=lines,
                    timestamps=True,
                    stdout=True,
                    stderr=True,
                )
            except Exception as e:
                logger.warning(
                    "LogCrawler: logs() failed for %s: %s", name, e
                )
                continue
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            for line in text.splitlines():
                parsed = self._parse_line(line, name)
                if parsed is not None:
                    events.append(parsed)
        return events

    def _parse_line(self, line: str, container_name: str) -> dict | None:
        if not line or not line.strip():
            return None
        ts = ""
        body = line
        m = _TS_RE.match(line.strip())
        if m:
            ts = m.group("ts")
            body = m.group("body")

        level: str | None = None
        for pattern, lvl in CRITICAL_PATTERNS:
            if pattern.search(body) or pattern.search(line):
                level = lvl
                break
        if level is None:
            return None

        # Prefer explicit level token in body when present
        lm = _LEVEL_IN_BODY.search(body)
        if lm and lm.group("lvl").upper() in ("FATAL", "ERROR", "CRITICAL"):
            level = lm.group("lvl").upper()

        return {
            "timestamp": ts,
            "level": level,
            "message": body.strip()[:500],
            "container": container_name,
        }

    def structured_summary(self, events: list[dict]) -> str:
        """Deterministic structured summary (counts + short samples)."""
        if not events:
            return "• Logs: No hay eventos críticos recientes."

        by_level: Counter[str] = Counter()
        by_container: Counter[str] = Counter()
        samples: list[str] = []

        for e in events:
            lvl = str(e.get("level") or "UNKNOWN")
            cname = str(e.get("container") or "?")
            by_level[lvl] += 1
            by_container[cname] += 1
            if len(samples) < _MAX_SAMPLES:
                msg = str(e.get("message") or "")[:_MAX_SAMPLE_LEN]
                samples.append(f"  - [{lvl}] {cname}: {msg}")

        lines = ["• Logs críticos (resumen estructurado):"]
        level_bits = ", ".join(f"{k}={v}" for k, v in sorted(by_level.items()))
        lines.append(f"  Niveles: {level_bits} (total={len(events)})")
        cont_bits = ", ".join(
            f"{k}={v}" for k, v in by_container.most_common(8)
        )
        lines.append(f"  Contenedores: {cont_bits}")
        if samples:
            lines.append("  Muestras:")
            lines.extend(samples)
        text = "\n".join(lines)
        if len(text) > _MAX_SUMMARY_CHARS:
            text = text[: _MAX_SUMMARY_CHARS - 20] + "\n… (truncado)"
        return text

    async def llm_polish(self, summary_text: str) -> str | None:
        """Optional LiteLLM polish on structured aggregate only (never raw logs)."""
        if not summary_text or not settings.litellm_url:
            return None
        url = settings.litellm_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            # settings default is .../v1 — append completions path
            if url.endswith("/v1"):
                url = f"{url}/chat/completions"
            else:
                url = f"{url}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    url,
                    json={
                        "model": "gemini-flash",
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Resume eventos de infra en 2-3 frases, "
                                    "español, sin inventar. No pidas acciones."
                                ),
                            },
                            {
                                "role": "user",
                                "content": summary_text[:_MAX_LLM_INPUT],
                            },
                        ],
                        "max_tokens": 150,
                        "temperature": 0.2,
                    },
                )
                if r.status_code != 200:
                    logger.info(
                        "llm_polish non-200 status=%s", r.status_code
                    )
                    return None
                data = r.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content")
                )
                if not content or not str(content).strip():
                    return None
                return str(content).strip()
        except Exception as e:
            logger.info("llm_polish failed (fallback to structured): %s", e)
            return None

    async def summarize(self, events: list) -> str:
        """Structured summary; optional LLM polish with deterministic fallback.

        Never embeds a full raw log dump — only counts and short samples.
        """
        structured = self.structured_summary(list(events or []))
        if not events:
            return structured
        polished = await self.llm_polish(structured)
        if polished:
            return f"{structured}\n\n• Resumen LLM:\n  {polished}"
        return structured
