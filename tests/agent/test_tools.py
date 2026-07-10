from fractions import Fraction as F

from fretsure.agent.tools import (
    diagnostics_to_prompt,
    edit_schema_prompt,
    solve_and_check,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.core import OracleResult
from fretsure.oracle.diagnostics import Diagnostic
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import Infeasible
from fretsure.tab import Tab

_TARGET = (Note(F(0), F(1), 60, "melody"), Note(F(0), F(1), 48, "bass"))


def test_solve_and_check_solvable() -> None:
    tab, oracle = solve_and_check(_TARGET, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(tab, Tab)
    assert oracle is not None and oracle.verdict != "RED"


def test_solve_and_check_infeasible() -> None:
    target = (Note(F(0), F(1), 85, "melody"), Note(F(0), F(1), 86, "harmony"))
    res, oracle = solve_and_check(target, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(res, Infeasible)
    assert oracle is None


def test_diagnostics_prompt_green() -> None:
    green = OracleResult("GREEN", (), "oracle@0.1.0", "median@0.1")
    p = diagnostics_to_prompt(green, _TARGET)
    assert "GREEN" in p


def test_diagnostics_prompt_amber_lists_violations_and_target() -> None:
    amber = OracleResult(
        "AMBER",
        (Diagnostic(1, F(1), "FRET_SPAN", (0, 1), 12.3, ("drop_5th",)),),
        "oracle@0.1.0",
        "median@0.1",
    )
    p = diagnostics_to_prompt(amber, _TARGET)
    assert "AMBER" in p and "FRET_SPAN" in p and "bar 1" in p
    assert "60" in p and "48" in p  # target summary present


def test_diagnostics_prompt_infeasible() -> None:
    inf = Infeasible(F(2), "shift too fast", (60, 62))
    p = diagnostics_to_prompt(inf, _TARGET)
    assert "INFEASIBLE" in p.upper() and "2" in p


def test_edit_schema_prompt_lists_ops() -> None:
    s = edit_schema_prompt()
    for op in ("drop_note", "octave_shift", "revoice"):
        assert op in s
    assert "melody" in s.lower()
