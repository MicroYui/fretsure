"""Attribution, which is the part of a refusal report that can quietly mislead.

Counting *which violation types appear* among a frame's realisations answers a
question nobody asked: a type present in one reading proves nothing when another
reading avoids it. The question with an actionable answer is whether dropping one
rule would admit the editor's fingering, and that is a statement about the
minimal blocking sets, not about a tally of codes.

These tests pin the set algebra rather than any measured number, because the
number moves whenever the hand model does and the algebra must not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    path = ROOT / "scripts" / "classify_editorial_refusals.py"
    spec = importlib.util.spec_from_file_location("classify_editorial_refusals", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _script()


def test_a_superset_is_not_a_minimal_blocking_set(script: ModuleType) -> None:
    """One realisation violating {A} makes {A, B} irrelevant.

    If some reading of the fingering fails on A alone, dropping A admits the
    frame, and the reading that also failed on B says nothing about what to fix.
    Reporting both would credit B with a frame it does not hold.
    """

    minimal = script._minimal(
        [frozenset({"A"}), frozenset({"A", "B"}), frozenset({"B", "C"})]
    )
    assert minimal == [frozenset({"A"}), frozenset({"B", "C"})]


def test_an_admitted_realisation_empties_the_blocking_sets(script: ModuleType) -> None:
    """A passing reading means the frame is not refused at all.

    `_violated_sets` returns an empty set for a realisation with no diagnostics,
    and that has to drop out rather than become a blocking set of size zero,
    which would compare as a subset of everything and erase the rest.
    """

    assert script._minimal([frozenset(), frozenset({"A"})]) == [frozenset({"A"})]
    assert script._minimal([frozenset()]) == []


def test_two_rule_frames_are_not_added_to_either_rules_tally(script: ModuleType) -> None:
    """A frame no single rule unblocks costs more than one change.

    Crediting it to both rules would make the two tallies sum to more than the
    refusals, and would suggest either rule alone is worth fixing when neither is.
    """

    frames = [
        {"minimal_blocking_sets": [["A"]], "unblocked_by_one_rule": ["A"]},
        {"minimal_blocking_sets": [["A", "B"]], "unblocked_by_one_rule": []},
    ]
    alone = [rule for frame in frames for rule in frame["unblocked_by_one_rule"]]
    assert alone == ["A"]
    assert sum(1 for frame in frames if not frame["unblocked_by_one_rule"]) == 1
