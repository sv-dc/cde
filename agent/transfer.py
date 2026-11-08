"""One file, from a peer, verified before it counts as held.

The hash is computed while the bytes stream in, so a peer that lies about the
content costs bandwidth and nothing else: the temp file is deleted and the
revision stays "not held". There is no path here that writes to a final
destination before the digest matches.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import httpx

CHUNK = 256 * 1024


class TransferError(RuntimeError):
    pass


class BlobHashMismatch(TransferError):
    """The peer served bytes that are not the revision it claimed to have."""


def download_blob(
    url: str,
    sha256: str,
    expected_size: int,
    tmp_dir: Path,
) -> Path:
    """Stream `url` to a temp file in tmp_dir, verify sha256 and size, return it.

    Raises TransferError if the peer cannot be reached or answers with an error,
    and BlobHashMismatch if the bytes are wrong. In both cases nothing is left
    behind: the caller's local state is unchanged.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f".pull-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    written = 0
    try:
        # trust_env=False: a peer on the same LAN must not be routed through a
        # proxy that happens to be configured for the internet.
        with httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise TransferError(
                        f"{url} answered {response.status_code}"
                    )
                with open(tmp, "wb") as fh:
                    for chunk in response.iter_bytes(CHUNK):
                        if not chunk:
                            continue
                        digest.update(chunk)
                        written += len(chunk)
                        fh.write(chunk)
                    fh.flush()
                    os.fsync(fh.fileno())
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise TransferError(f"cannot fetch {url}: {exc}") from exc
    except TransferError:
        tmp.unlink(missing_ok=True)
        raise

    if written != expected_size:
        tmp.unlink(missing_ok=True)
        raise BlobHashMismatch(
            f"{url}: got {written} bytes, revision says {expected_size}"
        )
    if digest.hexdigest() != sha256:
        tmp.unlink(missing_ok=True)
        raise BlobHashMismatch(
            f"{url}: sha256 {digest.hexdigest()[:12]} does not match {sha256[:12]}"
        )
    return tmp
