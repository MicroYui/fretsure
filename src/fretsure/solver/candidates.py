"""Candidate (string, fret) positions for a pitch under a tuning/capo."""

from fretsure.geometry import open_pitch


def candidates(
    pitch: int, tuning: tuple[int, ...], capo: int, max_fret: int = 22
) -> list[tuple[int, int]]:
    """All (string, fret) that sound ``pitch``, in string-ascending (bass-first)
    order. ``fret`` is capo-relative; ``0 <= fret <= max_fret``."""
    out: list[tuple[int, int]] = []
    for string in range(len(tuning)):
        fret = pitch - open_pitch(string, tuning, capo)
        if 0 <= fret <= max_fret:
            out.append((string, fret))
    return out
