"""The gate has to say what produced it, not only what came out.

Every frozen gate artifact before 2026-07-31 was a bare aggregate: accepted,
examples, outcome counts, infeasible reasons. No mode, no beam, no profile, no
checker version. `evaluate_example` even carries a comment saying the capo mode
"is reported as a separate number, never replacing the recorded one" — the code
knew it mattered and the artifact did not record it.

The cost was concrete. 146/292 was quoted across sessions with no way to tell it
was the `--choose-capo` figure, then re-measured in the other mode, found not to
reproduce, and publicly "corrected" to a number that was measuring something
else. A single field in the summary would have made that impossible.

So this pins the summary's self-description rather than any measured value.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "evaluate_repertoire_playability",
        ROOT / "scripts" / "evaluate_repertoire_playability.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_repertoire_playability"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _script()


def _tiny_corpus(path: Path) -> Path:
    """Two easy notes, so the test measures the report shape and not the solver."""

    path.write_text(
        json.dumps(
            {
                "examples": [
                    {
                        "id": "tiny",
                        "split": "train",
                        "capo": 0,
                        "tuning": [40, 45, 50, 55, 59, 64],
                        "tempo_bpm": 90,
                        "time_signature": [4, 4],
                        "notes": [
                            {"onset": [0, 1], "duration": [1, 1], "pitch": 60,
                             "voice": "melody"},
                            {"onset": [1, 1], "duration": [1, 1], "pitch": 62,
                             "voice": "melody"},
                        ],
                        "annotations": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _summary(script: ModuleType, tmp_path: Path, *extra: str) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    corpus = _tiny_corpus(tmp_path / "corpus.json")
    out = tmp_path / "summary.json"
    code = script.main(
        ["--corpus", str(corpus), "--summary-only", "--output", str(out), *extra]
    )
    assert code == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_summary_records_the_mode_that_produced_it(
    script: ModuleType, tmp_path: Path
) -> None:
    """The field whose absence cost a day.

    `--choose-capo` lets a refused score try other capo positions. It is a real
    arranging liberty and the two modes are different metrics; a summary that
    does not say which one it is cannot be compared with anything.
    """

    fixed = _summary(script, tmp_path / "a")
    laddered = _summary(script, tmp_path / "b", "--choose-capo")

    assert fixed["configuration"]["choose_capo"] is False
    assert laddered["configuration"]["choose_capo"] is True


def test_the_summary_records_what_it_was_measured_against(
    script: ModuleType, tmp_path: Path
) -> None:
    """Mode is not the only thing a bare aggregate loses.

    A gate number is meaningless without the hand model and the checker that
    produced it, and both move often in this project. The profile fingerprint is
    the one that cannot be faked by a version string someone forgot to bump.
    """

    summary = _summary(script, tmp_path)

    configuration = summary["configuration"]
    assert configuration["beam"] == 16
    assert isinstance(configuration["profile_version"], str)
    assert len(configuration["profile_fingerprint"]) == 64
    assert configuration["corpus"] == ["corpus.json"]

    versions = summary["versions"]
    for field in ("checker", "fingering_solver", "score_solver", "left_hand_model"):
        assert isinstance(versions[field], str) and versions[field]


def test_the_summary_still_carries_the_aggregate(
    script: ModuleType, tmp_path: Path
) -> None:
    """Self-description is added, not substituted for the result."""

    aggregate = _summary(script, tmp_path)["aggregate"]

    assert aggregate["examples"] == 1
    assert aggregate["accepted"] in (0, 1)
    assert sum(aggregate["outcome_counts"].values()) == 1
    assert "baseline_subset" in aggregate


def test_the_three_solver_modes_are_selected_and_recorded(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three modes, three metrics, and the summary has to say which ran.

    `--escalate` reaches `solve_fingering_score_with_escalation`, which nothing
    outside tests had ever called although it has been implemented and documented
    for a week. It was added to the gate on 2026-07-31 with no test that it picks
    the right solver -- a flag that silently falls back to the plain path would
    report a ceiling measurement as a floor one.
    """

    called: list[str] = []

    from fretsure.solver.api import Infeasible, InfeasibleCode

    def _spy(name: str):
        def solver(*_args: object, **_kwargs: object) -> Infeasible:
            called.append(name)
            return Infeasible(InfeasibleCode.NO_FRAME_CONFIG, None, "spy", ())

        return solver

    monkeypatch.setattr(script, "solve_fingering_score", _spy("plain"))
    monkeypatch.setattr(script, "solve_fingering_score_choosing_capo", _spy("capo"))
    monkeypatch.setattr(script, "solve_fingering_score_with_escalation", _spy("escalate"))

    for flags, expected in (
        ((), "plain"),
        (("--choose-capo",), "capo"),
        (("--escalate",), "escalate"),
        # Escalation already contains the capo ladder, so it wins when both are
        # given rather than the two quietly composing into something neither
        # flag names.
        (("--choose-capo", "--escalate"), "escalate"),
    ):
        called.clear()
        summary = _summary(script, tmp_path / f"m{len(called)}{''.join(flags)}", *flags)
        assert called == [expected], (flags, called)
        assert summary["configuration"]["escalate"] is ("--escalate" in flags)
        assert summary["configuration"]["choose_capo"] is ("--choose-capo" in flags)
