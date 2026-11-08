"""Shared fixtures: real hub process, two real agents, two separate roots.

Nothing here mocks the network. Every byte in these tests crosses HTTP between two
processes, because the point of P1 is that this works when it is not simulated.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "Scripts" / "python.exe"
SCRIPTS = REPO / "scripts"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pdf_fixture import write_pdf  # noqa: E402  (needs the path above)


# ------------------------------------------------------------------- primitives


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError as exc:
            last = exc
        time.sleep(0.1)
    raise RuntimeError(f"{url} did not become healthy: {last}")


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def stop_process(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_cindy(root: Path, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "agent", *args, "--root", str(root)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


# ---------------------------------------------------------------------- cluster


class Cluster:
    """One hub plus two independent agents, each with its own root directory."""

    def __init__(self, work: Path):
        self.work = work
        self.hub_port = free_port()
        self.hub_url = f"http://127.0.0.1:{self.hub_port}"
        self.procs: dict[str, subprocess.Popen] = {}
        self.roots: dict[str, Path] = {}
        self.parties: dict[str, dict] = {}
        self._logs: list[Path] = []

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> "Cluster":
        hub_log = self.work / "hub.log"
        hub_out = open(hub_log, "w", encoding="utf-8")
        self._logs.append(hub_log)
        self.procs["hub"] = subprocess.Popen(
            [str(PY), "-m", "hub", "--db", str(self.work / "hub" / "hub.db"),
             "--port", str(self.hub_port)],
            cwd=str(REPO),
            stdout=hub_out,
            stderr=subprocess.STDOUT,
        )
        wait_http(f"{self.hub_url}/health")
        self.project = httpx.post(
            f"{self.hub_url}/projects", json={"name": "P1 acceptance"}
        ).json()
        return self

    def add_party(self, name: str) -> dict:
        invite = httpx.post(
            f"{self.hub_url}/projects/{self.project['id']}/parties",
            json={"name": name},
        ).json()
        root = self.work / name.split()[-1].lower()
        port = free_port()
        result = subprocess.run(
            [str(PY), "-m", "agent", "init", "--root", str(root), "--hub", self.hub_url,
             "--invite", invite["invite_token"], "--port", str(port)],
            cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        self.roots[name] = root
        self.parties[name] = json.loads((root / ".cindy" / "state.json").read_text())
        return self.parties[name]

    def start_serve(self, name: str) -> None:
        assert "serve:" + name not in self.procs, f"{name} blob service already running"
        log = self.work / f"serve-{name.split()[-1].lower()}.log"
        self._logs.append(log)
        out = open(log, "w", encoding="utf-8")
        self.procs["serve:" + name] = subprocess.Popen(
            [str(PY), "-m", "agent", "serve", "--root", str(self.roots[name])],
            cwd=str(REPO), stdout=out, stderr=subprocess.STDOUT,
        )
        wait_http(f"http://127.0.0.1:{self.parties[name]['blob_port']}/health")

    def stop_serve(self, name: str) -> None:
        proc = self.procs.pop("serve:" + name, None)
        if proc is not None:
            stop_process(proc)

    def close(self) -> None:
        for key in [k for k in self.procs if k.startswith("serve:")]:
            self.stop_serve(key.split(":", 1)[1])
        if "hub" in self.procs:
            stop_process(self.procs.pop("hub"))

    # ------------------------------------------------------------------ actions

    def create_container(self, number: str, title: str, originator: str) -> dict:
        return httpx.post(
            f"{self.hub_url}/projects/{self.project['id']}/containers",
            json={
                "number": number,
                "title": title,
                "type": "drawing",
                "originator_party": self.parties[originator]["party_id"],
            },
        ).json()

    def cindy(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return run_cindy(self.roots[name], *args)

    def publish(self, name: str, container: str, path: Path, rev: str,
                supersedes: str | None = None) -> subprocess.CompletedProcess:
        extra = ["--supersedes", supersedes] if supersedes else []
        result = self.cindy(name, "publish", "--container", container,
                            "--path", str(path), "--rev", rev, *extra)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    def pull(self, name: str) -> subprocess.CompletedProcess:
        return self.cindy(name, "pull")

    def pull_json(self, name: str) -> dict:
        result = self.cindy(name, "pull", "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    # ------------------------------------------------------------------ reading

    def revisions(self) -> list[dict]:
        return httpx.get(
            f"{self.hub_url}/projects/{self.project['id']}/revisions"
        ).json()

    def containers(self) -> list[dict]:
        return httpx.get(
            f"{self.hub_url}/projects/{self.project['id']}/containers"
        ).json()

    def holders(self, revision_id: str) -> list[dict]:
        return httpx.get(
            f"{self.hub_url}/projects/{self.project['id']}/revisions/"
            f"{revision_id}/holders"
        ).json()

    def stats(self) -> dict:
        return httpx.get(f"{self.hub_url}/stats").json()

    def hub_data_bytes(self) -> int:
        return dir_bytes(self.work / "hub")

    def local_files(self, name: str) -> list[Path]:
        root = self.roots[name] / "_cindy"
        return sorted(p for p in root.rglob("*") if p.is_file())

    def local_index(self, name: str) -> dict:
        path = self.roots[name] / ".cindy" / "index.json"
        return json.loads(path.read_text())["blobs"] if path.exists() else {}


@pytest.fixture
def cluster(tmp_path):
    c = Cluster(tmp_path)
    c.start()
    c.add_party("Company A")
    c.add_party("Company B")
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def pdf(tmp_path):
    """A one-page-per-revision PDF bigger than 1 MB, so T2 measures something."""
    def _make(name: str, label: str) -> Path:
        return write_pdf(tmp_path / name, label, padding_bytes=1_300_000)
    return _make
