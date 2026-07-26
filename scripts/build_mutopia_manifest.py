#!/usr/bin/env python3
"""Discover the unmined Mutopia guitar repertoire and emit a reviewed manifest.

`build_mutopia_lilypond_corpus.py` consumes a manifest that pins, per source
file, the exact bytes and the exact conversion result.  Writing one by hand for
three hundred scores is not practical, so this produces it -- but it produces it
by running the *same* pinned converter and the *same* parser the consumer will
run, so a manifest this emits is one the consumer has effectively already
validated.  Nothing here is trusted: every digest it writes is a digest it
computed from bytes on disk.

License is read per file from the score's own header and never assumed.  The
two families are kept in separate artifacts because the repository is
Apache-2.0: ShareAlike sources may be redistributed but must stay identifiable,
and anything whose declaration this cannot parse is excluded and reported
rather than guessed.

Usage:

    uv run --frozen --with 'lxml==5.3.0' --with 'python-ly==0.9.10' \\
        python scripts/build_mutopia_manifest.py \\
        --checkout /path/to/MutopiaProject \\
        --converter /path/to/graded-guitar/scripts/m1_lilypond.py \\
        --out-dir data/score_corpus
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from lxml import etree  # type: ignore[import-untyped]

from fretsure.score_corpus import ScoreCorpusMetadata, parse_score_corpus_source

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_mutopia_lilypond_corpus import (  # noqa: E402
    MANIFEST_SCHEMA,
    _convert_source,
    _declared_license,
    _load_converter,
    _pressed_label_count,
)

# Solo guitar only.  Anything naming a second instrument is an ensemble score
# and outside the single-part importer contract.
_SOLO_INSTRUMENTS: Final = frozenset({"Guitar", "Classical Guitar"})

# Exact declaration strings, mapped to an SPDX-style identifier.  Matching is
# exact on purpose: a source whose wording this does not recognise is excluded
# and listed, never bucketed by a fuzzy guess.
_LICENSES: Final[dict[str, tuple[str, str, str]]] = {
    "Public Domain": ("public-domain", "PD", ""),
    "Creative Commons Attribution 3.0": (
        "permissive",
        "CC-BY-3.0",
        "https://creativecommons.org/licenses/by/3.0/",
    ),
    "Creative Commons Attribution-ShareAlike 2.0": (
        "share-alike",
        "CC-BY-SA-2.0",
        "https://creativecommons.org/licenses/by-sa/2.0/",
    ),
    "Creative Commons Attribution-ShareAlike 2.5": (
        "share-alike",
        "CC-BY-SA-2.5",
        "https://creativecommons.org/licenses/by-sa/2.5/",
    ),
    # The same licence, spelled a second way by one typesetter.  Still an exact
    # match, so this admits that file without loosening anything.
    "Creative Commons BY-SA 2.5": (
        "share-alike",
        "CC-BY-SA-2.5",
        "https://creativecommons.org/licenses/by-sa/2.5/",
    ),
    "Creative Commons Attribution-ShareAlike 3.0": (
        "share-alike",
        "CC-BY-SA-3.0",
        "https://creativecommons.org/licenses/by-sa/3.0/",
    ),
    "Creative Commons Attribution-ShareAlike 4.0": (
        "share-alike",
        "CC-BY-SA-4.0",
        "https://creativecommons.org/licenses/by-sa/4.0/",
    ),
}

_INSTRUMENT_RE: Final = re.compile(r'mutopiainstrument\s*=\s*"([^"]*)"')
_LICENSE_RE: Final = re.compile(r'\b(?:copyright|license)\s*=\s*"([^"]*)"')
# ``mutopiacomposer`` is an internal identifier ("HoretzkyF"); ``composer`` is
# the human-readable name.  Attribution is a licence obligation under CC-BY and
# CC-BY-SA, so the readable form is what gets recorded, with the identifier only
# as a fallback.
_COMPOSER_RE: Final = re.compile(r'^\s*composer\s*=\s*"([^"]*)"', re.MULTILINE)
_COMPOSER_ID_RE: Final = re.compile(r'mutopiacomposer\s*=\s*"([^"]*)"')
# Some engravers write the composer as a LilyPond markup block rather than a
# plain string.  Two shapes cover almost all of them: `\line {Name}` and a
# `\column {"Name" "(dates)"}`.  Anything else keeps the Mutopia identifier,
# which is honest about being an identifier rather than inventing a name.
_COMPOSER_MARKUP_RE: Final = re.compile(
    r"^\s*composer\s*=\s*\\markup(?P<body>.{0,400}?)\n\s*(?:[a-z]+\s*=|%|\})",
    re.MULTILINE | re.DOTALL,
)
_MARKUP_LINE_RE: Final = re.compile(r"\\line\s*\{([^{}]+)\}")
_MARKUP_QUOTED_RE: Final = re.compile(r'"([^"]{3,})"')


def _markup_composer(text: str) -> str:
    """A readable name out of a markup composer field, or empty if unclear."""

    block = _COMPOSER_MARKUP_RE.search(text)
    if block is None:
        return ""
    body = block.group("body")
    for pattern in (_MARKUP_LINE_RE, _MARKUP_QUOTED_RE):
        found = pattern.search(body)
        if found:
            name = found.group(1).strip()
            if name and not name.startswith("\\"):
                return name
    return ""
_TITLE_RE: Final = re.compile(r'mutopiatitle\s*=\s*"([^"]*)"')
_MAINTAINER_RE: Final = re.compile(r'maintainer\s*=\s*"([^"]*)"')
_FOOTER_RE: Final = re.compile(r'footer\s*=\s*"([^"]*)"')

RESULT_SCHEMA: Final = "fretsure-mutopia-discovery@0.1.0"
_MUTOPIA_FTP: Final = "https://www.mutopiaproject.org/ftp"


@dataclass(frozen=True, slots=True)
class Candidate:
    path: Path
    relative: str
    instrument: str
    declaration: str
    family: str
    license_id: str
    license_url: str
    composer: str
    title: str
    edition: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _header(text: str, pattern: re.Pattern[str], default: str = "") -> str:
    """A header field's value, treating a present-but-empty field as absent."""

    found = pattern.search(text)
    if found is None:
        return default
    return found.group(1).strip() or default


