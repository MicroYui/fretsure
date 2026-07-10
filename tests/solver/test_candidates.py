from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.solver.candidates import candidates


def test_candidates_all_valid_and_sorted() -> None:
    c = candidates(64, STANDARD_TUNING, 0)  # E4
    assert c
    assert c == sorted(c)  # string ascending
    for s, f in c:
        assert 0 <= f <= 22
        assert note_pitch(s, f, STANDARD_TUNING, 0) == 64


def test_candidates_open_high_e() -> None:
    assert (5, 0) in candidates(64, STANDARD_TUNING, 0)


def test_candidates_out_of_range_empty() -> None:
    assert candidates(10, STANDARD_TUNING, 0) == []  # below every string


def test_candidates_capo_shifts() -> None:
    # capo 2: pitch 42 = low-E string (40) + capo 2 open
    assert (0, 0) in candidates(42, STANDARD_TUNING, 2)


def test_candidates_respects_max_fret() -> None:
    # a very high pitch only reachable past a low max_fret -> fewer/no candidates
    low = candidates(72, STANDARD_TUNING, 0, max_fret=3)
    for _s, f in low:
        assert f <= 3
