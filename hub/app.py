"""Cindy hub HTTP API.

Rules this file enforces:
  * a revision is a signed statement; an unsigned or wrongly signed one is 403 (E1)
  * the same bytes in the same container is one revision, forever (A3)
  * the hub records who holds bytes and where; it never receives the bytes (B5)
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

from hub import db as dbm
from hub.records import revision_payload, verify_signature

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------- request bodies


class ProjectIn(BaseModel):
    name: str = Field(min_length=1)


class PartyIn(BaseModel):
    # Admin call: name only -> hub creates the party and returns an invite token.
    # Agent call: invite_token + public_key_b64 -> the device claims it (E1).
    name: str | None = None
    invite_token: str | None = None
    public_key_b64: str | None = None


class ContainerIn(BaseModel):
    number: str = Field(min_length=1)
    title: str = Field(min_length=1)
    type: str = Field(default="document", min_length=1)
    originator_party: str
    state: str = "published"


class RevisionIn(BaseModel):
    container_id: str
    sha256: str
    size: int = Field(ge=0)
    media_type: str
    file_name: str
    revision_code: str
    author_party: str
    created_at: str
    supersedes_id: str | None = None
    signature_b64: str


class HolderIn(BaseModel):
    party_id: str
    path: str = Field(min_length=1)


class AddressIn(BaseModel):
    host: str | None = None
    port: int | None = None
    address: str | None = None


# ------------------------------------------------------------------ serialisation


def _holder_rows(conn, revision_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT h.revision_id, h.party_id, p.name AS party_name, h.path,"
        " h.verified_at, l.address"
        " FROM holder h JOIN party p ON p.id = h.party_id"
        " LEFT JOIN party_link l ON l.party_id = h.party_id"
        " WHERE h.revision_id = ? ORDER BY h.verified_at, h.party_id",
        (revision_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _revision_dict(conn, row, with_holders: bool = True) -> dict:
    item = {
        "id": row["id"],
        "container_id": row["container_id"],
        "container_number": row["container_number"],
        "container_title": row["container_title"],
        "sha256": row["sha256"],
        "size": row["size"],
        "media_type": row["media_type"],
        "file_name": row["file_name"],
        "revision_code": row["revision_code"],
        "author_party": row["author_party"],
        "created_at": row["created_at"],
        "supersedes_id": row["supersedes_id"],
        "signature_b64": row["signature_b64"],
    }
    if with_holders:
        item["holders"] = _holder_rows(conn, row["id"])
    return item


REVISION_SELECT = (
    "SELECT r.*, c.number AS container_number, c.title AS container_title"
    " FROM revision r JOIN container c ON c.id = r.container_id"
)


def _current_revision_id(conn, container_id: str) -> str | None:
    """The revision nothing supersedes; latest wins if two compete (A3)."""
    row = conn.execute(
        "SELECT r.id FROM revision r"
        " WHERE r.container_id = ?"
        "   AND NOT EXISTS (SELECT 1 FROM revision s WHERE s.supersedes_id = r.id)"
        " ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1",
        (container_id,),
    ).fetchone()
    return row["id"] if row else None


def dir_bytes(path: Path) -> int:
    total = 0
    for p in Path(path).rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


# -------------------------------------------------------------------- application


def create_app(db_path: Path | str) -> FastAPI:
    db_path = Path(db_path)
    dbm.init_db(db_path)
    data_dir = db_path.parent

    app = FastAPI(title="Cindy hub", version="P1")

    def get_db():
        conn = dbm.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def project_or_404(conn, project_id: str) -> dict:
        row = conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown project")
        return dict(row)

    def party_or_400(conn, project_id: str, party_id: str) -> dict:
        row = conn.execute(
            "SELECT p.*, l.address FROM party p"
            " LEFT JOIN party_link l ON l.party_id = p.id"
            " WHERE p.id = ? AND p.project_id = ?",
            (party_id, project_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=400, detail="unknown party for this project")
        return dict(row)

    # ---------------------------------------------------------------- health

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "cindy-hub"}

    @app.get("/stats")
    def stats(conn=Depends(get_db)) -> dict[str, Any]:
        counts = {}
        for table in (
            "project",
            "party",
            "container",
            "revision",
            "holder",
            "audit",
            "party_link",
        ):
            counts[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        recorded = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS n FROM revision"
        ).fetchone()["n"]
        return {
            "tables": counts,
            # Size of the whole hub data directory (db + wal + anything else),
            # not just the tables: the point of T2 is that nothing big lands here.
            "data_dir_bytes": dir_bytes(data_dir),
            "recorded_revision_bytes": recorded,
            "data_dir": str(data_dir),
        }

    # --------------------------------------------------------------- projects

    @app.post("/projects", status_code=201)
    def create_project(body: ProjectIn, conn=Depends(get_db)) -> dict:
        pid = dbm.new_id("prj")
        now = dbm.now_iso()
        with conn:
            conn.execute(
                "INSERT INTO project (id, name, created_at) VALUES (?, ?, ?)",
                (pid, body.name, now),
            )
            dbm.audit(
                conn,
                project_id=pid,
                actor_party=None,
                action="project.created",
                subject=pid,
                detail=json.dumps({"name": body.name}),
            )
        return {"id": pid, "name": body.name, "created_at": now}

    # ----------------------------------------------------------------- parties

    @app.post("/projects/{project_id}/parties", status_code=201)
    def create_party(project_id: str, body: PartyIn, conn=Depends(get_db)) -> dict:
        project_or_404(conn, project_id)

        if body.invite_token:
            return _claim_party(conn, project_id, body)
        return _invite_party(conn, project_id, body)

    def _invite_party(conn, project_id: str, body: PartyIn) -> dict:
        # The admin names a party; the key arrives later from the device itself,
        # so the private half never exists anywhere but that machine (E1).
        if not body.name:
            raise HTTPException(status_code=400, detail="name is required to invite a party")
        pid = dbm.new_id("pty")
        # The token names the project and the party so an agent given only
        # `--hub URL --invite TOKEN` knows both without a second lookup. The
        # secret half is what proves the claim; only its hash is stored.
        token = f"{project_id}~{pid}~{secrets.token_urlsafe(24)}"
        now = dbm.now_iso()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO party (id, project_id, name, public_key_b64, created_at)"
                    " VALUES (?, ?, ?, NULL, ?)",
                    (pid, project_id, body.name, now),
                )
                conn.execute(
                    "INSERT INTO party_link (party_id, address, invite_token_hash, bound_at)"
                    " VALUES (?, NULL, ?, NULL)",
                    (pid, hashlib.sha256(token.encode()).hexdigest()),
                )
                dbm.audit(
                    conn,
                    project_id=project_id,
                    actor_party=None,
                    action="party.invited",
                    subject=pid,
                    detail=json.dumps({"name": body.name}),
                )
        except Exception as exc:  # duplicate name is the only realistic failure
            if "UNIQUE" in str(exc):
                raise HTTPException(status_code=409, detail="party name already used") from exc
            raise
        return {
            "id": pid,
            "project_id": project_id,
            "name": body.name,
            "invite_token": token,
            "public_key_b64": None,
        }

    def _claim_party(conn, project_id: str, body: PartyIn) -> dict:
        if not body.public_key_b64:
            raise HTTPException(status_code=400, detail="public_key_b64 is required")
        try:
            token_project, party_id, _secret = (body.invite_token or "").split("~", 2)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="malformed invite token") from exc
        if token_project != project_id:
            raise HTTPException(status_code=403, detail="invite belongs to another project")
        row = conn.execute(
            "SELECT p.*, l.invite_token_hash FROM party p"
            " JOIN party_link l ON l.party_id = p.id"
            " WHERE p.id = ? AND p.project_id = ?",
            (party_id, project_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown invite")
        expected = row["invite_token_hash"]
        if not secrets.compare_digest(
            expected, hashlib.sha256(body.invite_token.encode()).hexdigest()
        ):
            raise HTTPException(status_code=403, detail="bad invite token")
        if row["public_key_b64"] and row["public_key_b64"] != body.public_key_b64:
            # Re-binding a claimed party is a re-issue (E2), not part of P1.
            raise HTTPException(status_code=409, detail="party already bound to a key")
        now = dbm.now_iso()
        with conn:
            conn.execute(
                "UPDATE party SET public_key_b64 = ? WHERE id = ?",
                (body.public_key_b64, party_id),
            )
            conn.execute(
                "UPDATE party_link SET bound_at = COALESCE(bound_at, ?) WHERE party_id = ?",
                (now, party_id),
            )
            dbm.audit(
                conn,
                project_id=project_id,
                actor_party=party_id,
                action="party.key_bound",
                subject=party_id,
                detail=None,
            )
        return {
            "id": party_id,
            "project_id": project_id,
            "name": row["name"],
            "invite_token": None,
            "public_key_b64": body.public_key_b64,
        }

    @app.post("/projects/{project_id}/parties/{party_id}/address")
    def set_address(
        project_id: str,
        party_id: str,
        body: AddressIn = Body(...),
        conn=Depends(get_db),
    ) -> dict:
        project_or_404(conn, project_id)
        party_or_400(conn, project_id, party_id)
        if body.address:
            address = body.address.rstrip("/")
        elif body.host and body.port:
            address = f"http://{body.host}:{body.port}"
        else:
            raise HTTPException(status_code=400, detail="send address or host+port")
        with conn:
            conn.execute(
                "UPDATE party_link SET address = ? WHERE party_id = ?", (address, party_id)
            )
            dbm.audit(
                conn,
                project_id=project_id,
                actor_party=party_id,
                action="party.address_set",
                subject=party_id,
                detail=json.dumps({"address": address}),
            )
        return {"party_id": party_id, "address": address}

    # -------------------------------------------------------------- containers

    @app.post("/projects/{project_id}/containers", status_code=201)
    def create_container(project_id: str, body: ContainerIn, conn=Depends(get_db)) -> dict:
        project_or_404(conn, project_id)
        party_or_400(conn, project_id, body.originator_party)
        cid = dbm.new_id("cnt")
        try:
            with conn:
                conn.execute(
                    "INSERT INTO container (id, project_id, number, title, type,"
                    " originator_party, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        cid,
                        project_id,
                        body.number,
                        body.title,
                        body.type,
                        body.originator_party,
                        body.state,
                    ),
                )
                dbm.audit(
                    conn,
                    project_id=project_id,
                    actor_party=body.originator_party,
                    action="container.created",
                    subject=cid,
                    detail=json.dumps({"number": body.number}),
                )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(
                    status_code=409, detail="container number already used"
                ) from exc
            raise
        return _container_dict(conn, cid)

    def _container_dict(conn, container_id: str) -> dict:
        row = conn.execute("SELECT * FROM container WHERE id = ?", (container_id,)).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM revision WHERE container_id = ?", (container_id,)
        ).fetchone()["n"]
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "number": row["number"],
            "title": row["title"],
            "type": row["type"],
            "originator_party": row["originator_party"],
            "state": row["state"],
            "revision_count": count,
            "current_revision_id": _current_revision_id(conn, container_id),
        }

    @app.get("/projects/{project_id}/containers")
    def list_containers(project_id: str, conn=Depends(get_db)) -> list[dict]:
        project_or_404(conn, project_id)
        rows = conn.execute(
            "SELECT id FROM container WHERE project_id = ? ORDER BY number", (project_id,)
        ).fetchall()
        return [_container_dict(conn, r["id"]) for r in rows]

    # --------------------------------------------------------------- revisions

    @app.post("/projects/{project_id}/revisions", status_code=201)
    def post_revision(
        project_id: str,
        body: RevisionIn,
        response: Response,
        conn=Depends(get_db),
    ) -> dict:
        project_or_404(conn, project_id)

        if not SHA256_RE.match(body.sha256):
            raise HTTPException(status_code=400, detail="sha256 must be 64 lowercase hex")
        container = conn.execute(
            "SELECT * FROM container WHERE id = ? AND project_id = ?",
            (body.container_id, project_id),
        ).fetchone()
        if container is None:
            raise HTTPException(status_code=400, detail="unknown container for this project")

        author = party_or_400(conn, project_id, body.author_party)
        if not author["public_key_b64"]:
            raise HTTPException(status_code=403, detail="author has no bound key")

        payload = revision_payload(
            container_id=body.container_id,
            sha256=body.sha256,
            size=body.size,
            media_type=body.media_type,
            file_name=body.file_name,
            revision_code=body.revision_code,
            author_party=body.author_party,
            created_at=body.created_at,
            supersedes_id=body.supersedes_id,
        )
        # Verified before anything is written: a 403 must leave no row behind.
        if not verify_signature(author["public_key_b64"], body.signature_b64, payload):
            raise HTTPException(status_code=403, detail="bad revision signature")

        if body.supersedes_id:
            prev = conn.execute(
                "SELECT * FROM revision WHERE id = ? AND container_id = ?",
                (body.supersedes_id, body.container_id),
            ).fetchone()
            if prev is None:
                raise HTTPException(
                    status_code=400, detail="supersedes_id is not a revision of this container"
                )

        existing = conn.execute(
            "SELECT * FROM revision WHERE container_id = ? AND sha256 = ?",
            (body.container_id, body.sha256),
        ).fetchone()
        if existing is not None:
            # Idempotent publish: same bytes, same container, same row (A3).
            response.status_code = 200
            row = conn.execute(REVISION_SELECT + " WHERE r.id = ?", (existing["id"],)).fetchone()
            item = _revision_dict(conn, row)
            item["created"] = False
            return item

        rid = dbm.new_id("rev")
        with conn:
            conn.execute(
                "INSERT INTO revision (id, container_id, sha256, size, media_type,"
                " file_name, revision_code, author_party, created_at, signature_b64,"
                " supersedes_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    body.container_id,
                    body.sha256,
                    body.size,
                    body.media_type,
                    body.file_name,
                    body.revision_code,
                    body.author_party,
                    body.created_at,
                    body.signature_b64,
                    body.supersedes_id or None,
                ),
            )
            dbm.audit(
                conn,
                project_id=project_id,
                actor_party=body.author_party,
                action="revision.published",
                subject=rid,
                detail=json.dumps(
                    {"container": container["number"], "sha256": body.sha256}
                ),
                signature_b64=body.signature_b64,
            )
        row = conn.execute(REVISION_SELECT + " WHERE r.id = ?", (rid,)).fetchone()
        item = _revision_dict(conn, row)
        item["created"] = True
        return item

    @app.get("/projects/{project_id}/revisions")
    def list_revisions(
        project_id: str,
        since: str | None = Query(default=None),
        conn=Depends(get_db),
    ) -> list[dict]:
        project_or_404(conn, project_id)
        sql = REVISION_SELECT + " WHERE c.project_id = ?"
        params: list[Any] = [project_id]
        if since:
            try:
                datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="since must be ISO-8601") from exc
            sql += " AND r.created_at > ?"
            params.append(since)
        sql += " ORDER BY r.created_at, r.rowid"
        rows = conn.execute(sql, params).fetchall()
        return [_revision_dict(conn, r) for r in rows]

    # ----------------------------------------------------------------- holders

    def revision_or_404(conn, project_id: str, revision_id: str):
        row = conn.execute(
            REVISION_SELECT + " WHERE r.id = ? AND c.project_id = ?",
            (revision_id, project_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown revision for this project")
        return row

    @app.get("/projects/{project_id}/revisions/{revision_id}/holders")
    def list_holders(project_id: str, revision_id: str, conn=Depends(get_db)) -> list[dict]:
        project_or_404(conn, project_id)
        revision_or_404(conn, project_id, revision_id)
        return _holder_rows(conn, revision_id)

    @app.post("/projects/{project_id}/revisions/{revision_id}/holders", status_code=201)
    def add_holder(
        project_id: str, revision_id: str, body: HolderIn, conn=Depends(get_db)
    ) -> dict:
        project_or_404(conn, project_id)
        revision_or_404(conn, project_id, revision_id)
        party_or_400(conn, project_id, body.party_id)
        now = dbm.now_iso()
        with conn:
            # Re-registering is a fresh verification timestamp, not a new row:
            # a party holds a revision at one path (A2).
            conn.execute(
                "INSERT INTO holder (revision_id, party_id, path, verified_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT (revision_id, party_id)"
                " DO UPDATE SET path = excluded.path, verified_at = excluded.verified_at",
                (revision_id, body.party_id, body.path, now),
            )
            dbm.audit(
                conn,
                project_id=project_id,
                actor_party=body.party_id,
                action="holder.registered",
                subject=revision_id,
                detail=json.dumps({"path": body.path}),
            )
        return {
            "revision_id": revision_id,
            "party_id": body.party_id,
            "path": body.path,
            "verified_at": now,
        }

    return app
