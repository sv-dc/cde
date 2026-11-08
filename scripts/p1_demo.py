"""One command, the whole P1 story, on this machine.

    python scripts/p1_demo.py

Starts a hub, starts two agents with two separate roots, publishes revision A and
then revision B of one container, pulls them from A to B over HTTP, and prints
what moved and what the hub kept. Everything is a real process; the only thing
simulated is "two PCs", which is two directories and two ports here.

--keep leaves the work directory in place so you can look at the files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from pdf_fixture import write_pdf  # noqa: E402


# ------------------------------------------------------------------- utilities


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{url} never became healthy")


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def human(n: float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(cmd: list[str], cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def cindy(root: Path, *args: str) -> subprocess.CompletedProcess:
    return run([sys.executable, "-m", "agent", *args, "--root", str(root)])


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def rmtree(path: Path) -> None:
    """Windows keeps read-only files alive; clear the bit before deleting."""
    def on_error(func, target, _exc):
        try:
            Path(target).chmod(stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=on_error)


def table(headers: list[str], rows: list[list]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(len(headers)):
            widths[i] = max(widths[i], len(str(row[i])))
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    out.append("  ".join("-" * w for w in widths))
    for row in rows:
        out.append("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(out)


# ------------------------------------------------------------------------ story


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cindy P1 end-to-end demo")
    parser.add_argument("--keep", action="store_true", help="keep the work directory")
    parser.add_argument("--workdir", default=None, help="use this directory instead of a temp one")
    args = parser.parse_args(argv)

    work = Path(args.workdir) if args.workdir else Path(
        tempfile.mkdtemp(prefix="cindy-p1-demo-")
    )
    work.mkdir(parents=True, exist_ok=True)
    hub_dir = work / "hub"
    root_a = work / "agent-a"
    root_b = work / "agent-b"
    src = work / "source"
    procs: list[subprocess.Popen] = []

    hub_port = free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    port_a, port_b = free_port(), free_port()

    try:
        print(f"work directory  {work}")
        print(f"hub             {hub_url}  (sqlite under {hub_dir})")
        print(f"agent A root    {root_a}")
        print(f"agent B root    {root_b}")

        hub = subprocess.Popen(
            [sys.executable, "-m", "hub", "--db", str(hub_dir / "hub.db"),
             "--port", str(hub_port)],
            cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(hub)
        wait_http(f"{hub_url}/health")

        project = httpx.post(hub_url + "/projects", json={"name": "Riverside School"}).json()
        print(f"project         {project['name']} ({project['id']})")

        invites = {}
        for name in ("Alpha Design", "Beta Construction"):
            invites[name] = httpx.post(
                f"{hub_url}/projects/{project['id']}/parties", json={"name": name}
            ).json()
            print(f"invited         {name} ({invites[name]['id']})")

        for root, name, port in (
            (root_a, "Alpha Design", port_a),
            (root_b, "Beta Construction", port_b),
        ):
            result = cindy(root, "init", "--hub", hub_url,
                           "--invite", invites[name]["invite_token"], "--port", str(port))
            if result.returncode != 0:
                print(result.stdout + result.stderr, file=sys.stderr)
                return 1

        state_a = json.loads((root_a / ".cindy" / "state.json").read_text())
        state_b = json.loads((root_b / ".cindy" / "state.json").read_text())

        container = httpx.post(
            f"{hub_url}/projects/{project['id']}/containers",
            json={
                "number": "D-101",
                "title": "Ground floor plan",
                "type": "drawing",
                "originator_party": state_a["party_id"],
            },
        ).json()
        print(f"container       {container['number']} {container['title']}")

        # Two real PDFs, each bigger than 1 MB, differing by their content.
        pdf_a = write_pdf(src / "D-101_A.pdf", "Ground floor plan - revision A")
        pdf_b = write_pdf(src / "D-101_B.pdf", "Ground floor plan - revision B, grids adjusted")

        published_a = cindy(root_a, "publish", "--container", "D-101",
                            "--path", str(pdf_a), "--rev", "A", "--json")
        if published_a.returncode != 0:
            print(published_a.stdout + published_a.stderr, file=sys.stderr)
            return 1
        rev_a = json.loads(published_a.stdout)
        print(f"published       A  {rev_a['sha256'][:12]}  {human(rev_a['size'])}")

        published_b = cindy(root_a, "publish", "--container", "D-101",
                            "--path", str(pdf_b), "--rev", "B",
                            "--supersedes", rev_a["revision_id"], "--json")
        if published_b.returncode != 0:
            print(published_b.stdout + published_b.stderr, file=sys.stderr)
            return 1
        rev_b = json.loads(published_b.stdout)
        print(f"published       B  {rev_b['sha256'][:12]}  {human(rev_b['size'])}  "
              f"supersedes A")

        for root, state in ((root_a, state_a), (root_b, state_b)):
            serve = subprocess.Popen(
                [sys.executable, "-m", "agent", "serve", "--root", str(root)],
                cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            procs.append(serve)
            wait_http(f"http://127.0.0.1:{state['blob_port']}/health")

        # ---- the transfer, measured on both ends
        before = dir_bytes(hub_dir)
        pulled = cindy(root_b, "pull", "--json")
        after = dir_bytes(hub_dir)
        if pulled.returncode != 0:
            print(pulled.stdout + pulled.stderr, file=sys.stderr)
            return 1
        transfer = json.loads(pulled.stdout)

        verify_b = cindy(root_b, "verify")
        verify_a = cindy(root_a, "verify")
        verified = verify_b.returncode == 0 and verify_a.returncode == 0

        # Every local file B now holds, matched back to the record by hash.
        record = {r["sha256"]: r for r in httpx.get(
            f"{hub_url}/projects/{project['id']}/revisions").json()}
        rows = []
        for item in transfer["transferred"]:
            rows.append([
                item["revision_code"],
                item["container"],
                item["sha256"][:12],
                human(item["size"]),
                item["holder_party"],
                item["transfer_ms"],
                before,
                after,
                "ok" if verified else "FAILED",
            ])

        print()
        print(table(
            ["revision", "container", "sha256", "size", "holder", "transfer ms",
             "hub bytes before", "hub bytes after", "verify"],
            rows,
        ))

        blob_total = sum(item["size"] for item in transfer["transferred"])
        print()
        print(f"hub data dir    {before} -> {after} bytes (growth {after - before} bytes)")
        print(f"blob bytes      {blob_total} bytes transferred directly, agent to agent")
        print(f"hub total       {after} bytes on disk for the whole project record "
              f"({after / max(1, blob_total) * 100:.1f}% of what moved, none of it file content)")
        print(f"hub contents    {', '.join(sorted(p.name for p in hub_dir.iterdir()))}")

        # Prove the claim rather than assert it: no file under the hub directory
        # is a published blob, by content.
        blob_hashes = {item["sha256"] for item in transfer["transferred"]}
        leaked = []
        for path in hub_dir.rglob("*"):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() in blob_hashes or b"%PDF-" in data:
                leaked.append(path.name)
        print(f"hub scan        " + (
            "no published file content found in the hub directory"
            if not leaked else f"LEAKED {leaked}"
        ))

        # The record and the two independent roots agree, checked from the files.
        checks = []
        for item in transfer["transferred"]:
            revision = record[item["sha256"]]
            checks.append(sha256_of(Path(item["path"])) == revision["sha256"])
        print(f"verification    {sum(checks)}/{len(checks)} pulled files match the signed record")

        current = httpx.get(f"{hub_url}/projects/{project['id']}/containers").json()[0]
        print(f"current         {container['number']} revision "
              f"{'B' if current['current_revision_id'] == rev_b['revision_id'] else '?'}")
        print(f"revisions kept  {len(record)} (A {rev_a['sha256'][:12]}, B {rev_b['sha256'][:12]})")
        print(f"holders         " + ", ".join(
            sorted({h['party_name'] for r in record.values() for h in r['holders']})))

        print()
        print("B's copies:")
        for path in sorted((root_b / "_cindy").rglob("*")):
            if path.is_file():
                print(f"  {human(path.stat().st_size):>8}  {path}")
        return 0
    finally:
        for proc in procs:
            stop(proc)
        if args.keep:
            print(f"\nkept {work}")
        elif not args.workdir:
            rmtree(work)


if __name__ == "__main__":
    sys.exit(main())
