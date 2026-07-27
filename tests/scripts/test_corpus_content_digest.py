"""What the corpus content digest must and must not notice.

The manifest used to bind a rebuild to `root_sha256`, the digest of the
intermediate MusicXML the converter emits.  That digest depends on the libxml2
build lxml links against, so the shipped corpus stopped rebuilding on a machine
whose XML library differed -- even though the music was identical note for note.
A check that fires on the wrong thing is worse than no check, because people
learn to route around it.

These tests pin the replacement's two halves: it ignores how the XML was
serialised, and it still notices any change to the music.
"""

from __future__ import annotations

import importlib.util
import sys
from fractions import Fraction
from pathlib import Path
from types import ModuleType

import pytest

from fretsure.ir import Note
from fretsure.score_corpus import FingeringAnnotation, ScoreCorpusExample

ROOT = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    name = "build_mutopia_lilypond_corpus"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "scripts/build_mutopia_lilypond_corpus.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


content_sha256 = _load_script().content_sha256


def _example(
    *,
    notes: tuple[Note, ...] | None = None,
    annotations: tuple[FingeringAnnotation, ...] | None = None,
    tuning: tuple[int, ...] = (40, 45, 50, 55, 59, 64),
    capo: int = 0,
    root_sha256: str = "a" * 64,
    title: str = "Study",
    tempo_bpm: float = 90.0,
) -> ScoreCorpusExample:
    return ScoreCorpusExample(
        id="example-1",
        work_id="w",
        group_id="g",
        title=title,
        composer="Composer",
        edition="Edition",
        source_url="https://example.invalid/score.ly",
        license="PD",
        source_filename="score.musicxml",
        source_sha256="b" * 64,
        root_sha256=root_sha256,
        root_member=None,
        tempo_bpm=tempo_bpm,
        time_signature=(4, 4),
        tuning=tuning,
        capo=capo,
        styles=("classical",),
        grade=None,
        notes=notes
        if notes is not None
        else (
            Note(Fraction(0), Fraction(1), 64, "melody"),
            Note(Fraction(1), Fraction(1), 62, "melody"),
        ),
        annotations=annotations
        if annotations is not None
        else (FingeringAnnotation(Fraction(0), 64, (1,), 0, 2),),
    )


def test_the_build_fingerprint_does_not_change_the_content_digest() -> None:
    """The whole point: a different XML library build must not fail a rebuild."""

    assert content_sha256(_example(root_sha256="a" * 64)) == content_sha256(
        _example(root_sha256="c" * 64)
    )


def test_credits_do_not_change_the_content_digest() -> None:
    """Titles and credits come from the manifest, which already pins them."""

    assert content_sha256(_example(title="Study")) == content_sha256(
        _example(title="Estudio")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"notes": ()}, id="notes-dropped"),
        pytest.param(
            {
                "notes": (
                    Note(Fraction(0), Fraction(1), 65, "melody"),
                    Note(Fraction(1), Fraction(1), 62, "melody"),
                )
            },
            id="pitch-changed",
        ),
        pytest.param(
            {
                "notes": (
                    Note(Fraction(0), Fraction(2), 64, "melody"),
                    Note(Fraction(1), Fraction(1), 62, "melody"),
                )
            },
            id="duration-changed",
        ),
        pytest.param(
            {
                "notes": (
                    Note(Fraction(0), Fraction(1), 64, "bass"),
                    Note(Fraction(1), Fraction(1), 62, "melody"),
                )
            },
            id="voice-changed",
        ),
        pytest.param({"annotations": ()}, id="fingering-dropped"),
        pytest.param(
            {"annotations": (FingeringAnnotation(Fraction(0), 64, (3,), 0, 2),)},
            id="fingering-changed",
        ),
        pytest.param({"tuning": (38, 45, 50, 55, 59, 64)}, id="tuning-changed"),
        pytest.param({"capo": 2}, id="capo-changed"),
        pytest.param({"tempo_bpm": 120.0}, id="tempo-changed"),
    ],
)
def test_every_musical_change_moves_the_content_digest(mutation: dict[str, object]) -> None:
    baseline = content_sha256(_example())
    assert content_sha256(_example(**mutation)) != baseline  # type: ignore[arg-type]


def test_the_digest_is_stable_across_calls() -> None:
    """It is written into a manifest, so it cannot depend on dict ordering."""

    assert content_sha256(_example()) == content_sha256(_example())
