"""Mutation testing: inject a known fault into each predicate and confirm a
trigger input *kills* it (the mutant's verdict differs from the real one).

A surviving mutant means the trigger set can't distinguish a broken predicate
from a correct one — a test-adequacy gap. Threshold-relaxation faults feed the
real predicate a perturbed profile (widened resource = constraint effectively
removed); deletion faults return no diagnostics.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction as F

from fretsure.oracle.diagnostics import Diagnostic
from fretsure.oracle.predicates import (
    check_barre,
    check_finger_count,
    check_finger_monotonic,
    check_fret_span,
    check_one_string_one_note,
    check_range,
    check_right_hand,
    check_shift_speed,
    check_sustain,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.tab import Tab, TabNote

Pred = Callable[[Tab, Profile], list[Diagnostic]]

_TUN = (40, 45, 50, 55, 59, 64)


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), _TUN, 0)


# Trigger tabs: each violates its target predicate under the real MEDIAN profile.
_RANGE = _t([TabNote(F(0), F(1), 0, 99, 1, "p")])
_ONE_STRING = _t([TabNote(F(0), F(1), 2, 3, 1, "i"), TabNote(F(0), F(1), 2, 5, 2, "m")])
_fc = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]
_FINGER_COUNT = _t([TabNote(F(0), F(1), s, fr, min(fr, 4), "p") for s, fr in _fc])
_MONOTONIC = _t([TabNote(F(0), F(1), 1, 2, 3, "p"), TabNote(F(0), F(1), 2, 5, 1, "i")])
_SPAN = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 15, 2, "i")])
_BARRE = _t(
    [
        TabNote(F(0), F(1), 0, 5, 1, "p"),
        TabNote(F(0), F(1), 3, 5, 1, "a"),
        TabNote(F(0), F(1), 1, 2, 2, "i"),
    ]
)
_SHIFT = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(1, 4), F(1), 0, 12, 1, "p")])
_SUSTAIN = _t([TabNote(F(0), F(2), 0, 3, 1, "p"), TabNote(F(1), F(1), 1, 5, 1, "i")])
_RH_REPEAT = _t([TabNote(F(0), F(1), 0, 0, 0, "p"), TabNote(F(1, 32), F(1), 0, 0, 0, "p")])

# Perturbed profiles that neutralize a single constraint (fault injection).
_NO_MAX_FRET = replace(MEDIAN_HAND, max_fret=99_999)
_HUGE_SPAN = replace(MEDIAN_HAND, hand_span_mm=1e6)
_HUGE_SHIFT = replace(MEDIAN_HAND, v_shift_mm_per_s=1e9)
_HUGE_RMAX = replace(MEDIAN_HAND, r_max_hz=1e9)


def _under(pred: Pred, profile: Profile) -> Pred:
    """A mutant that runs the real predicate under a constraint-neutralizing profile."""

    def mutant(tab: Tab, _profile: Profile) -> list[Diagnostic]:
        return pred(tab, profile)

    return mutant


def _deleted(tab: Tab, profile: Profile) -> list[Diagnostic]:
    return []


# (name, real predicate, mutant, trigger tabs)
MUTANTS: list[tuple[str, Pred, Pred, tuple[Tab, ...]]] = [
    ("range_no_upper_bound", check_range, _under(check_range, _NO_MAX_FRET), (_RANGE,)),
    ("span_dmax_widened", check_fret_span, _under(check_fret_span, _HUGE_SPAN), (_SPAN,)),
    ("shift_speed_disabled", check_shift_speed, _under(check_shift_speed, _HUGE_SHIFT), (_SHIFT,)),
    ("rh_repeat_ignored", check_right_hand, _under(check_right_hand, _HUGE_RMAX), (_RH_REPEAT,)),
    ("one_string_one_note_deleted", check_one_string_one_note, _deleted, (_ONE_STRING,)),
    ("finger_count_deleted", check_finger_count, _deleted, (_FINGER_COUNT,)),
    ("finger_monotonic_deleted", check_finger_monotonic, _deleted, (_MONOTONIC,)),
    ("barre_deleted", check_barre, _deleted, (_BARRE,)),
    ("sustain_deleted", check_sustain, _deleted, (_SUSTAIN,)),
]


@dataclass(frozen=True)
class MutationReport:
    total: int
    killed: int
    survived: tuple[str, ...]


def run_mutation_suite() -> MutationReport:
    killed = 0
    survived: list[str] = []
    for name, real_fn, mutant_fn, triggers in MUTANTS:
        is_killed = any(
            bool(real_fn(tab, MEDIAN_HAND)) != bool(mutant_fn(tab, MEDIAN_HAND))
            for tab in triggers
        )
        if is_killed:
            killed += 1
        else:
            survived.append(name)
    return MutationReport(len(MUTANTS), killed, tuple(survived))


def kill_rate(report: MutationReport) -> float:
    return report.killed / report.total if report.total else 1.0
