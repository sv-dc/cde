"""The `cindy` command line, run as `python -m agent` so nothing needs installing.

Exit codes: 0 success, 1 the work did not converge (mismatch, peer down),
2 the request or the hub rejected us. Tests and the demo rely on those numbers.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent import storage
from agent.client import HubClient, HubError, HubUnreachable
from agent.signing import sign_payload
from agent.state import State
from agent.transfer import TransferError, download_blob

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


# ------------------------------------------------------------------------ helpers


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def human_bytes(n: int | float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def print_table(headers: list[str], rows: list[list]) -> None:
    cols = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], len(str(row[i])))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(cols)))
    for row in rows:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(cols)))


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return EXIT_USAGE


def require_state(root: str) -> State:
    return State.load(root)


def find_container(client: HubClient, project_id: str, number: str) -> dict:
    for container in client.list_containers(project_id):
        if container["number"] == number:
            return container
    raise LookupError(
        f"container {number!r} does not exist in this project; "
        "create it with POST /projects/<pid>/containers"
    )


def _holder_candidates(revision: dict, party_id: str) -> list[dict]:
    """Holders we can actually reach, author first.

    A holder row without an announced address is a party that registered its copy
    but is not serving right now; skip it rather than fail on it.
    """
    holders = [
        h
        for h in revision.get("holders", [])
        if h["party_id"] != party_id and h.get("address")
    ]
    holders.sort(key=lambda h: (h["party_id"] != revision["author_party"], h["party_id"]))
    return holders


def _fetch_revision(
    state: State, revision: dict, holder: dict, dest: Path
) -> tuple[Path, float]:
    """Download, verify, place. Returns (path, milliseconds spent transferring)."""
    started = time.perf_counter()
    tmp = download_blob(
        f"{holder['address'].rstrip('/')}/blob/{revision['sha256']}",
        revision["sha256"],
        revision["size"],
        dest.parent,
    )
    try:
        storage.place_file(tmp, dest, revision["sha256"])
    finally:
        tmp.unlink(missing_ok=True)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return dest, elapsed_ms


# ----------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    state_path = State.path_for(root)
    if state_path.exists() and not args.force:
        return fail(f"{state_path} already exists (use --force to replace the key)")

    pieces = args.invite.split("~")
    if len(pieces) != 3:
        return fail("invite token is malformed; copy it from the hub response")
    project_id = pieces[0]

    key = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(
        key.public_key().public_bytes_raw()
    ).decode("ascii")
    private_b64 = base64.b64encode(key.private_bytes_raw()).decode("ascii")

    hub_url = args.hub.rstrip("/")
    with HubClient(hub_url) as client:
        claim = client.claim_party(project_id, args.invite, public_b64)

        state = State(
            root=str(root),
            project_id=project_id,
            party_id=claim["id"],
            party_name=claim["name"],
            hub_url=hub_url,
            host=args.host,
            blob_port=args.port,
            private_key_b64=private_b64,
            public_key_b64=public_b64,
            created_at=now_iso(),
        )
        state.save()
        # Announce where the bytes can be fetched from. This is the only address
        # the hub knows; it never learns the bytes.
        client.announce_address(project_id, state.party_id, args.host, args.port)

    print(f"party   {state.party_name} ({state.party_id})")
    print(f"project {state.project_id}")
    print(f"hub     {state.hub_url}")
    print(f"blob    {state.address}")
    print(f"state   {state_path}")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from agent.serve import create_app

    state = require_state(args.root)
    app = create_app(state.root, state)
    # 0.0.0.0 when the party announced a LAN address: binding 127.0.0.1 would
    # make the announced address unreachable from the other machine.
    bind = "0.0.0.0" if state.host not in ("127.0.0.1", "localhost") else state.host
    uvicorn.run(
        app,
        host=bind,
        port=args.port or state.blob_port,
        log_level=args.log_level,
    )
    return EXIT_OK


def cmd_publish(args: argparse.Namespace) -> int:
    state = require_state(args.root)
    source = Path(args.path).expanduser()
    if not source.is_file():
        return fail(f"no such file: {source}")

    sha256 = storage.sha256_file(source)
    size = source.stat().st_size
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    created_at = now_iso()

    with HubClient(state.hub_url) as client:
        try:
            container = find_container(client, state.project_id, args.container)
        except LookupError as exc:
            return fail(str(exc))

        payload = {
            "container_id": container["id"],
            "sha256": sha256,
            "size": size,
            "media_type": media_type,
            "file_name": source.name,
            "revision_code": args.rev,
            "author_party": state.party_id,
            "created_at": created_at,
            "supersedes_id": args.supersedes or "",
        }
        body = dict(payload, supersedes_id=args.supersedes, signature_b64=sign_payload(state, payload))
        try:
            revision = client.post_revision(state.project_id, body)
        except HubError as exc:
            return fail(str(exc))

        # Only now does a local copy exist: a rejected publish leaves no file.
        dest = storage.store_local_copy(
            state,
            container_number=container["number"],
            revision_code=args.rev,
            file_name=source.name,
            sha256=sha256,
            source=source,
        )
        index = storage.LocalIndex.load(state.root)
        index.add(
            sha256=sha256,
            size=size,
            path=dest,
            revision_id=revision["id"],
            revision_code=args.rev,
            file_name=source.name,
            container_id=container["id"],
            container_number=container["number"],
        )
        index.save()
        client.register_holder(
            state.project_id, revision["id"], state.party_id, str(dest)
        )

    result = {
        "revision_id": revision["id"],
        "container": container["number"],
        "revision_code": args.rev,
        "sha256": sha256,
        "size": size,
        "path": str(dest),
        "created": revision.get("created", True),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        verb = "published" if result["created"] else "already published (same bytes)"
        print(f"{verb} {args.rev} of {container['number']}: {sha256[:12]} {human_bytes(size)}")
        print(f"local copy {dest} (read-only)")
    return EXIT_OK


def cmd_pull(args: argparse.Namespace) -> int:
    state = require_state(args.root)
    index = storage.LocalIndex.load(state.root)
    transferred: list[dict] = []
    failed: list[dict] = []

    with HubClient(state.hub_url) as client:
        revisions = client.list_revisions(state.project_id)
        missing = [r for r in revisions if not index.holds_valid(r["sha256"])]

        for revision in missing:
            candidates = _holder_candidates(revision, state.party_id)
            if not candidates:
                failed.append(
                    {
                        "revision_id": revision["id"],
                        "container": revision["container_number"],
                        "revision_code": revision["revision_code"],
                        "reason": "no other holder has announced a reachable address",
                    }
                )
                continue

            dest = storage.revision_path(
                state,
                revision["container_number"],
                revision["revision_code"],
                revision["file_name"],
                revision["sha256"],
            )
            errors = []
            for holder in candidates:
                try:
                    _, elapsed_ms = _fetch_revision(state, revision, holder, dest)
                except TransferError as exc:
                    # Includes a hash mismatch: a lying peer is just another
                    # failed candidate, and its bytes were never placed.
                    errors.append(str(exc))
                    continue
                client.register_holder(
                    state.project_id, revision["id"], state.party_id, str(dest)
                )
                index.add(
                    sha256=revision["sha256"],
                    size=revision["size"],
                    path=dest,
                    revision_id=revision["id"],
                    revision_code=revision["revision_code"],
                    file_name=revision["file_name"],
                    container_id=revision["container_id"],
                    container_number=revision["container_number"],
                )
                index.save()
                transferred.append(
                    {
                        "revision_id": revision["id"],
                        "container": revision["container_number"],
                        "revision_code": revision["revision_code"],
                        "sha256": revision["sha256"],
                        "size": revision["size"],
                        "holder_party": holder["party_name"],
                        "holder_address": holder["address"],
                        "path": str(dest),
                        "transfer_ms": round(elapsed_ms, 1),
                    }
                )
                break
            else:
                failed.append(
                    {
                        "revision_id": revision["id"],
                        "container": revision["container_number"],
                        "revision_code": revision["revision_code"],
                        "reason": "; ".join(errors) or "no holder could serve the blob",
                    }
                )

    if args.json:
        print(
            json.dumps(
                {
                    "transferred": transferred,
                    "failed": failed,
                    "already_held": len(revisions) - len(missing),
                },
                indent=2,
            )
        )
    else:
        if transferred:
            print_table(
                ["container", "rev", "sha256", "size", "from", "ms"],
                [
                    [
                        t["container"],
                        t["revision_code"],
                        t["sha256"][:12],
                        human_bytes(t["size"]),
                        t["holder_party"],
                        t["transfer_ms"],
                    ]
                    for t in transferred
                ],
            )
        else:
            print("nothing to pull: every revision is already held and verified")
        for problem in failed:
            print(
                f"FAILED {problem['container']} rev {problem['revision_code']}: "
                f"{problem['reason']}",
                file=sys.stderr,
            )
    return EXIT_FAIL if failed else EXIT_OK


def _verify_entries(state: State, index: storage.LocalIndex) -> list[dict]:
    results = []
    for sha256, entry in sorted(
        index.blobs.items(), key=lambda kv: (kv[1]["container_number"], kv[1]["revision_code"])
    ):
        path = index.abs_path(entry)
        if not path.is_file():
            status = "missing"
            actual = ""
        elif path.stat().st_size != entry["size"]:
            status = "size-mismatch"
            actual = storage.sha256_file(path)
        else:
            actual = storage.sha256_file(path)
            status = "ok" if actual == sha256 else "hash-mismatch"
        results.append(
            {
                "container": entry["container_number"],
                "revision_code": entry["revision_code"],
                "sha256": sha256,
                "path": str(path),
                "status": status,
                "actual": actual,
            }
        )
    return results


def cmd_verify(args: argparse.Namespace) -> int:
    state = require_state(args.root)
    index = storage.LocalIndex.load(state.root)
    results = _verify_entries(state, index)
    bad = [r for r in results if r["status"] != "ok"]

    if args.json:
        print(json.dumps({"checked": len(results), "bad": bad, "results": results}, indent=2))
    else:
        if results:
            print_table(
                ["container", "rev", "sha256", "size", "status"],
                [
                    [
                        r["container"],
                        r["revision_code"],
                        r["sha256"][:12],
                        human_bytes(index.blobs[r["sha256"]]["size"]),
                        r["status"],
                    ]
                    for r in results
                ],
            )
        else:
            print("no local copies")
        print(f"{len(results) - len(bad)} ok, {len(bad)} bad")
    return EXIT_FAIL if bad else EXIT_OK


def cmd_repair(args: argparse.Namespace) -> int:
    state = require_state(args.root)
    index = storage.LocalIndex.load(state.root)
    results = _verify_entries(state, index)
    bad = [r for r in results if r["status"] != "ok"]
    if not bad:
        print("nothing to repair: every local copy matches its hash")
        return EXIT_OK

    repaired = 0
    with HubClient(state.hub_url) as client:
        revisions = {r["sha256"]: r for r in client.list_revisions(state.project_id)}
        for item in bad:
            revision = revisions.get(item["sha256"])
            if revision is None:
                print(
                    f"cannot repair {item['container']} rev {item['revision_code']}: "
                    "the hub has no such revision",
                    file=sys.stderr,
                )
                continue
            candidates = _holder_candidates(revision, state.party_id)
            dest = Path(item["path"])
            for holder in candidates:
                try:
                    _, elapsed_ms = _fetch_revision(state, revision, holder, dest)
                except TransferError as exc:
                    print(f"  {holder['party_name']}: {exc}", file=sys.stderr)
                    continue
                client.register_holder(
                    state.project_id, revision["id"], state.party_id, str(dest)
                )
                print(
                    f"repaired {item['container']} rev {item['revision_code']} "
                    f"from {holder['party_name']} in {elapsed_ms:.0f} ms"
                )
                repaired += 1
                break
            else:
                print(
                    f"could not repair {item['container']} rev {item['revision_code']}: "
                    "no holder served matching bytes",
                    file=sys.stderr,
                )

    return EXIT_OK if repaired == len(bad) else EXIT_FAIL


def cmd_status(args: argparse.Namespace) -> int:
    state = require_state(args.root)
    index = storage.LocalIndex.load(state.root)
    with HubClient(state.hub_url) as client:
        containers = client.list_containers(state.project_id)
        revisions = client.list_revisions(state.project_id)

    by_container: dict[str, list[dict]] = {}
    for revision in revisions:
        by_container.setdefault(revision["container_id"], []).append(revision)

    rows = []
    for container in sorted(containers, key=lambda c: c["number"]):
        revs = by_container.get(container["id"], [])
        current = next(
            (r for r in revs if r["id"] == container["current_revision_id"]), None
        )
        holders = set()
        for revision in revs:
            holders.update(h["party_name"] for h in revision["holders"])
        mine = [r for r in revs if index.holds_valid(r["sha256"])]
        local = f"{len(mine)}/{len(revs)}" if revs else "-"
        rows.append(
            [
                container["number"],
                container["title"],
                current["revision_code"] if current else "-",
                len(revs),
                ", ".join(sorted(holders)) or "-",
                local,
            ]
        )

    if args.json:
        print(
            json.dumps(
                {"party": state.party_name, "containers": containers, "revisions": revisions},
                indent=2,
            )
        )
        return EXIT_OK

    print(f"party {state.party_name} ({state.party_id})  project {state.project_id}")
    print(f"root  {state.root}   hub {state.hub_url}   blob {state.address}")
    if rows:
        print_table(["container", "title", "current", "revs", "holders", "local"], rows)
    else:
        print("no containers yet")
    return EXIT_OK


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cindy", description="Cindy agent (P1): local copies, signed record."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create this device's key and state")
    p_init.add_argument("--root", required=True, help="project folder on this machine")
    p_init.add_argument("--hub", required=True, help="hub base URL, e.g. http://127.0.0.1:8080")
    p_init.add_argument("--invite", required=True, help="invite token returned by the hub")
    p_init.add_argument("--port", type=int, required=True, help="port for this agent's blob service")
    p_init.add_argument(
        "--host",
        default="127.0.0.1",
        help="address other agents use to reach this one (LAN IP for two machines)",
    )
    p_init.add_argument("--force", action="store_true", help="replace an existing key")
    p_init.set_defaults(func=cmd_init)

    p_serve = sub.add_parser("serve", help="serve this party's held blobs over HTTP")
    p_serve.add_argument("--root", required=True)
    p_serve.add_argument("--port", type=int, default=None, help="override the port from state")
    p_serve.add_argument("--log-level", default="warning")
    p_serve.set_defaults(func=cmd_serve)

    p_pub = sub.add_parser("publish", help="publish a file as a revision")
    p_pub.add_argument("--root", required=True)
    p_pub.add_argument("--container", required=True, help="container number")
    p_pub.add_argument("--path", required=True, help="file to publish")
    p_pub.add_argument("--rev", required=True, help="revision code, e.g. A or P01")
    p_pub.add_argument("--supersedes", default=None, help="revision id this replaces")
    p_pub.add_argument("--json", action="store_true")
    p_pub.set_defaults(func=cmd_publish)

    p_pull = sub.add_parser("pull", help="fetch and verify revisions not held here")
    p_pull.add_argument("--root", required=True)
    p_pull.add_argument("--json", action="store_true")
    p_pull.set_defaults(func=cmd_pull)

    p_ver = sub.add_parser("verify", help="re-hash local copies")
    p_ver.add_argument("--root", required=True)
    p_ver.add_argument("--json", action="store_true")
    p_ver.set_defaults(func=cmd_verify)

    p_rep = sub.add_parser("repair", help="re-fetch copies that fail verification")
    p_rep.add_argument("--root", required=True)
    p_rep.set_defaults(func=cmd_repair)

    p_st = sub.add_parser("status", help="containers, revisions, holders, local presence")
    p_st.add_argument("--root", required=True)
    p_st.add_argument("--json", action="store_true")
    p_st.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        return fail(str(exc))
    except HubUnreachable as exc:
        return fail(str(exc))
    except HubError as exc:
        return fail(str(exc))
    except KeyboardInterrupt:
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
