"""python-ly drops string numbers, and this patch puts them back.

`<g-1\\2>` is *G, first finger, second string*: a complete placement with nothing
left to infer. python-ly lexes it -- `ly.lex.lilypond.StringNumber` exists, with
regex `\\\\\\d+` -- and its mediator's own docstring claims to handle "an
articulation, fingering, string number, or other symbol". The string-number
branch was never written, so the token falls through the articulation lookup and
is discarded. 619 indications across 31 vendored sources reached this corpus as
nothing at all.

That mattered more than a missing field usually does. Without the string, a
printed fingering does not locate a note -- D4 with finger 3 is four different
shapes -- so every geometric question about the verifier had to guess, and the
guessing produced three wrong results in one week.

python-ly is not a runtime dependency; it is needed only to rebuild the corpus
from LilyPond. These tests therefore skip when it is absent, the same shape as
the negative-tab guard, rather than making an offline build tool a requirement
for running the suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

ly = pytest.importorskip("ly", reason="python-ly is an offline corpus-build tool")


@pytest.fixture(scope="module")
def patched() -> object:
    import python_ly_string_numbers

    python_ly_string_numbers.apply(ly)
    return python_ly_string_numbers


def _convert(source: str) -> str:
    from ly.musicxml import writer

    handle = writer()
    handle.parse_text(source)
    return handle.musicxml().tostring().decode("utf-8", "replace")


_SCORE = r"""
\version "2.18.2"
\score {
  \new Staff {
    \relative c' {
      <g-1\2 c-3\5>4 e-2\4 g4 c4
    }
  }
  \layout { }
}
"""


def test_a_string_number_survives_the_conversion(patched) -> None:
    """The whole point: the engraver said which string and it comes out."""

    xml = _convert(_SCORE)
    assert "<string>" in xml, "python-ly still drops the string number"
    # Two, not three: the chord's two members share one `current_note`, so the
    # second indication overwrites the first. python-ly's own fingering support
    # loses one the same way on the same input, so this asserts parity with the
    # branch being mirrored rather than an improvement on it.
    assert xml.count("<string>") == 2


def test_the_string_sits_inside_the_technical_element(patched) -> None:
    """Where MusicXML puts it, and where `score_corpus.py` looks for it.

    Emitting it anywhere else would be silently invisible to the importer, which
    is exactly the failure being repaired.
    """

    xml = _convert(_SCORE)
    index = xml.find("<string>")
    assert index > 0
    preceding = xml[:index]
    assert preceding.rfind("<technical>") > preceding.rfind("</technical>")


def test_fingerings_are_untouched(patched) -> None:
    """The patch adds a branch; it must not divert the one already there."""

    xml = _convert(_SCORE)
    # Also two, and for the same reason -- which is the point: the patch neither
    # helps nor harms the path it sits beside.
    assert xml.count("<fingering>") == 2


def test_applying_twice_changes_nothing(patched) -> None:
    """The corpus build loads the converter once per manifest and should not
    have to track whether the patch already ran."""

    import python_ly_string_numbers

    before = _convert(_SCORE)
    python_ly_string_numbers.apply(ly)
    python_ly_string_numbers.apply(ly)
    assert _convert(_SCORE) == before


def test_a_score_without_string_numbers_gains_none(patched) -> None:
    """No string element is invented where the engraver did not write one."""

    plain = _SCORE.replace(r"\2", "").replace(r"\5", "").replace(r"\4", "")
    xml = _convert(plain)
    assert "<string>" not in xml
    assert xml.count("<fingering>") == 2
