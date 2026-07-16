"""Playability hard predicates.

Each predicate is a pure function of a *fully-fingered* :class:`Tab` and a
:class:`Profile`, returning localized :class:`Diagnostic` reports (empty ==
that constraint holds). The oracle verifies a given fingering; the solver
(Plan 2) reverse-searches feasible assignments.

Note indices in diagnostics are global indices into ``tab.notes``.
"""

import heapq
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
    """Attack frames, sorted by onset and then string."""
    by_onset: dict[Fraction, list[tuple[int, TabNote]]] = {}
    for idx, n in enumerate(tab.notes):
        by_onset.setdefault(n.onset, []).append((idx, n))
    return [
        (onset, sorted(by_onset[onset], key=lambda t: t[1].string))
        for onset in sorted(by_onset)
    ]


def _indexed_sounding_frames(
    tab: Tab,
) -> list[tuple[Fraction, list[tuple[int, TabNote]]]]:
    """Left-hand frames containing every note sounding at each attack onset.

    Fretted sustain is a physical hold, not merely a duration used for audio.
    Geometry, finger ordering, barre feasibility and hand position must therefore
    see notes whose half-open interval ``[onset, onset + duration)`` began in an
    earlier attack frame.

    A malformed Tab may contain thousands of overlapping intervals on one
    string.  ``check_string_sustain`` rejects that independently; this helper
    keeps one deterministic, most-recent active representative per string so
    downstream geometry stays bounded to the six-string physical state instead
    of materializing an O(n^2) active-frame explosion.  On a string-valid Tab the
    representative is the sole sounding note, so no information is discarded.
    """

    starts = sorted(
        enumerate(tab.notes),
        key=lambda item: (item[1].onset, item[0]),
    )
    active: dict[int, TabNote] = {}
    end_heap: list[tuple[Fraction, int]] = []
    # Min-heaps over negative onset/index give the most recent active attack.
    latest_by_string: dict[int, list[tuple[Fraction, int, int]]] = {}
    result: list[tuple[Fraction, list[tuple[int, TabNote]]]] = []

    cursor = 0
    while cursor < len(starts):
        onset = starts[cursor][1].onset
        while end_heap and end_heap[0][0] <= onset:
            _end, expired_index = heapq.heappop(end_heap)
            active.pop(expired_index, None)

        while cursor < len(starts) and starts[cursor][1].onset == onset:
            index, note = starts[cursor]
            active[index] = note
            heapq.heappush(end_heap, (note.onset + note.duration, index))
            heapq.heappush(
                latest_by_string.setdefault(note.string, []),
                (-note.onset, -index, index),
            )
            cursor += 1

        frame: list[tuple[int, TabNote]] = []
        for string in sorted(latest_by_string):
            heap = latest_by_string[string]
            while heap and heap[0][2] not in active:
                heapq.heappop(heap)
            if heap:
                index = heap[0][2]
                frame.append((index, active[index]))
        result.append((onset, frame))
    return result


def check_wellformed(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """A note must be a valid exhibited fingering: fretted-with-a-finger or
    open-without-one (``fret > 0`` iff ``left_finger > 0``), the left finger in
    ``0..4``, and the right finger in ``{p, i, m, a}``. Rejecting this is a
    soundness requirement: an out-of-domain finger (e.g. ``left_finger == 5``)
    would inflate ``d_max`` and certify a span no real 4-finger hand can reach,
    and an unfiltered note would slip past the finger-filtered predicates."""
    out: list[Diagnostic] = []
    for idx, n in enumerate(tab.notes):
        malformed = (
            (n.fret > 0) != (n.left_finger > 0)
            or not 0 <= n.left_finger <= 4
            or n.right_finger not in ("p", "i", "m", "a")
        )
        if malformed:
            measure, beat = _measure_beat(n.onset, beats_per_bar)
            out.append(
                Diagnostic(measure, beat, "MALFORMED_FINGERING", (idx,), 0.0, ("refinger",))
            )
    return out


def check_range(tab: Tab, profile: Profile, *, beats_per_bar: int = 4) -> list[Diagnostic]:
    """Each note's *absolute* position (``capo + fret``) must sit on the neck:
    ``0 <= fret`` and ``capo + fret <= max_fret``. The upper bound is absolute
    because a capo does not move the neck's last fret."""
    out: list[Diagnostic] = []
    for idx, n in enumerate(tab.notes):
        absolute = tab.capo + n.fret
        if n.fret < 0 or absolute > profile.max_fret:
            measure, beat = _measure_beat(n.onset, beats_per_bar)
            if absolute > profile.max_fret:
                overage = float(absolute - profile.max_fret)
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
    for onset, notes in _indexed_sounding_frames(tab):
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
    for onset, notes in _indexed_sounding_frames(tab):
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
    "check_string_sustain",
    "check_sustain",
    "check_wellformed",
]

