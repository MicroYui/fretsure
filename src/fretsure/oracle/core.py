"""Three-state, model-relative playability verdict.

Direction inside the versioned model:
- RED   = fails even the *optimistic* profile,
- GREEN = passes even the *pessimistic* profile,
- AMBER = in between (send to repair / human; never a GREEN-certified output).

The verdict never relaxes the GREEN threshold; uncertainty is absorbed by AMBER.
Diagnostics are computed on the median profile for localization, and the whole
result is deterministic and version-stamped. Real-player error rates require a
separate human-played gold set.
"""

from dataclasses import dataclass
from fractions import Fraction

from fretsure.oracle.diagnostics import Diagnostic, Verdict
from fretsure.oracle.input import ORACLE_INPUT_SCHEMA_VERSION, ensure_oracle_input
from fretsure.oracle.predicates import (
    check_barre,
    check_finger_count,
    check_finger_monotonic,
    check_fret_span,
    check_one_string_one_note,
    check_range,
    check_right_hand,
    check_shift_speed,
    check_string_sustain,
    check_sustain,
    check_wellformed,
)
from fretsure.oracle.profiles import Profile, optimistic, pessimistic
from fretsure.tab import Tab

CHECKER_VERSION = "oracle@0.2.0"


@dataclass(frozen=True)
class OracleResult:
    verdict: Verdict
    diagnostics: tuple[Diagnostic, ...]
    checker_version: str
    profile_version: str
    profile_fingerprint: str
    input_schema_version: str


def _sort_key(d: Diagnostic) -> tuple[int, Fraction, str, tuple[int, ...]]:
    return (d.measure, d.beat, d.violation_type, d.offending_notes)


def _all_diagnostics(
    tab: Tab, profile: Profile, *, tempo_bpm: float, beats_per_bar: int
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    diags += check_wellformed(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_range(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_one_string_one_note(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_finger_count(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_finger_monotonic(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_fret_span(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_barre(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_shift_speed(tab, profile, tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar)
    diags += check_string_sustain(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_sustain(tab, profile, beats_per_bar=beats_per_bar)
    diags += check_right_hand(tab, profile, tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar)
    return sorted(diags, key=_sort_key)


def check_playability(
    tab: Tab, profile: Profile, *, tempo_bpm: float = 90.0, beats_per_bar: int = 4
) -> OracleResult:
    tab, profile, tempo_bpm, beats_per_bar = ensure_oracle_input(
        tab,
        profile,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
    )
    fails_optimistic = bool(
        _all_diagnostics(
            tab, optimistic(profile), tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar
        )
    )
    if fails_optimistic:
        verdict: Verdict = "RED"
    else:
        fails_pessimistic = bool(
            _all_diagnostics(
                tab,
                pessimistic(profile),
                tempo_bpm=tempo_bpm,
                beats_per_bar=beats_per_bar,
            )
        )
        verdict = "AMBER" if fails_pessimistic else "GREEN"

    diagnostics = _all_diagnostics(
        tab, profile, tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar
    )
    return OracleResult(
        verdict=verdict,
        diagnostics=tuple(diagnostics),
        checker_version=CHECKER_VERSION,
        profile_version=profile.version,
        profile_fingerprint=profile.fingerprint,
        input_schema_version=ORACLE_INPUT_SCHEMA_VERSION,
    )


def _any_violation(
    tab: Tab, profile: Profile, *, tempo_bpm: float, beats_per_bar: int
) -> bool:
    """True if any predicate flags a violation, short-circuiting on the first
    (cheap, profile-independent predicates first). Same predicate set as
    _all_diagnostics — no parallel logic."""
    if check_wellformed(tab, profile, beats_per_bar=beats_per_bar):
        return True
    if check_range(tab, profile, beats_per_bar=beats_per_bar):
        return True
    if check_one_string_one_note(tab, profile, beats_per_bar=beats_per_bar):
        return True
    if check_finger_count(tab, profile, beats_per_bar=beats_per_bar):
        return True
    if check_finger_monotonic(tab, profile, beats_per_bar=beats_per_bar):
        return True
    if check_barre(tab, profile, beats_per_bar=beats_per_bar):
        return True
    if check_fret_span(tab, profile, beats_per_bar=beats_per_bar):
        return True
    if check_right_hand(tab, profile, tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar):
        return True
    if check_shift_speed(tab, profile, tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar):
        return True
    if check_string_sustain(tab, profile, beats_per_bar=beats_per_bar):
        return True
    return bool(check_sustain(tab, profile, beats_per_bar=beats_per_bar))


def passes_optimistic(
    tab: Tab, profile: Profile, *, tempo_bpm: float = 90.0, beats_per_bar: int = 4
) -> bool:
    """True iff the tab is NOT RED (passes the optimistic profile). A fast path for
    the solver's inner loop: one profile evaluation with first-violation exit,
    equivalent to ``check_playability(...).verdict != "RED"``."""
    tab, profile, tempo_bpm, beats_per_bar = ensure_oracle_input(
        tab,
        profile,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
    )
    return not _any_violation(
        tab, optimistic(profile), tempo_bpm=tempo_bpm, beats_per_bar=beats_per_bar
    )
