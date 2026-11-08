"""SQLite schema and connection helpers for the hub.

SQLite because P1 is one process on one machine: no server to install, and the
whole record is a single file the client can carry away at project close (B6).
Every table here holds metadata only. There is deliberately no blob table and no
byte column: the hub cannot store a file even by accident (B5).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS party (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES project(id),
    name           TEXT NOT NULL,
    public_key_b64 TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (project_id, name)
);

-- Runtime facts about a party that are not part of the identity record:
-- where its blob service listens, and the hash of its unused invite token.
CREATE TABLE IF NOT EXISTS party_link (
    party_id         TEXT PRIMARY KEY REFERENCES party(id),
    address          TEXT,
    invite_token_hash TEXT NOT NULL,
    bound_at         TEXT
);

CREATE TABLE IF NOT EXISTS container (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES project(id),
    number           TEXT NOT NULL,
    title            TEXT NOT NULL,
    type             TEXT NOT NULL,
    originator_party TEXT NOT NULL REFERENCES party(id),
    state            TEXT NOT NULL,
    UNIQUE (project_id, number)
);

-- One row per distinct content in a container. UNIQUE(container_id, sha256) is
-- what makes publishing the same bytes twice return the existing revision
-- instead of creating a second one, even under a retry.
CREATE TABLE IF NOT EXISTS revision (
    id            TEXT PRIMARY KEY,
    container_id  TEXT NOT NULL REFERENCES container(id),
    sha256        TEXT NOT NULL,
    size          INTEGER NOT NULL,
    media_type    TEXT NOT NULL,
    file_name     TEXT NOT NULL,
    revision_code TEXT NOT NULL,
    author_party  TEXT NOT NULL REFERENCES party(id),
    created_at    TEXT NOT NULL,
    signature_b64 TEXT NOT NULL,
    supersedes_id TEXT REFERENCES revision(id),
    UNIQUE (container_id, sha256)
);

CREATE INDEX IF NOT EXISTS revision_by_container
    ON revision (container_id, created_at);

-- Who actually holds the bytes, and where. A path is a claim by that party
-- about its own storage; it is not addressable from outside.
CREATE TABLE IF NOT EXISTS holder (
    revision_id TEXT NOT NULL REFERENCES revision(id),
    party_id    TEXT NOT NULL REFERENCES party(id),
    path        TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (revision_id, party_id)
);

CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL,
    actor_party   TEXT,
    action        TEXT NOT NULL,
    subject       TEXT,
    detail_json   TEXT,
    at            TEXT NOT NULL,
    signature_b64 TEXT
);
"""


def now_iso() -> str:
    """UTC timestamp with microseconds, so two revisions in the same second sort."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def new_id(prefix: str) -> str:
    """12 hex characters: short enough for a Windows path (B8), long enough to
    never collide in a project."""
    return f"{prefix}_{secrets.token_hex(6)}"


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(db_path: Path | str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def audit(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    actor_party: str | None,
    action: str,
    subject: str | None,
    detail: str | None,
    signature_b64: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit (project_id, actor_party, action, subject, detail_json, at,"
        " signature_b64) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project_id, actor_party, action, subject, detail, now_iso(), signature_b64),
    )
