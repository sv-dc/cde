"""The seven P1 tests. Each one runs the hub and both agents as real processes.

T1 byte-identical publish/pull          T5 a wrong key is rejected and leaves no row
T2 the hub stores no file bytes          T6 losing the holder and reconnecting
T3 supersede keeps both revisions        T7 pulling twice changes nothing
T4 tamper detection and repair
"""

from __future__ import annotations

import hashlib
import http.server
import threading
from pathlib import Path

import pytest

from agent import storage as agent_storage
from agent.transfer import BlobHashMismatch, download_blob

from conftest import free_port


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- T1


def test_T1_publish_and_pull_are_byte_identical(cluster, pdf):
    a_pdf = pdf("S100_A.pdf", "Site plan revision A")
    cluster.create_container("S100", "Site plan", "Company A")
    cluster.publish("Company A", "S100", a_pdf, "A")
    cluster.start_serve("Company A")
    cluster.start_serve("Company B")

    result = cluster.pull("Company B")
    assert result.returncode == 0, result.stdout + result.stderr

    sha = sha256_of(a_pdf)
    b_files = cluster.local_files("Company B")
    assert len(b_files) == 1
    assert sha256_of(b_files[0]) == sha
    assert b_files[0].read_bytes() == a_pdf.read_bytes()

    # The record agrees with the disk on both sides.
    [revision] = cluster.revisions()
    assert revision["sha256"] == sha
    holders = {h["party_name"] for h in cluster.holders(revision["id"])}
    assert holders == {"Company A", "Company B"}


# --------------------------------------------------------------------------- T2


def test_T2_hub_stores_no_file_bytes(cluster, pdf):
    a_pdf = pdf("S100_A.pdf", "Site plan revision A")
    cluster.create_container("S100", "Site plan", "Company A")
    cluster.publish("Company A", "S100", a_pdf, "A")
    cluster.start_serve("Company A")
    cluster.start_serve("Company B")

    before = cluster.hub_data_bytes()
    result = cluster.pull("Company B")
    assert result.returncode == 0, result.stdout + result.stderr
    after = cluster.hub_data_bytes()

    blob_bytes = a_pdf.stat().st_size
    growth = after - before
    print(
        f"\nT2: blob {blob_bytes} bytes transferred; hub data dir "
        f"{before} -> {after} bytes (growth {growth} bytes)"
    )

    assert blob_bytes > 1024 * 1024, "the blob must be bigger than 1 MB to mean anything"
    # Not "a few kilobytes relative to the file": a hard cap, so a future change
    # that starts parking bytes on the hub fails here.
    assert growth < 100 * 1024, f"hub grew by {growth} bytes for a {blob_bytes} byte file"
    assert sha256_of(cluster.local_files("Company B")[0]) == sha256_of(a_pdf)


# --------------------------------------------------------------------------- T3


def test_T3_supersede_keeps_both_revisions(cluster, pdf):
    a_pdf = pdf("S100_A.pdf", "Site plan revision A")
    b_pdf = pdf("S100_B.pdf", "Site plan revision B - grids adjusted")
    assert a_pdf.read_bytes() != b_pdf.read_bytes()

    cluster.create_container("S100", "Site plan", "Company A")
    cluster.start_serve("Company A")
    cluster.start_serve("Company B")

    cluster.publish("Company A", "S100", a_pdf, "A")
    rev_a = cluster.revisions()[0]
    assert cluster.pull("Company B").returncode == 0

    cluster.publish("Company A", "S100", b_pdf, "B", supersedes=rev_a["id"])
    rev_b = [r for r in cluster.revisions() if r["revision_code"] == "B"][0]
    assert cluster.pull("Company B").returncode == 0

    revisions = cluster.revisions()
    assert len(revisions) == 2, "supersede adds a revision, it never replaces one"
    assert sha256_of(a_pdf) != sha256_of(b_pdf)

    # A's own copy of revision A is still there, untouched.
    a_index = cluster.local_index("Company A")
    a_copy = cluster.roots["Company A"] / a_index[sha256_of(a_pdf)]["path"]
    assert a_copy.is_file()
    assert sha256_of(a_copy) == sha256_of(a_pdf)
    assert agent_storage.is_readonly(a_copy)

    # The container now points at B, and B has both files on disk.
    [current] = cluster.containers()
    assert current["current_revision_id"] == rev_b["id"]
    assert {sha256_of(p) for p in cluster.local_files("Company B")} == {
        sha256_of(a_pdf),
        sha256_of(b_pdf),
    }


# --------------------------------------------------------------------------- T4


def _corrupt_in_place(path: Path) -> None:
    """Same size, one flipped byte: a corruption only a hash can catch."""
    agent_storage.clear_readonly(path)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))


class _LyingPeer(http.server.BaseHTTPRequestHandler):
    """Serves bytes that do not hash to the digest it was asked for."""

    payload = b""

    def do_GET(self):  # noqa: N802 (http.server naming)
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):
        pass


