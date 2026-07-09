"""Geometric span feasibility as a CSP.

``feasible_finger_assignment`` is a pruned depth-first search; a separate
exhaustive ``feasible_finger_assignment_bruteforce`` is an independent
second implementation (N-version) that the test suite differentially fuzzes
against the fast one — a bug in one is unlikely to be mirrored in the other.

A ``FingerAssignment`` gives one left-hand finger (1..4) per *fretted* note of a
frame, aligned to ``[n for n in frame if n.fret > 0]``. Open strings need no
finger. ``capo`` shifts every fretted note to its absolute neck position.
"""

import itertools

from fretsure.geometry import d_max, euclid, fingertip_xy
from fretsure.oracle.profiles import Profile
from fretsure.tab import Frame, TabNote

FingerAssignment = tuple[int, ...]


def _fretted(frame: Frame) -> list[TabNote]:
    return [n for n in frame if n.fret > 0]


def assignment_valid(
    fretted: list[TabNote],
    assignment: FingerAssignment,
    profile: Profile,
    *,
    capo: int = 0,
) -> bool:
    """True iff ``assignment`` satisfies monotonicity, barre, and mm-span limits."""
    n = len(fretted)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            na, nb = fretted[i], fretted[j]
            fa, fb = assignment[i], assignment[j]
            if na.fret < nb.fret and fa > fb:  # monotonic
                return False
            if fa == fb and na.fret != nb.fret:  # same finger => same fret (barre)
                return False
    sl = profile.string_length_mm
    hs = profile.hand_span_mm
    for i in range(n):
        for j in range(i + 1, n):
            if assignment[i] != assignment[j]:
                pa = fingertip_xy(fretted[i].string, capo + fretted[i].fret, sl)
                pb = fingertip_xy(fretted[j].string, capo + fretted[j].fret, sl)
                assert pa is not None and pb is not None  # fret > 0 => fretted
                if euclid(pa, pb) > d_max(assignment[i], assignment[j], hs):
                    return False
    return True


def feasible_finger_assignment(
    frame: Frame, profile: Profile, *, capo: int = 0
) -> FingerAssignment | None:
    """Pruned DFS: assign fingers in fret-ascending order, bounding each finger
    below by the max finger of strictly-lower-fret notes (monotonicity) and
    pruning on barre/mm-span. Returns an aligned assignment, or None."""
    fretted = _fretted(frame)
    if not fretted:
        return ()
    n = len(fretted)
    order = sorted(range(n), key=lambda k: fretted[k].fret)
    assign = [0] * n

    def dfs(pos: int) -> bool:
        if pos == n:
            return True
        k = order[pos]
        note = fretted[k]
        lo = 1
        for pk in order[:pos]:
            if fretted[pk].fret < note.fret:
                lo = max(lo, assign[pk])
        for f in range(lo, 5):
            ok = True
            for pk in order[:pos]:
                pn = fretted[pk]
                pf = assign[pk]
                if f == pf and pn.fret != note.fret:
                    ok = False
                    break
                if f != pf:
                    pa = fingertip_xy(note.string, capo + note.fret, profile.string_length_mm)
                    pb = fingertip_xy(pn.string, capo + pn.fret, profile.string_length_mm)
                    assert pa is not None and pb is not None
                    if euclid(pa, pb) > d_max(f, pf, profile.hand_span_mm):
                        ok = False
                        break
            if ok:
                assign[k] = f
                if dfs(pos + 1):
                    return True
                assign[k] = 0
        return False

    if dfs(0):
        return tuple(assign)
    return None


def feasible_finger_assignment_bruteforce(
    frame: Frame, profile: Profile, *, capo: int = 0
) -> FingerAssignment | None:
    """Exhaustive N-version spec: enumerate all 4**k assignments in order."""
    fretted = _fretted(frame)
    if not fretted:
        return ()
    for combo in itertools.product(range(1, 5), repeat=len(fretted)):
        if assignment_valid(fretted, combo, profile, capo=capo):
            return combo
    return None


def feasible_fingerings(
    frame: Frame, profile: Profile, *, capo: int = 0, limit: int = 64
) -> list[FingerAssignment]:
    """All valid finger assignments for a frame (capped), for solver/repair."""
    fretted = _fretted(frame)
    if not fretted:
        return [()]
    out: list[FingerAssignment] = []
    for combo in itertools.product(range(1, 5), repeat=len(fretted)):
        if assignment_valid(fretted, combo, profile, capo=capo):
            out.append(combo)
            if len(out) >= limit:
                break
    return out
