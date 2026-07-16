#!/usr/bin/env python3
"""Fail when a repository Markdown link targets a missing local file."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "node_modules"}
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
REFERENCE_LINK = re.compile(r"^\s*\[([^\]]+)\]:\s*(<[^>]+>|\S+)", re.MULTILINE)


def _markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts)
    )


def _target_path(source: Path, token: str) -> Path | None:
    token = token.removeprefix("<").removesuffix(">")
    if not token or token.startswith("#"):
        return None
    parsed = urlsplit(token)
    if parsed.scheme or parsed.netloc:
        return None
    local = unquote(parsed.path)
    if not local:
        return None
    return (ROOT / local.lstrip("/")) if local.startswith("/") else (source.parent / local)


def main() -> int:
    failures: list[str] = []
    for source in _markdown_files():
        text = source.read_text(encoding="utf-8")
        tokens = [match.group(1) for match in INLINE_LINK.finditer(text)]
        tokens.extend(
            match.group(2)
            for match in REFERENCE_LINK.finditer(text)
            if not match.group(1).startswith("^")
        )
        for token in tokens:
            target = _target_path(source, token)
            if target is not None and not target.exists():
                failures.append(
                    f"{source.relative_to(ROOT)}: missing local link {token!r}"
                )
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Markdown links OK ({len(_markdown_files())} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
