"""Signing the revision record. Verification lives on the hub (hub/records.py)."""

from __future__ import annotations

import base64

from hub.records import canonical_bytes


def sign_payload(state, payload: dict) -> str:
    """Base64 Ed25519 signature over the canonical bytes of the payload.

    Ed25519 is deterministic: the same payload from the same key always produces
    the same signature, which is what makes a retried publish a no-op rather than
    a different assertion.
    """
    signature = state.private_key().sign(canonical_bytes(payload))
    return base64.b64encode(signature).decode("ascii")
