"""Loopback-only command-line launcher for the optional HTTP service."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from fretsure.api.app import create_app

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _port(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise argparse.ArgumentTypeError("port must be a decimal integer")
    parsed = int(value)
    if not 1 <= parsed <= 65_535:
        raise argparse.ArgumentTypeError("port must be in 1..65535")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fretsure-serve",
        description=(
            "Serve the local Fretsure Web/API interface on IPv4 loopback. "
            "This is not a public multi-tenant deployment server."
        ),
    )
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT)
    parser.add_argument(
        "--allow-proxy",
        action="store_true",
        help="allow requests to use the fixed local GPT-5.6 Sol proxy engine",
    )
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info"),
        default="info",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    import uvicorn

    app = create_app(allow_proxy=args.allow_proxy)
    uvicorn.run(
        app,
        host=LOOPBACK_HOST,
        port=args.port,
        log_level=args.log_level,
        proxy_headers=False,
        server_header=False,
        date_header=False,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["DEFAULT_PORT", "LOOPBACK_HOST", "main"]
