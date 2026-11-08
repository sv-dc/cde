"""The blob service: GET /blob/<sha256> from a party's own storage.

This is the direct agent-to-agent path (B2). The hub never sees this traffic and
cannot proxy it: it only knows the address a party announced.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from agent.state import State
from agent.storage import LocalIndex


def create_app(root: str | Path, state: State | None = None) -> FastAPI:
    state = state or State.load(root)
    app = FastAPI(title=f"Cindy agent {state.party_name}", version="P1")

    def index() -> LocalIndex:
        # Re-read per request: publishes happen in another process and the file
        # is tiny. A stale in-memory map would make a fresh publish unpullable.
        return LocalIndex.load(state.root)

    @app.get("/health")
    def health() -> dict:
        idx = index()
        return {
            "ok": True,
            "service": "cindy-agent-blob",
            "party_id": state.party_id,
            "party_name": state.party_name,
            "project_id": state.project_id,
            "blobs": len(idx.blobs),
        }

    @app.get("/blob/{sha256}")
    def get_blob(sha256: str):
        idx = index()
        entry = idx.get(sha256)
        if entry is None:
            raise HTTPException(status_code=404, detail="not held")
        path = idx.abs_path(entry)
        if not path.is_file():
            # The record says held, the disk disagrees: say 404, not 500. The
            # peer will try another holder and the local verify will report it.
            raise HTTPException(status_code=404, detail="not held")
        return FileResponse(
            path,
            media_type="application/octet-stream",
            headers={"X-Cindy-Sha256": sha256},
        )

    return app
