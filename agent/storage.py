"""Where bytes live on one machine, and how they are written without ever being
rewritten.

Layout: ROOT/_cindy/<project_id>/<container-number>/<revision-code>_<file-name>

One level below the project folder, as B8 requires. Published files are set
read-only and the only writer is `place_file`, which refuses to put different
bytes at an existing path: a changed file is a new revision with its own path
(A3).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path

from agent.state import INDEX_FILE, STATE_DIR

CHUNK = 1024 * 1024
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize(name: str) -> str:
    """One path component, safe on Windows and short enough to stay readable."""
    cleaned = _UNSAFE.sub("_", name.strip()).strip("._")
    return cleaned or "unnamed"


def container_dir(state, number: str) -> Path:
    return state.project_root / sanitize(number)


def revision_path(state, number: str, revision_code: str, file_name: str, sha256: str) -> Path:
    """Deterministic, human-readable path for a revision.

    `{code}_{file}` normally. If that name is already taken by different bytes
    (same code, different content), the hash prefix disambiguates, so a second
    revision can never land on the first one's path.
    """
    folder = container_dir(state, number)
    plain = folder / f"{sanitize(revision_code)}_{sanitize(file_name)}"
    if plain.exists() and sha256_file(plain) != sha256:
        return folder / f"{sanitize(revision_code)}_{sha256[:8]}_{sanitize(file_name)}"
    return plain


def make_readonly(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IREAD)
    except OSError:
        pass  # a share that ignores the attribute must not stop a publish


def is_readonly(path: Path) -> bool:
    mode = path.stat().st_mode
    return not (mode & stat.S_IWRITE)


def clear_readonly(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def place_file(tmp_path: Path, dest: Path, sha256: str) -> Path:
    """Move a verified temp file into its final place, read-only.

    If the destination already holds these exact bytes the temp file is dropped
    and the existing copy is kept (that is a repeated publish or a repeated
    pull). If it holds different bytes, that is corruption being repaired at the
    same path, and the caller had to ask for it explicitly by naming the path.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.is_file() and sha256_file(dest) == sha256:
            tmp_path.unlink(missing_ok=True)
            make_readonly(dest)
            return dest
        clear_readonly(dest)
    os.replace(tmp_path, dest)
    make_readonly(dest)
    return dest


def store_local_copy(
    state,
    *,
    container_number: str,
    revision_code: str,
    file_name: str,
    sha256: str,
    source: Path,
) -> Path:
    """Publish a local file into the project folder: copy, then place read-only.

    The source file is never touched. The copy goes to a temp name in the same
    folder first so a half-written file can never appear as a revision.
    """
    dest = revision_path(state, container_number, revision_code, file_name, sha256)
    if dest.exists() and sha256_file(dest) == sha256:
        make_readonly(dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".pub-{uuid.uuid4().hex}.part"
    try:
        shutil.copyfile(source, tmp)
        return place_file(tmp, dest, sha256)
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------------ index


class LocalIndex:
    """sha256 -> the local copy. Not the record (the hub is); just this disk's map.

    Without it the blob service would have to hash every file on every request,
    and `pull` could not tell "I hold this" from "I have a file with that name".
    """

    def __init__(self, root: str | Path, data: dict | None = None):
        self.root = Path(root)
        self.path = self.root / STATE_DIR / INDEX_FILE
        self.data = data if data is not None else {"blobs": {}}

    @property
    def blobs(self) -> dict:
        return self.data.setdefault("blobs", {})

    @classmethod
    def load(cls, root: str | Path) -> "LocalIndex":
        path = Path(root) / STATE_DIR / INDEX_FILE
        if not path.exists():
            return cls(root)
        return cls(root, json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, sha256: str) -> dict | None:
        return self.blobs.get(sha256)

    def abs_path(self, entry: dict) -> Path:
        return self.root / entry["path"]

    def add(
        self,
        *,
        sha256: str,
        size: int,
        path: Path,
        revision_id: str,
        revision_code: str,
        file_name: str,
        container_id: str,
        container_number: str,
    ) -> dict:
        entry = {
            "sha256": sha256,
            "size": int(size),
            "path": path.relative_to(self.root).as_posix(),
            "revision_id": revision_id,
            "revision_code": revision_code,
            "file_name": file_name,
            "container_id": container_id,
            "container_number": container_number,
        }
        self.blobs[sha256] = entry
        return entry

    def holds_valid(self, sha256: str) -> bool:
        entry = self.get(sha256)
        if entry is None:
            return False
        path = self.abs_path(entry)
        return path.is_file() and path.stat().st_size == entry["size"]
