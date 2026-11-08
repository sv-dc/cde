"""Run the hub: python -m hub --db PATH --port N."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from hub.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hub", description="Cindy hub")
    parser.add_argument("--db", required=True, help="path to the SQLite file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args(argv)

    app = create_app(Path(args.db))
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