def test_T4_tamper_is_detected_and_repaired(cluster, pdf):
    a_pdf = pdf("S100_A.pdf", "Site plan revision A")
    cluster.create_container("S100", "Site plan", "Company A")
    cluster.publish("Company A", "S100", a_pdf, "A")
    cluster.start_serve("Company A")
    cluster.start_serve("Company B")
    assert cluster.pull("Company B").returncode == 0

    [b_copy] = cluster.local_files("Company B")
    good_sha = sha256_of(a_pdf)
    _corrupt_in_place(b_copy)
    assert sha256_of(b_copy) != good_sha

    verify = cluster.cindy("Company B", "verify")
    assert verify.returncode != 0, "verify must fail on a corrupt copy"
    assert "hash-mismatch" in verify.stdout

    repair = cluster.cindy("Company B", "repair")
    assert repair.returncode == 0, repair.stdout + repair.stderr
    assert "repaired" in repair.stdout
    assert "Company A" in repair.stdout

    after = cluster.cindy("Company B", "verify")
    assert after.returncode == 0, after.stdout
    assert sha256_of(b_copy) == good_sha
    assert b_copy.read_bytes() == a_pdf.read_bytes()

    # A peer that serves the wrong bytes is refused by the transfer itself, and
    # leaves nothing behind for a later pull to mistake for a local copy.
    _LyingPeer.payload = b"not the plan" * 1000
    server = http.server.HTTPServer(("127.0.0.1", free_port()), _LyingPeer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        tmp_dir = cluster.work / "liar"
        url = f"http://127.0.0.1:{server.server_address[1]}/blob/{good_sha}"
        with pytest.raises(BlobHashMismatch):
            download_blob(url, good_sha, a_pdf.stat().st_size, tmp_dir)
        assert not list(tmp_dir.glob("*.part")), "a rejected download must be deleted"
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- T5


def test_T5_wrong_key_is_rejected_and_leaves_no_row(cluster, pdf):
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from hub.records import canonical_bytes, revision_payload

    cluster.create_container("S100", "Site plan", "Company A")
    a_party = cluster.parties["Company A"]["party_id"]
    container = cluster.containers()[0]

    data = pdf("forged.pdf", "forged revision").read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    payload = revision_payload(
        container_id=container["id"],
        sha256=sha,
        size=len(data),
        media_type="application/pdf",
        file_name="forged.pdf",
        revision_code="Z",
        author_party=a_party,
        created_at="2026-11-08T00:00:00.000000Z",
        supersedes_id=None,
    )
    # Signed with a key that is not Company A's: the classic forged record.
    wrong_key = Ed25519PrivateKey.generate()
    signature = base64.b64encode(wrong_key.sign(canonical_bytes(payload))).decode()

    import httpx

    response = httpx.post(
        f"{cluster.hub_url}/projects/{cluster.project['id']}/revisions",
        json=dict(payload, supersedes_id=None, signature_b64=signature),
    )
    assert response.status_code == 403, response.text
    assert cluster.revisions() == []
    assert cluster.stats()["tables"]["revision"] == 0


# --------------------------------------------------------------------------- T6


def test_T6_link_loss_fails_cleanly_then_converges(cluster, pdf):
    a_pdf = pdf("S100_A.pdf", "Site plan revision A")
    b_pdf = pdf("S100_B.pdf", "Site plan revision B - grids adjusted")
    cluster.create_container("S100", "Site plan", "Company A")

    cluster.publish("Company A", "S100", a_pdf, "A")
    rev_a = cluster.revisions()[0]
    cluster.publish("Company A", "S100", b_pdf, "B", supersedes=rev_a["id"])
    revisions_before = len(cluster.revisions())

    cluster.start_serve("Company A")
    cluster.start_serve("Company B")

    # B is not ready yet, so stop the only holder: this is the lost link. The
    # cable is not pulled; the holder's blob service is stopped (see docs/P1.md).
    cluster.stop_serve("Company A")

    offline = cluster.pull("Company B")
    assert offline.returncode != 0, "pull must report failure, not pretend to succeed"
    assert "FAILED" in offline.stderr
    assert cluster.local_files("Company B") == [], "a failed pull leaves no half-files"
    for revision in cluster.revisions():
        assert len(cluster.holders(revision["id"])) == 1  # A only: B registered nothing

    cluster.start_serve("Company A")
    back = cluster.pull("Company B")
    assert back.returncode == 0, back.stdout + back.stderr
    assert len(cluster.local_files("Company B")) == 2
    assert {sha256_of(p) for p in cluster.local_files("Company B")} == {
        sha256_of(a_pdf),
        sha256_of(b_pdf),
    }

    # Convergence: no duplicate rows, no duplicate files, nothing left to do.
    assert len(cluster.revisions()) == revisions_before
    for revision in cluster.revisions():
        parties = [h["party_name"] for h in cluster.holders(revision["id"])]
        assert sorted(parties) == ["Company A", "Company B"]
    again = cluster.pull_json("Company B")
    assert again["transferred"] == []
    assert len(cluster.local_files("Company B")) == 2


# --------------------------------------------------------------------------- T7


def test_T7_pulling_twice_duplicates_nothing(cluster, pdf):
    a_pdf = pdf("S100_A.pdf", "Site plan revision A")
    cluster.create_container("S100", "Site plan", "Company A")
    cluster.publish("Company A", "S100", a_pdf, "A")
    cluster.start_serve("Company A")
    cluster.start_serve("Company B")

    first = cluster.pull_json("Company B")
    assert len(first["transferred"]) == 1
    [revision] = cluster.revisions()
    holders_after_first = cluster.holders(revision["id"])

    files_after_first = [p.relative_to(cluster.roots["Company B"]) for p in cluster.local_files("Company B")]
    second = cluster.pull_json("Company B")

    assert second["transferred"] == []
    assert second["already_held"] == 1
    assert cluster.local_files("Company B") == [
        cluster.roots["Company B"] / p for p in files_after_first
    ]
    assert cluster.holders(revision["id"]) == holders_after_first
    assert len(cluster.revisions()) == 1

    # The other half of idempotency: publishing the same bytes again returns the
    # existing revision (and says so) instead of adding a second row.
    again = cluster.cindy("Company A", "publish", "--container", "S100",
                          "--path", str(a_pdf), "--rev", "A")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "already published" in again.stdout
    assert len(cluster.revisions()) == 1
