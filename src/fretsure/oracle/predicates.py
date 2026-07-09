"""Playability hard predicates.

Each predicate is a pure function of a *fully-fingered* :class:`Tab` and a
:class:`Profile`, returning localized :class:`Diagnostic` reports (empty ==
that constraint holds). The oracle verifies a given fingering; the solver
(Plan 2) reverse-searches feasible assignments.

Note indices in diagnostics are global indices into ``tab.notes``.
"""

from fractions import Fraction

from fretsure.oracle.diagnostics import Diagnostic
from fretsure.oracle.profiles import Profile
from fretsure.tab import Tab, TabNote


def _measure_beat(onset: Fraction, beats_per_bar: int) -> tuple[int, Fraction]:
    bar = int(onset // beats_per_bar)
    beat = onset - bar * beats_per_bar + 1
    return bar + 1, beat


def _indexed_frames(tab: Tab) -> list[tuple[Fraction, list[tuple[int, TabNote]]]]:
    """Frames as (onset, [(global_index, note), ...]) sorted by onset then string."""
    by_onset: dict[Fraction, list[tuple[int, TabNote]]] = {}
    for idx, n in enumerate(tab.notes):
        by_onset.setdefault(n.onset, []).append((idx, n))
    return [
        (onset, sorted(by_onset[onset], key=lambda t: t[1].string))
        for onset in sorted(by_onset)
    ]


def check_range(tab: Tab, profile: Profile, *, beats_per_bar: int = 4) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for idx, n in enumerate(tab.notes):
        if not 0 <= n.fret <= profile.max_fret:
            measure, beat = _measure_beat(n.onset, beats_per_bar)
            if n.fret > profile.max_fret:
                overage = float(n.fret - profile.max_fret)
                relax: tuple[str, ...] = ("octave_down_bass", "shift_to_lower_position")
            else:  # negative fret
                overage = float(-n.fret)
                relax = ()
            out.append(Diagnostic(measure, beat, "RANGE", (idx,), overage, relax))
    return out


def check_one_string_one_note(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for onset, notes in _indexed_frames(tab):
        by_string: dict[int, list[int]] = {}
        for idx, n in notes:
            by_string.setdefault(n.string, []).append(idx)
        for string in sorted(by_string):
            idxs = by_string[string]
            if len(idxs) > 1:
                measure, beat = _measure_beat(onset, beats_per_bar)
                out.append(
                    Diagnostic(
                        measure,
                        beat,
                        "ONE_STRING_ONE_NOTE",
                        tuple(idxs),
                        float(len(idxs) - 1),
                        (),
                    )
                )
    return out


def check_finger_count(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """A frame needs one distinct finger per distinct fretted fret (monotonicity
    forces distinct frets onto distinct fingers); >4 is infeasible."""
    out: list[Diagnostic] = []
    for onset, notes in _indexed_frames(tab):
        fretted = [(idx, n) for idx, n in notes if n.fret > 0]
        distinct_frets = {n.fret for _, n in fretted}
        if len(distinct_frets) > 4:
            measure, beat = _measure_beat(onset, beats_per_bar)
            out.append(
                Diagnostic(
                    measure,
                    beat,
                    "FINGER_COUNT",
                    tuple(idx for idx, _ in fretted),
                    float(len(distinct_frets) - 4),
                    ("drop_inner",),
                )
            )
    return out


def check_finger_monotonic(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """Fret order must map to finger order: fret_a<fret_b => finger_a<=finger_b,
    and the same finger may only span one fret (a barre)."""
    out: list[Diagnostic] = []
    for onset, notes in _indexed_frames(tab):
        fretted = [(idx, n) for idx, n in notes if n.fret > 0 and n.left_finger > 0]
        bad: set[int] = set()
        for ia, na in fretted:
            for ib, nb in fretted:
                if ia == ib:
                    continue
                if na.fret < nb.fret and na.left_finger > nb.left_finger:
                    bad.update((ia, ib))
                if na.left_finger == nb.left_finger and na.fret != nb.fret:
                    bad.update((ia, ib))
        if bad:
            measure, beat = _measure_beat(onset, beats_per_bar)
            out.append(
                Diagnostic(
                    measure, beat, "FINGER_MONOTONIC", tuple(sorted(bad)), 0.0, ("refinger",)
                )
            )
    return out


__all__ = [
    "check_finger_count",
    "check_finger_monotonic",
    "check_one_string_one_note",
    "check_range",
]