def discover(checkout: Path) -> tuple[list[Candidate], dict[str, list[str]]]:
    """Every solo guitar score in the checkout, classified by its own header."""

    kept: list[Candidate] = []
    skipped: dict[str, list[str]] = {}
    for path in sorted((checkout / "ftp").rglob("*.ly")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped.setdefault("unreadable", []).append(str(path))
            continue
        instrument = _header(text, _INSTRUMENT_RE)
        if not instrument:
            continue
        if instrument not in _SOLO_INSTRUMENTS:
            if "uitar" in instrument:
                skipped.setdefault("ensemble", []).append(instrument)
            continue
        declaration = _header(text, _LICENSE_RE)
        entry = _LICENSES.get(declaration)
        if entry is None:
            skipped.setdefault("unrecognised_license", []).append(
                f"{path.name}: {declaration!r}"
            )
            continue
        family, license_id, license_url = entry
        relative = str(path.relative_to(checkout / "ftp"))
        kept.append(
            Candidate(
                path=path,
                relative=relative,
                instrument=instrument,
                declaration=declaration,
                family=family,
                license_id=license_id,
                license_url=license_url,
                composer=_header(
                    text,
                    _COMPOSER_RE,
                    _markup_composer(text) or _header(text, _COMPOSER_ID_RE, "Unknown"),
                ),
                title=_header(text, _TITLE_RE, path.stem),
                edition=(
                    _header(text, _FOOTER_RE)
                    or _header(text, _MAINTAINER_RE, "Mutopia Project")
                ),
            )
        )
    return kept, skipped


def used_source_digests(corpus_dir: Path) -> set[str]:
    """Digests already pinned by a shipped manifest, so they are never re-added."""

    digests: set[str] = set()
    for manifest_path in sorted(corpus_dir.glob("*_manifest.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in payload.get("entries", []):
            digest = entry.get("source_sha256")
            if isinstance(digest, str):
                digests.add(digest)
    for source in sorted((corpus_dir / "sources").rglob("*.ly")):
        digests.add(_sha256(source.read_bytes()))
    return digests


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or "untitled"


def convert_candidate(
    candidate: Candidate,
    convert_lilypond: Any,
    seen_roots: set[str],
) -> tuple[dict[str, object] | None, str | None]:
    """Convert one source and pin exactly what came out, or say why it did not."""

    raw = candidate.path.read_bytes()
    try:
        result = _convert_source(convert_lilypond, raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - upstream converter, any failure is data
        return None, f"converter raised {type(exc).__name__}: {exc}"[:200]

    if _declared_license(result) != candidate.declaration:
        return None, "converter disagreed with the header license"

    movements: list[dict[str, object]] = []
    for movement in result.movements:
        if not movement.success or movement.musicxml_bytes is None:
            continue
        root_sha = _sha256(movement.musicxml_bytes)
        if root_sha in seen_roots:
            # Mutopia ships the same engraving under several paths; the consumer
            # rejects duplicates outright, so drop them here instead.
            continue
        slug = _slug(candidate.path.stem)
        identifier = f"mutopia-{slug}"
        if len(result.movements) > 1:
            identifier = f"{identifier}-movement-{movement.movement_index}"
        metadata = ScoreCorpusMetadata(
            id=identifier,
            work_id=f"mutopia:{slug}",
            group_id=f"mutopia:{slug}",
            title=candidate.title,
            composer=candidate.composer,
            edition=candidate.edition,
            source_url=f"{_MUTOPIA_FTP}/{candidate.relative}",
            license=candidate.license_id,
            styles=("classical",),
            tempo_bpm=float(getattr(movement, "tempo_bpm", 0) or 90.0),
        )
        try:
            example = parse_score_corpus_source(
                movement.musicxml_bytes,
                f"{candidate.path.stem}-movement-{movement.movement_index}.musicxml",
                metadata,
            )
        except Exception as exc:  # noqa: BLE001 - importer contract rejection is data
            return None, f"importer rejected movement {movement.movement_index}: {exc}"[:200]
        seen_roots.add(root_sha)
        movements.append(
            {
                "id": identifier,
                "index": movement.movement_index,
                "title": candidate.title,
                "tempo_bpm": metadata.tempo_bpm,
                "root_sha256": root_sha,
                "note_count": len(example.notes),
                "annotation_count": len(example.annotations),
                "pressed_annotation_count": _pressed_label_count(example),
            }
        )
    if not movements:
        return None, "no movement converted and parsed"
    slug = _slug(candidate.path.stem)
    return {
        "path": f"sources/mutopia_expanded/{candidate.path.name}",
        "source_url": f"{_MUTOPIA_FTP}/{candidate.relative}",
        "source_sha256": _sha256(raw),
        "source_license_declaration": candidate.declaration,
        "license": candidate.license_id,
        "license_url": candidate.license_url,
        "work_id": f"mutopia:{slug}",
        "group_id": f"mutopia:{slug}",
        "composer": candidate.composer,
        "edition": candidate.edition,
        "movements": movements,
    }, None


def write_attribution(out_dir: Path, families: dict[str, list[dict[str, object]]]) -> Path:
    """Emit the per-source attribution the licences require.

    CC-BY and CC-BY-SA oblige us to credit the licensor of each engraving, and
    240 sources is past what anyone maintains by hand.  Generating it from the
    same manifests the corpus is built from means the credit cannot silently
    drift away from what was actually shipped.
    """

    lines = [
        "# Expanded Mutopia score sources",
        "",
        "Generated by `scripts/build_mutopia_manifest.py`; do not edit by hand.",
        "",
        "Every entry below is a separate engraving with its own licence, credited",
        "to the typesetter who holds copyright in it.  The underlying compositions",
        "are in the public domain; the licence applies to the engraving.",
        "",
    ]
    for family, entries in sorted(families.items()):
        lines.append(f"## {family.replace('_', ' ').replace('-', ' ')}")
        lines.append("")
        lines.append("| work | composer | licence | engraving credit | source digest |")
        lines.append("|---|---|---|---|---|")
        for entry in sorted(entries, key=lambda item: cast(str, item["work_id"])):
            movements = cast(list[dict[str, object]], entry["movements"])
            title = str(movements[0]["title"]).replace("|", "/")[:60]
            credit = str(entry["edition"]).replace("|", "/")[:70]
            lines.append(
                f"| [{title}]({entry['source_url']}) | {entry['composer']} "
                f"| {entry['license']} | {credit} | `{str(entry['source_sha256'])[:16]}` |"
            )
        lines.append("")
    path = out_dir / "SOURCES_EXPANDED.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data/score_corpus")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    candidates, skipped = discover(args.checkout)
    already = used_source_digests(args.out_dir)
    fresh = [c for c in candidates if _sha256(c.path.read_bytes()) not in already]
    # Counted before --limit, so a sampled run still reports the true remainder.
    already_pinned = len(candidates) - len(fresh)
    if args.limit:
        fresh = fresh[: args.limit]

    converter = _load_converter(args.converter)
    convert_lilypond = cast(Any, converter.convert_lilypond)
    # libxml2 is recorded because the converted-root digest turns out to depend
    # on it: the same converter under a different libxml2 build emits different
    # XML bytes for identical music.  Pinning only the Python packages left that
    # dependency invisible, which is why the shipped artifact does not rebuild
    # here.  The note stream is unaffected; the counts below are what bind.
    conversion = {
        "upstream": "HugoFara/graded-guitar",
        "script": "scripts/m1_lilypond.py",
        "script_sha256": _sha256(args.converter.read_bytes()),
        "python_ly": "0.9.10",
        "lxml": "5.3.0",
        "libxml2": ".".join(str(part) for part in etree.LIBXML_VERSION),
    }

    seen_roots: set[str] = set()
    families: dict[str, list[dict[str, object]]] = {}
    failures: list[dict[str, str]] = []
    for index, candidate in enumerate(fresh, start=1):
        entry, reason = convert_candidate(candidate, convert_lilypond, seen_roots)
        if entry is None:
            failures.append({"path": candidate.relative, "reason": reason or "unknown"})
        else:
            families.setdefault(candidate.family, []).append(entry)
        if index % 25 == 0:
            print(f"  {index}/{len(fresh)}", file=sys.stderr, flush=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    sources_dir = args.out_dir / "sources/mutopia_expanded"
    sources_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for family, entries in sorted(families.items()):
        for entry in entries:
            name = Path(cast(str, entry["path"])).name
            match = next(c for c in fresh if c.path.name == name)
            (sources_dir / name).write_bytes(match.path.read_bytes())
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "conversion": conversion,
            "entries": sorted(entries, key=lambda item: cast(str, item["work_id"])),
        }
        out = args.out_dir / f"mutopia_expanded_{family.replace('-', '_')}_manifest.json"
        out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        try:
            label = str(out.relative_to(ROOT))
        except ValueError:
            label = str(out)
        written[label] = len(entries)

    attribution = write_attribution(args.out_dir, families)
    report = {
        "schema": RESULT_SCHEMA,
        "attribution": str(attribution.name),
        "checkout_solo_guitar": len(candidates),
        "already_pinned": already_pinned,
        "attempted": len(fresh),
        "converted": sum(len(v) for v in families.values()),
        "movements": sum(
            len(cast(list[object], e["movements"])) for v in families.values() for e in v
        ),
        "failed": len(failures),
        "manifests": written,
        "skipped_during_discovery": {k: len(v) for k, v in sorted(skipped.items())},
        "unrecognised_licenses": sorted(set(skipped.get("unrecognised_license", []))),
        "failure_reasons": dict(
            Counter(f["reason"].split(":")[0] for f in failures).most_common()
        ),
        "failures": failures,
    }
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
