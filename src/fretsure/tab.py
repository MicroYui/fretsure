"""Guitar tablature representation.

A :class:`Tab` is a fully-fingered arrangement the oracle *verifies*. The
fingering solver (Plan 2) reverse-searches assignments; this module only
represents and (de)serializes them.
"""

import json
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

RightFinger = Literal["p", "i", "m", "a"]  # thumb / index / middle / ring


@dataclass(frozen=True)
class TabNote:
    onset: Fraction
    duration: Fraction
    string: int  # 0 = lowest-pitched string (6th, low E) .. 5 = highest (1st, high E)
    fret: int  # 0 = open
    left_finger: int  # 0..4, 0 = open
    right_finger: RightFinger


@dataclass(frozen=True)
class Tab:
    notes: tuple[TabNote, ...]
    tuning: tuple[int, ...]  # open-string MIDI, low -> high
    capo: int  # capo fret, 0 = none


# A Frame is the set of TabNotes sounding at one onset — the oracle/solver unit.
Frame = tuple[TabNote, ...]


def frames(tab: Tab) -> list[Frame]:
    """Group notes by onset (ascending); within a frame, sort by string."""
    by_onset: dict[Fraction, list[TabNote]] = {}
    for n in tab.notes:
        by_onset.setdefault(n.onset, []).append(n)
    return [
        tuple(sorted(by_onset[onset], key=lambda n: n.string))
        for onset in sorted(by_onset)
    ]


def tab_to_json(tab: Tab) -> str:
    obj = {
        "tuning": list(tab.tuning),
        "capo": tab.capo,
        "notes": [
            {
                "onset": f"{n.onset.numerator}/{n.onset.denominator}",
                "duration": f"{n.duration.numerator}/{n.duration.denominator}",
                "string": n.string,
                "fret": n.fret,
                "left_finger": n.left_finger,
                "right_finger": n.right_finger,
            }
            for n in tab.notes
        ],
    }
    return json.dumps(obj)


def tab_from_json(s: str) -> Tab:
    obj = json.loads(s)
    notes = tuple(
        TabNote(
            onset=Fraction(d["onset"]),
            duration=Fraction(d["duration"]),
            string=int(d["string"]),
            fret=int(d["fret"]),
            left_finger=int(d["left_finger"]),
            right_finger=d["right_finger"],
        )
        for d in obj["notes"]
    )
    return Tab(notes=notes, tuning=tuple(int(x) for x in obj["tuning"]), capo=int(obj["capo"]))
