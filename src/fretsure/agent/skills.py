"""Arranging knowledge the proposer would otherwise have to rediscover per call.

The agent already receives a style and a technique profile, both hand-authored
strings compiled into a registry.  Those describe *intent* -- what kind of
arrangement is wanted.  This module carries the other half: what the verifier
has actually been measured to accept and refuse, which is knowledge the model
cannot derive from the score in front of it and currently pays for one rejected
candidate at a time.

Two rules govern what may live here, both learned the hard way elsewhere in this
project:

* **A skill states a measured fact, and cites the measurement.**  Plausible
  advice is what a language model already has; the value here is exactly the
  part that had to be measured against this oracle.  A skill without evidence
  is a guess with extra authority.
* **A skill that stops being true is a liability, not just dead weight**, because
  it speaks in a current voice.  The proposal prompt carried "keep at most 4
  notes sounding at the same onset" for weeks after ``oracle@0.4.0`` began
  admitting six-string gestures -- a rule that cost real arrangements and that
  nothing would have flagged.  Every skill therefore names the contract version
  it was measured against, so drift is visible rather than silent.

The registry is versioned by hand, exactly as the style and technique registries
are.  It is deliberately not content-hashed: what matters is which knowledge a
run used, not that its bytes are pinned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

ARRANGEMENT_SKILL_REGISTRY_VERSION: Final = "arrangement-skill-registry@0.1.0"

_ID_RE: Final = re.compile(r"[a-z][a-z0-9-]*\Z")


@dataclass(frozen=True, slots=True)
class ArrangementSkill:
    """One measured fact about what this oracle accepts, and where it came from."""

    id: str
    guidance: str
    evidence: str
    measured_against: str

    def __post_init__(self) -> None:
        if _ID_RE.fullmatch(self.id) is None:
            raise ValueError(f"skill id must be lower-kebab-case: {self.id!r}")
        for field in ("guidance", "evidence", "measured_against"):
            if not getattr(self, field).strip():
                raise ValueError(f"skill {self.id} has an empty {field}")


_SKILLS: Final[tuple[ArrangementSkill, ...]] = (
    ArrangementSkill(
        "sustain-melody-fully",
        "Never shorten a melody note. Write every melody note for its full "
        "written value; the solver has no freedom there and will not invent any.",
        "Measured twice on the published corpus: allowing melody to release "
        "early bought zero additional accepted pieces, while holding it keeps "
        "melody-F1 invariant by construction.",
        "sustain-model@0.2.0",
    ),
    ArrangementSkill(
        "bass-may-be-shortened-by-half",
        "A bass note may be written shorter than the harmony implies, but not "
        "below half its value: the chord root must still sound when the chord "
        "arrives.",
        "The whole-score sustain ladder releases bass to half and no further; "
        "this is the guard bass_root_accuracy depends on.",
        "sustain-model@0.2.0",
    ),
    ArrangementSkill(
        "do-not-re-pluck-instead-of-holding",
        "Do not re-attack a pitch to keep it present. Write it once and hold it. "
        "Repeated attacks on one string cost right-hand repeat rate, which is a "
        "hard limit, whereas holding costs nothing the verifier charges for.",
        "Substituting re-articulation for sustained holds measured worse on the "
        "benchmark, 27 accepted down to 24, because repeated plucking exceeds "
        "the thumb's r_max_hz.",
        "oracle@0.4.0",
    ),
    ArrangementSkill(
        "six-strings-is-the-hard-ceiling",
        "Never write more simultaneous notes than the instrument has strings. "
        "Six is the ceiling and no technique raises it; a seventh note at one "
        "onset is unplayable however it is fingered.",
        "Two corpus scores demand 7 and 11 notes at a single onset and are "
        "refused by every hand model; a third demands two pitches that both "
        "need the sixth string.",
        "oracle@0.4.0",
    ),
    ArrangementSkill(
        "five-note-chords-need-a-sweep",
        "Five and six note chords are playable, but only as one right-hand "
        "gesture: the thumb sweeps a run of ADJACENT low strings while i, m and "
        "a take the top three. A chord written as five independent plucks is "
        "refused, and a sweep that skips a string is refused.",
        "An open E minor is GREEN as a thumb sweep plus three plucks and RED as "
        "six plucks. Before attack groups existed no spelling of it was "
        "playable at all.",
        "oracle@0.4.0",
    ),
    ArrangementSkill(
        "adjacent-fingers-are-the-tight-span",
        "Wide stretches are limited by ADJACENT finger pairs, not by the "
        "index-to-little span. Two notes far apart along the neck are more "
        "likely to be reachable than two notes a few frets apart that would "
        "need neighbouring fingers.",
        "At the frames where the span rule alone refuses a published score, 13 "
        "of 16 bind on a gap-1 pair and only 3 on the 1-4 pair. Raising the "
        "1-4 allowance by half changed nothing.",
        "oracle@0.4.0",
    ),
    ArrangementSkill(
        "along-neck-distance-is-what-costs",
        "Spreading a chord across strings is nearly free; spreading it along the "
        "neck is what gets refused. Prefer a wide voicing on adjacent frets over "
        "a narrow one that spans many frets.",
        "The span rule measures Euclidean fingertip distance, in which the "
        "across-string component reaches only 52.5 mm across all six strings "
        "while a few frets of neck cost more than that.",
        "oracle@0.4.0",
    ),
)

SKILLS: Final[tuple[ArrangementSkill, ...]] = _SKILLS
SKILL_IDS: Final[tuple[str, ...]] = tuple(skill.id for skill in _SKILLS)

if len(set(SKILL_IDS)) != len(SKILL_IDS):  # pragma: no cover - construction guard
    raise ValueError("arrangement skill ids must be unique")


def skill_guidance() -> str:
    """The skills as one block of prompt text, in registry order.

    Only the guidance is sent.  The evidence is for whoever maintains the
    registry and would be noise to the model, but it is what keeps a skill
    honest: a line nobody can attach a measurement to should be deleted rather
    than reworded.
    """

    return " ".join(skill.guidance for skill in _SKILLS)


__all__ = [
    "ARRANGEMENT_SKILL_REGISTRY_VERSION",
    "SKILLS",
    "SKILL_IDS",
    "ArrangementSkill",
    "skill_guidance",
]
