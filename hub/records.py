"""The signed record format, shared by the hub and the agent.

A revision record is signed with the author's Ed25519 key over a canonical JSON
encoding of exactly the fields below. Both sides build the payload with the same
function, so a field added on one side and forgotten on the other is impossible:
the signature simply stops verifying.

Canonical form: sorted keys, no whitespace, UTF-8. Field *presence* matters, so
every field is always written, using an empty string for "no supersedes".
"""

from __future__ import annotations

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Everything the author asserts, and nothing the hub assigns (id, receipt time).
REVISION_FIELDS = (
    "container_id",
    "sha256",
    "size",
    "media_type",
    "file_name",
    "revision_code",
    "author_party",
    "created_at",
    "supersedes_id",
)


def revision_payload(
    *,
    container_id: str,
    sha256: str,
    size: int,
    media_type: str,
    file_name: str,
    revision_code: str,
    author_party: str,
    created_at: str,
    supersedes_id: str | None,
) -> dict:
    """The exact dict that gets signed. None becomes "" so the key always exists."""
    return {
        "container_id": container_id,
        "sha256": sha256,
        "size": int(size),
        "media_type": media_type,
        "file_name": file_name,
        "revision_code": revision_code,
        "author_party": author_party,
        "created_at": created_at,
        "supersedes_id": supersedes_id or "",
    }


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def verify_signature(public_key_b64: str, signature_b64: str, payload: dict) -> bool:
    """True only for a valid Ed25519 signature over this exact payload.

    Any decoding error is a failed verification, never an exception the caller
    might accidentally treat as success.
    """
    try:
        raw_key = base64.b64decode(public_key_b64, validate=True)
        raw_sig = base64.b64decode(signature_b64, validate=True)
        public = Ed25519PublicKey.from_public_bytes(raw_key)
        public.verify(raw_sig, canonical_bytes(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
