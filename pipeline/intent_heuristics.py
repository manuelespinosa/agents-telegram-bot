"""Deterministic NL intent hints — skip flaky router LLM for common ops.

Homelab operators expect inventory and simple lifecycle phrases to work even when
Gemini/crewAI misbehaves. Heuristics never invent VMIDs for writes.
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

# Write verbs (ES + EN)
_START_RE = re.compile(
    r"\b(arranca|arrancar|inicia|iniciar|enciende|encender|start|prende|prender)\b"
)
_STOP_RE = re.compile(
    r"\b(para|parar|apaga|apagar|stop|halt|shutdown|detén|deten|detener)\b"
)
_REBOOT_RE = re.compile(
    r"\b(reinicia|reiniciar|reboot|restart|reincia|reset)\b"
)
_SNAPSHOT_RE = re.compile(
    r"\b(snapshot|instantanea|instantánea|foto|captura)\b"
)
# Prefer explicit "vmid 300" / "vm 300" / bare 2–4 digit ids in ops context
_VMID_RE = re.compile(
    r"\b(?:vmid|vm|ct|lxc)\s*[#:=]?\s*(\d{2,4})\b|\b(\d{2,4})\b",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    raw = (text or "").casefold().strip()
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


def extract_vmid(text: str) -> int | None:
    """Best-effort VMID from operator text. None if missing/ambiguous."""
    t = _fold(text)
    # Prefer labeled ids
    labeled = re.findall(r"\b(?:vmid|vm|ct|lxc)\s*[#:=]?\s*(\d{2,4})\b", t)
    if len(labeled) == 1:
        return int(labeled[0])
    if len(labeled) > 1:
        return None  # ambiguous — force clarify
    # Single bare number only if write/list-ops context has exactly one
    bare = re.findall(r"\b(\d{2,4})\b", t)
    if len(bare) == 1:
        return int(bare[0])
    return None


def is_list_vms_request(text: str) -> bool:
    """True for inventory-style reads (ES/EN, light typo tolerance)."""
    t = _fold(text)
    if not t:
        return False
    if re.search(r"\blist[_ ]?vms?\b", t):
        return True
    if re.search(
        r"\b(lista|listar|muestra|mostrar|inventario)\b.*\b(vms?|maquinas?|cts?)\b", t
    ):
        return True
    if re.search(
        r"\b(vms?|maquinas?|cts?)\b.*\b(lista|listar|inventario)\b", t
    ):
        return True
    words = _words(text)
    if not words:
        return False
    has_list = any(_fuzzy_verb(w) for w in words)
    has_vm = any(
        w in _VM_NOUNS or w.startswith("vm") or w.endswith("vms") for w in words
    )
    if "inventario" in words or (has_list and "cluster" in words):
        return True
    return has_list and has_vm


def _write_intent(text: str) -> str | None:
    """Map write verbs → ActionId intent, or None."""
    t = _fold(text)
    # order: reboot before start (reinicia contains inicia-like stems carefully)
    if _REBOOT_RE.search(t):
        return "vm_reboot"
    if _STOP_RE.search(t):
        return "vm_stop"
    if _START_RE.search(t):
        return "vm_start"
    if _SNAPSHOT_RE.search(t) and re.search(r"\b(crea|create|haz|hacer|nuevo|new)\b", t):
        return "snapshot_create"
    if _SNAPSHOT_RE.search(t) and re.search(r"\b(lista|list|muestra|show)\b", t):
        return "snapshot_list"
    if _SNAPSHOT_RE.search(t):
        # bare "snapshot 300" → create is common operator shorthand
        return "snapshot_create"
    return None


def try_deterministic_decision(message: str) -> RouterDecision | None:
    """High-confidence decisions for safe shortcuts; None if LLM should classify."""
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

    write = _write_intent(message)
    if write is None:
        return None

    vmid = extract_vmid(message)
    if vmid is None:
        return RouterDecision(
            intent=write,
            confidence=0.9,
            severity="info",
            route="clarify",
            missing_params=["vmid"],
            extracted_params={"action_id": write},
            rationale=f"heuristic:{write}:missing_vmid",
        )

    # snapshot_list is read; others write via HITL
    return RouterDecision(
        intent=write,
        confidence=0.92,
        severity="info",
        route="worker",
        missing_params=[],
        extracted_params={
            "skip_llm": True,
            "action_id": write,
            "vmid": vmid,
        },
        rationale=f"heuristic:{write}:{vmid}",
    )
