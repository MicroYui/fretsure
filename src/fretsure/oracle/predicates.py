"""Playability hard predicates.

Each predicate is a pure function of a *fully-fingered* :class:`Tab` and a
:class:`Profile`, returning localized :class:`Diagnostic` reports (empty ==
that constraint holds). The oracle verifies a given fingering; the solver
(Plan 2) reverse-searches feasible assignments.

Note indices in diagnostics are global indices into ``tab.notes``.
"""

from fractions import Fraction

from fretsure.geometry import d_max, euclid, fingertip_xy, press_x
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
    "check_barre",
    "check_finger_count",
    "check_finger_monotonic",
    "check_fret_span",
    "check_one_string_one_note",
    "check_range",
    "check_right_hand",
    "check_shift_speed",
    "check_sustain",
]

_RIGHT_RANK = {"p": 0, "i": 1, "m": 2, "a": 3}  # thumb..ring, low->high strings


def check_fret_span(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """Verify the *given* fingering is geometrically reachable: every
    different-finger fretted pair within ``d_max`` (mm)."""
    out: list[Diagnostic] = []
    for onset, notes in _indexed_frames(tab):
        fretted = [(idx, n) for idx, n in notes if n.fret > 0 and n.left_finger > 0]
        offending: set[int] = set()
        max_over = 0.0
        for a in range(len(fretted)):
            for b in range(a + 1, len(fretted)):
                ia, na = fretted[a]
                ib, nb = fretted[b]
                if na.left_finger == nb.left_finger:
                    continue
                pa = fingertip_xy(na.string, tab.capo + na.fret, profile.string_length_mm)
                pb = fingertip_xy(nb.string, tab.capo + nb.fret, profile.string_length_mm)
                assert pa is not None and pb is not None
                dist = euclid(pa, pb)
                limit = d_max(na.left_finger, nb.left_finger, profile.hand_span_mm)
                if dist > limit:
                    offending.update((ia, ib))
                    max_over = max(max_over, dist - limit)
        if offending:
            measure, beat = _measure_beat(onset, beats_per_bar)
            out.append(
                Diagnostic(
                    measure,
                    beat,
                    "FRET_SPAN",
                    tuple(sorted(offending)),
                    max_over,
                    ("drop_5th", "shift_to_lower_position"),
                )
            )
    return out


def check_barre(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """A barre (one finger, multiple strings) must be a single fret, and nothing
    lower may be needed on the strings it spans (you cannot fret behind a barre).

    v1 model: covered strings are the inclusive span of the barre's strings.
    """
    out: list[Diagnostic] = []
    for onset, notes in _indexed_frames(tab):
        by_finger: dict[int, list[tuple[int, TabNote]]] = {}
        for idx, n in notes:
            if n.fret > 0 and n.left_finger > 0:
                by_finger.setdefault(n.left_finger, []).append((idx, n))
        offending: set[int] = set()
        for finger in sorted(by_finger):
            grp = by_finger[finger]
            if len(grp) < 2:
                continue
            frets = {n.fret for _, n in grp}
            if len(frets) > 1:  # a finger cannot span two frets
                offending.update(idx for idx, _ in grp)
                continue
            barre_fret = next(iter(frets))
            strings = [n.string for _, n in grp]
            lo, hi = min(strings), max(strings)
            for idx, n in notes:
                if lo <= n.string <= hi and n.left_finger != finger and n.fret < barre_fret:
                    offending.add(idx)
        if offending:
            measure, beat = _measure_beat(onset, beats_per_bar)
            out.append(
                Diagnostic(
                    measure,
                    beat,
                    "BARRE_INFEASIBLE",
                    tuple(sorted(offending)),
                    0.0,
                    ("substitute_voicing",),
                )
            )
    return out


def check_shift_speed(
    tab: Tab, profile: Profile, *, tempo_bpm: float = 90.0, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """Between consecutive frames the hand centre (mean absolute press-x of the
    fretted notes) may not move faster than ``v_shift``. A shared
    (string, fret, finger) across the two frames acts as a guide finger and
    anchors the hand, so no shift is charged."""
    out: list[Diagnostic] = []
    prev: tuple[Fraction, float, set[tuple[int, int, int]]] | None = None
    for onset, notes in _indexed_frames(tab):
        fretted = [(idx, n) for idx, n in notes if n.fret > 0]
        if not fretted:
            prev = None  # open-only frame: hand position undefined, reset
            continue
        xs: list[float] = []
        for _, n in fretted:
            px = press_x(tab.capo + n.fret, profile.string_length_mm)
            assert px is not None  # fret > 0 => not open
            xs.append(px)
        hand_center = sum(xs) / len(xs)
        keys = {(n.string, n.fret, n.left_finger) for _, n in fretted}
        if prev is not None:
            p_onset, p_center, p_keys = prev
            if not (keys & p_keys):  # no guide finger anchoring the hand
                dt = float(onset - p_onset) * 60.0 / tempo_bpm
                if dt > 0:
                    speed = abs(hand_center - p_center) / dt
                    if speed > profile.v_shift_mm_per_s:
                        measure, beat = _measure_beat(onset, beats_per_bar)
                        out.append(
                            Diagnostic(
                                measure,
                                beat,
                                "SHIFT_SPEED",
                                tuple(idx for idx, _ in fretted),
                                speed - profile.v_shift_mm_per_s,
                                ("shift_to_lower_position",),
                            )
                        )
        prev = (onset, hand_center, keys)
    return out


def check_sustain(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """A held note whose left finger is needed at a *different fret* while it is
    still sounding is infeasible. Same-fret different-string overlap is a barre,
    not a conflict."""
    out: list[Diagnostic] = []
    indexed = list(enumerate(tab.notes))
    for a in range(len(indexed)):
        ia, na = indexed[a]
        if na.fret <= 0 or na.left_finger <= 0:
            continue
        for b in range(a + 1, len(indexed)):
            ib, nb = indexed[b]
            if nb.fret <= 0 or nb.left_finger <= 0:
                continue
            overlap = (
                na.onset < nb.onset + nb.duration
                and nb.onset < na.onset + na.duration
            )
            if overlap and na.left_finger == nb.left_finger and na.fret != nb.fret:
                measure, beat = _measure_beat(max(na.onset, nb.onset), beats_per_bar)
                out.append(
                    Diagnostic(
                        measure,
                        beat,
                        "SUSTAIN_CONFLICT",
                        (ia, ib),
                        0.0,
                        ("octave_down_bass", "refinger"),
                    )
                )
    return out


def check_right_hand(
    tab: Tab, profile: Profile, *, tempo_bpm: float = 90.0, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """Right-hand (p-i-m-a) feasibility: one finger per plucked string in a
    frame, thumb-to-ring following ascending string index, at most four
    simultaneous plucks, and no single finger repeating faster than ``r_max``."""
    out: list[Diagnostic] = []
    last_used: dict[str, Fraction] = {}
    for onset, notes in _indexed_frames(tab):
        measure, beat = _measure_beat(onset, beats_per_bar)

        if len(notes) > 4:
            out.append(
                Diagnostic(
                    measure,
                    beat,
                    "RIGHT_HAND",
                    tuple(idx for idx, _ in notes),
                    float(len(notes) - 4),
                    ("drop_inner",),
                )
            )

        bad: set[int] = set()
        by_finger: dict[str, list[int]] = {}
        for idx, n in notes:
            by_finger.setdefault(n.right_finger, []).append(idx)
        for idxs in by_finger.values():
            if len(idxs) > 1:  # one finger cannot pluck two strings at once
                bad.update(idxs)
        for ia, na in notes:
            for ib, nb in notes:
                if ia == ib:
                    continue
                ra, rb = _RIGHT_RANK[na.right_finger], _RIGHT_RANK[nb.right_finger]
                if na.string < nb.string and ra > rb:
                    bad.update((ia, ib))
        if bad:
            out.append(
                Diagnostic(measure, beat, "RIGHT_HAND", tuple(sorted(bad)), 0.0, ("refinger",))
            )

        for idx, n in notes:
            prev = last_used.get(n.right_finger)
            if prev is not None:
                dt = float(onset - prev) * 60.0 / tempo_bpm
                if 0 < dt < 1.0 / profile.r_max_hz:
                    out.append(
                        Diagnostic(
                            measure,
                            beat,
                            "RIGHT_HAND",
                            (idx,),
                            profile.r_max_hz - 1.0 / dt,
                            ("simplify_rhythm",),
                        )
                    )
        for _, n in notes:
            last_used[n.right_finger] = onset
    return out
