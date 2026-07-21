"""HMAC-SHA256 payload lock (HITL-03). Stdlib only — no third-party crypto."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def canonical_payload(
    action_id: str,
    params: dict[str, Any],
    request_id: str,
    expires_at: str,
) -> bytes:
    """Build sorted compact JSON UTF-8 over action_id, params, request_id, expires_at."""
    body = {
        "action_id": action_id,
        "params": params,
        "request_id": request_id,
        "expires_at": expires_at,
    }
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign_payload(secret: bytes, payload: bytes) -> str:
    """Return hex digest of HMAC-SHA256(secret, payload)."""
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_payload(secret: bytes, payload: bytes, signature_hex: str) -> bool:
    """Timing-safe verify of hex HMAC signature against payload."""
    expected = sign_payload(secret, payload)
    return hmac.compare_digest(expected, signature_hex)
