from fractions import Fraction as F

import pytest

from fretsure.arrange.revision import SectionSelection, measure_bounds, merge_section_target
from fretsure.ir import Meta, MusicIR, Note


def _source() -> MusicIR:
    return MusicIR(
        (
            Note(F(0), F(1), 64, "melody"),
            Note(F(4), F(1), 65, "melody"),
        ),
        (),
        Meta("C", (4, 4), 90.0, "test", "revision", "CC0", F(8)),
    )


def test_measure_merge_changes_only_unlocked_voices_inside_selection() -> None:
    source = _source()
    baseline = (
        Note(F(0), F(1), 40, "bass"),
        Note(F(0), F(1), 52, "harmony"),
        Note(F(0), F(1), 64, "melody"),
        Note(F(4), F(1), 41, "bass"),
        Note(F(4), F(1), 55, "harmony"),
        Note(F(4), F(1), 65, "melody"),
    )
    proposal = (
        Note(F(4), F(1), 45, "bass"),
        Note(F(4), F(1), 57, "harmony"),
        Note(F(5), F(1), 67, "melody"),
    )
    selection = SectionSelection(2, 2, ("bass",))

    revised = merge_section_target(source, baseline, proposal, selection)

    assert measure_bounds(source, selection) == (F(4), F(8))
    assert {note for note in revised if note.onset < 4} == {
        note for note in baseline if note.onset < 4
    }
    assert Note(F(4), F(1), 41, "bass") in revised
    assert Note(F(4), F(1), 45, "bass") not in revised
    assert Note(F(4), F(1), 55, "harmony") not in revised
    assert Note(F(4), F(1), 57, "harmony") in revised
    assert Note(F(4), F(1), 65, "melody") in revised
    assert Note(F(5), F(1), 67, "melody") in revised


def test_all_voice_locks_make_merge_an_exact_noop() -> None:
    source = _source()
    baseline = source.notes
    proposal = (Note(F(4), F(1), 72, "harmony"),)
    selection = SectionSelection(2, 2, ("melody", "bass", "harmony"))

    assert merge_section_target(source, baseline, proposal, selection) == baseline


def test_selection_accepts_32_measures_and_normalizes_lock_order() -> None:
    selection = SectionSelection(3, 34, ("harmony", "melody"))
    assert selection.locked_voices == ("melody", "harmony")


def test_selection_rejects_more_than_32_measures_and_duplicate_locks() -> None:
    with pytest.raises(ValueError, match="1..32"):
        SectionSelection(1, 33, ())
    with pytest.raises(ValueError, match="unique"):
        SectionSelection(1, 1, ("bass", "bass"))
