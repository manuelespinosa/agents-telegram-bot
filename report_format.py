"""Safe Telegram message formatting helpers (length cap + HTML escape).

Prefer plain text or HTML escape for dynamic Proxmox/Docker names.
Never use Markdown parse_mode on untrusted/dynamic fields (T-02-13).
"""
from __future__ import annotations

import html
from typing import Iterable

# Practical Telegram limit with headroom under 4096 hard cap
DEFAULT_CHUNK = 3500


def html_escape(text: str) -> str:
    """Escape dynamic content for parse_mode=HTML."""
    return html.escape(str(text), quote=False)


def split_message(text: str, limit: int = DEFAULT_CHUNK) -> list[str]:
    """Split ``text`` into chunks under ``limit`` characters.

    Prefers splitting on newlines; falls back to hard cuts if a single
    paragraph exceeds the limit.
    """
    if text is None:
        return []
    text = str(text)
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        # Prefer break at last newline in window
        br = window.rfind("\n")
        if br < limit // 4:
            # No good newline — hard cut
            br = limit
        piece = remaining[:br]
        if not piece:
            piece = remaining[:limit]
            br = limit
        chunks.append(piece)
        remaining = remaining[br:].lstrip("\n")
    return chunks


def join_sections(sections: Iterable[str], sep: str = "\n\n") -> str:
    """Join non-empty section strings."""
    return sep.join(s for s in sections if s)