_RIGHT_RANK = {"p": 0, "i": 1, "m": 2, "a": 3}  # thumb..ring, low->high strings


def check_fret_span(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """Verify the *given* fingering is geometrically reachable: every
    different-finger fretted pair within ``d_max`` (mm)."""
    out: list[Diagnostic] = []
    for onset, notes in _indexed_sounding_frames(tab):
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
    for onset, notes in _indexed_sounding_frames(tab):
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


_HandInterval = tuple[float, float]


def _expand_interval(interval: _HandInterval, distance: float) -> _HandInterval:
    return (interval[0] - distance, interval[1] + distance)


def _intersect_intervals(
    left: _HandInterval, right: _HandInterval
) -> _HandInterval | None:
    lower = max(left[0], right[0])
    upper = min(left[1], right[1])
    return (lower, upper) if lower <= upper else None


def _interval_gap(left: _HandInterval, right: _HandInterval) -> float:
    """Distance between disjoint intervals, or zero when they overlap."""
    if left[1] < right[0]:
        return right[0] - left[1]
    if right[1] < left[0]:
        return left[0] - right[1]
    return 0.0


def check_shift_speed(
    tab: Tab, profile: Profile, *, tempo_bpm: float = 90.0, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """Propagate the reachable one-dimensional hand-centre interval.

    Every positive-duration fretted note constrains the hand for its half-open
    sounding interval.  At each event time releases are applied before attacks.
    Between events the reachable interval expands at ``v_shift`` and is clipped
    to the active shape's feasible interval::

        [max(press_x_i - reach), min(press_x_i + reach)]

    The reachable *subset* is never reset at an ordinary transition or by a
    guide note.  A sustained guide works naturally because its constraint stays
    active while the newly attacked notes further intersect the same state.
    Open-string attacks create no left-hand event.  ``overage`` is the finite
    interval gap, in millimetres, at an impossible attack.
    """

    attacks: dict[Fraction, list[tuple[int, TabNote]]] = {}
    releases: dict[Fraction, list[int]] = {}
    for index, note in enumerate(tab.notes):
        if note.fret <= 0 or note.duration <= 0:
            continue
        attacks.setdefault(note.onset, []).append((index, note))
        releases.setdefault(note.onset + note.duration, []).append(index)

    active: dict[int, _HandInterval] = {}
    max_lower: list[tuple[float, int]] = []
    min_upper: list[tuple[float, int]] = []

    def active_shape() -> _HandInterval | None:
        while max_lower and max_lower[0][1] not in active:
            heapq.heappop(max_lower)
        while min_upper and min_upper[0][1] not in active:
            heapq.heappop(min_upper)
        if not active:
            return None
        return (-max_lower[0][0], min_upper[0][0])

    def closest_shape_state(
        source: _HandInterval, shape: _HandInterval
    ) -> _HandInterval:
        """Return a bounded singleton after an already-diagnosed empty set."""
        if shape[0] > shape[1]:
            midpoint = (shape[0] + shape[1]) / 2.0
            return (midpoint, midpoint)
        if source[1] < shape[0]:
            return (shape[0], shape[0])
        return (shape[1], shape[1])

    out: list[Diagnostic] = []
    reachable: _HandInterval | None = None
    previous_time: Fraction | None = None
    event_times = sorted(attacks.keys() | releases.keys())

    for event_time in event_times:
        if reachable is not None and previous_time is not None:
            elapsed_seconds = (
                float(event_time - previous_time) * 60.0 / tempo_bpm
            )
            arrival = _expand_interval(
                reachable,
                profile.v_shift_mm_per_s * elapsed_seconds,
            )
            held_shape = active_shape()
            if held_shape is None:
                reachable = arrival
            else:
                held_reachable = _intersect_intervals(arrival, held_shape)
                # A valid prior state cannot lose feasibility while its active
                # shape is unchanged: expansion only widens it.  Projection is
                # needed solely to keep diagnostics finite after an earlier
                # intrinsically infeasible shape.
                reachable = (
                    held_reachable
                    if held_reachable is not None
                    else closest_shape_state(arrival, held_shape)
                )

        # Half-open sounding intervals: a note ending now no longer constrains
        # an attack at the same time.  Releasing a constraint never widens the
        # reachable state instantaneously and never emits a diagnostic.
        for index in releases.get(event_time, ()):
            active.pop(index, None)

        new_attacks = attacks.get(event_time, ())
        for index, note in new_attacks:
            x = press_x(tab.capo + note.fret, profile.string_length_mm)
            assert x is not None  # fret > 0 => not open
            interval = (x - profile.reach_mm, x + profile.reach_mm)
            active[index] = interval
            heapq.heappush(max_lower, (-interval[0], index))
            heapq.heappush(min_upper, (interval[1], index))

        if new_attacks:
            shape = active_shape()
            assert shape is not None
            intrinsic_gap = max(0.0, shape[0] - shape[1])
            if reachable is None:
                # The first feasible fretted shape may start at any hand centre.
                if intrinsic_gap == 0.0:
                    reachable = shape
                else:
                    midpoint = (shape[0] + shape[1]) / 2.0
                    reachable = (midpoint, midpoint)
            else:
                intersection = _intersect_intervals(reachable, shape)
                if intersection is not None:
                    reachable = intersection
                else:
                    if intrinsic_gap == 0.0:
                        travel_gap = _interval_gap(reachable, shape)
                    else:
                        midpoint = (shape[0] + shape[1]) / 2.0
                        travel_gap = _interval_gap(
                            reachable, (midpoint, midpoint)
                        )
                    reachable = closest_shape_state(reachable, shape)
                    intrinsic_gap = max(intrinsic_gap, travel_gap)

            if intrinsic_gap > 0.0:
                measure, beat = _measure_beat(event_time, beats_per_bar)
                out.append(
                    Diagnostic(
                        measure,
                        beat,
                        "SHIFT_SPEED",
                        tuple(index for index, _ in new_attacks),
                        intrinsic_gap,
                        ("shift_to_lower_position",),
                    )
                )

        previous_time = event_time

    return out


def check_string_sustain(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """Reject overlapping sounding intervals on one physical string.

    ``one_string_one_note`` only covers attacks at the same onset.  A later
    attack also terminates/replaces what that string was sounding, so an
    explicitly longer previous duration cannot coexist with it.  Intervals are
    half-open: an attack exactly at the prior end is valid.

    At most one representative conflict is emitted per newly-starting note.
    Tracking the active interval with the latest end is sufficient to detect
    every conflict while keeping hostile long-sustain inputs O(n log n) rather
    than materializing O(n^2) pairs.
    """

    del profile  # model-independent predicate; retained for the common API
    by_string: dict[int, list[tuple[int, TabNote]]] = {}
    for index, note in enumerate(tab.notes):
        by_string.setdefault(note.string, []).append((index, note))

    out: list[Diagnostic] = []
    for string in sorted(by_string):
        entries = sorted(
            by_string[string],
            key=lambda item: (item[1].onset, item[0]),
        )
        active: tuple[Fraction, int] | None = None  # latest end, representative index
        for index, note in entries:
            end = note.onset + note.duration
            if active is not None:
                active_end, active_index = active
                if note.onset < active_end:
                    measure, beat = _measure_beat(note.onset, beats_per_bar)
                    out.append(
                        Diagnostic(
                            measure,
                            beat,
                            "STRING_SUSTAIN_CONFLICT",
                            tuple(sorted((active_index, index))),
                            float(active_end - note.onset),
                            ("shorten_sustain", "reposition"),
                        )
                    )
            if active is None or end > active[0]:
                active = (end, index)
    return out


def check_sustain(
    tab: Tab, profile: Profile, *, beats_per_bar: int = 4
) -> list[Diagnostic]:
    """A held note whose left finger is needed at a *different fret* while it is
    still sounding is infeasible. Same-fret different-string overlap is a barre,
    not a conflict."""
    del profile  # model-independent predicate; retained for the common API
    by_finger: dict[int, list[tuple[int, TabNote]]] = {}
    for index, note in enumerate(tab.notes):
        if note.fret > 0 and note.left_finger > 0:
            by_finger.setdefault(note.left_finger, []).append((index, note))

    out: list[Diagnostic] = []
    for finger in sorted(by_finger):
        entries = sorted(
            by_finger[finger],
            key=lambda item: (item[1].onset, item[0]),
        )
        # One latest-ending representative per fret is enough: same-fret
        # overlap is a barre; any active different fret proves a conflict.
        active_by_fret: dict[int, tuple[Fraction, int]] = {}
        for index, note in entries:
            active_by_fret = {
                fret: active
                for fret, active in active_by_fret.items()
                if note.onset < active[0]
            }
            conflicts = [
                (end, prior_index)
                for fret, (end, prior_index) in active_by_fret.items()
                if fret != note.fret
            ]
            if conflicts:
                _end, prior_index = max(conflicts, key=lambda item: (item[0], -item[1]))
                measure, beat = _measure_beat(note.onset, beats_per_bar)
                out.append(
                    Diagnostic(
                        measure,
                        beat,
                        "SUSTAIN_CONFLICT",
                        tuple(sorted((prior_index, index))),
                        0.0,
                        ("octave_down_bass", "refinger"),
                    )
                )
            end = note.onset + note.duration
            current = active_by_fret.get(note.fret)
            if current is None or end > current[0]:
                active_by_fret[note.fret] = (end, index)
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
                ra = _RIGHT_RANK.get(na.right_finger, 0)
                rb = _RIGHT_RANK.get(nb.right_finger, 0)
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
                            1.0 / dt - profile.r_max_hz,
                            ("simplify_rhythm",),
                        )
                    )
        for _, n in notes:
            last_used[n.right_finger] = onset
    return out
