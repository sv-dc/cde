"""The one file an agent cannot lose: ROOT/.cindy/state.json.

It holds the party's Ed25519 private key. Nothing else on the machine is
authoritative: the hub is the record, ROOT/_cindy is the bytes, and this file is
the identity that makes the record attributable (E1).
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

STATE_DIR = ".cindy"
STATE_FILE = "state.json"
INDEX_FILE = "index.json"


@dataclass
class State:
    root: str
    project_id: str
    party_id: str
    party_name: str
    hub_url: str
    host: str
    blob_port: int
    private_key_b64: str
    public_key_b64: str
    created_at: str

    # -------------------------------------------------------------- persistence

    @staticmethod
    def path_for(root: str | Path) -> Path:
        return Path(root) / STATE_DIR / STATE_FILE

    @classmethod
    def load(cls, root: str | Path) -> "State":
        path = cls.path_for(root)
        if not path.exists():
            raise FileNotFoundError(
                f"no agent state at {path}; run 'cindy init --root {root} ...' first"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    def save(self) -> Path:
        path = self.path_for(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)
        # Best effort: on Windows this is a no-op, on POSIX it keeps the key private.
        try:
            path.chmod(0o600)
            path.parent.chmod(0o700)
        except OSError:
            pass
        return path

    # -------------------------------------------------------------------- crypto

    def private_key(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(self.private_key_b64)
        )

    @property
    def address(self) -> str:
        return f"http://{self.host}:{self.blob_port}"

    @property
    def project_root(self) -> Path:
        """Where this party's copies of the project's bytes live (B1)."""
        return Path(self.root) / "_cindy" / self.project_id
