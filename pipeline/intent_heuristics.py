"""Deterministic NL intent hints — skip flaky router LLM for common reads.

Homelab operators expect "lista las VMs" (and typos) to behave like /list_vms
without requiring Gemini/LiteLLM. Heuristics never invent VMIDs for writes.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from pipeline.models import RouterDecision

_LIST_VERBS = (
    "lista",
    "listar",
    "list",
    "ls",
    "muestra",
    "mostrar",
    "inventario",
    "enumera",
    "enumerar",
    "dame",
    "ver",
    "show",
    "get",
)
_VM_NOUNS = (
    "vm",
    "vms",
    "maquina",
    "maquinas",
    "maquinasvirtuales",
    "ct",
    "cts",
    "lxc",
    "qemu",
    "guest",
    "guests",
    "contenedor",
    "contenedores",
    "cluster",
    "inventario",
)


def _fold(text: str) -> str:
    raw = (text or "").casefold().strip()
    # strip accents: máquina → maquina
    nk = unicodedata.normalize("NFKD", raw)
    return "".join(c for c in nk if not unicodedata.combining(c))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", _fold(text))


def _fuzzy_verb(word: str) -> bool:
    if word in _LIST_VERBS:
        return True
    for v in _LIST_VERBS:
        if abs(len(word) - len(v)) > 2:
            continue
        if SequenceMatcher(None, word, v).ratio() >= 0.72:
            return True
    return False


def is_list_vms_request(text: str) -> bool:
    """True for inventory-style reads (ES/EN, light typo tolerance)."""
    t = _fold(text)
    if not t:
        return False
    # compact patterns
    if re.search(r"\blist[_ ]?vms?\b", t):
        return True
    if re.search(r"\b(lista|listar|muestra|mostrar|inventario)\b.*\b(vms?|maquinas?|cts?)\b", t):
        return True
    if re.search(r"\b(vms?|maquinas?|cts?)\b.*\b(lista|listar|inventario)\b", t):
        return True
    words = _words(text)
    if not words:
        return False
    has_list = any(_fuzzy_verb(w) for w in words)
    has_vm = any(
        w in _VM_NOUNS or w.startswith("vm") or w.endswith("vms") for w in words
    )
    # "inventario del cluster" without explicit vm token
    if "inventario" in words or (has_list and "cluster" in words):
        return True
    return has_list and has_vm


def try_deterministic_decision(message: str) -> RouterDecision | None:
    """Return a high-confidence worker decision for safe read shortcuts, else None."""
    if is_list_vms_request(message):
        return RouterDecision(
            intent="list_vms",
            confidence=0.95,
            severity="info",
            route="worker",
            missing_params=[],
            extracted_params={"skip_llm": True, "action_id": "list_vms"},
            rationale="heuristic:list_vms",
        )
    return None
