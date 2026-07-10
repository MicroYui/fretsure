"""Agent-computer interface (ACI): the load-bearing seam between the LLM policy
and the deterministic environment.

Two pieces carry the weight (per SWE-agent): the *diagnostic format* fed to the
LLM (localized bar/beat/violation/overage/suggested relaxations + the current
target), and the compact *edit schema* it replies in. ``solve_and_check`` wraps
the Plan 2 solver + Plan 1 oracle.
"""

from fretsure.geometry import note_pitch
from fretsure.ir import Note
from fretsure.oracle.core import OracleResult, check_playability
from fretsure.oracle.profiles import Profile
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab


def solve_and_check(
    target: tuple[Note, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    tempo_bpm: float = 90.0,
) -> tuple[Tab | Infeasible, OracleResult | None]:
    solved = solve_fingering(target, tuning, capo, profile, tempo_bpm=tempo_bpm)
    if isinstance(solved, Infeasible):
        return solved, None
    return solved, check_playability(solved, profile, tempo_bpm=tempo_bpm)


def _target_summary(target: tuple[Note, ...]) -> str:
    rows = [
        f"  {n.voice} pitch={n.pitch} onset={n.onset}"
        for n in sorted(target, key=lambda x: (x.onset, x.pitch))
    ]
    return "\n".join(rows)


def diagnostics_to_prompt(
    result: OracleResult | Infeasible, target: tuple[Note, ...], tab: Tab | None = None
) -> str:
    if isinstance(result, Infeasible):
        head = (
            f"The arrangement is INFEASIBLE at onset {result.onset}: {result.reason}. "
            f"Offending pitches: {result.pitches}."
        )
    elif result.verdict == "GREEN":
        head = "The arrangement is GREEN (certified playable). No repair needed."
    else:
        rows = []
        for d in result.diagnostics:
            if tab is not None:
                offending = [
                    note_pitch(tab.notes[i].string, tab.notes[i].fret, tab.tuning, tab.capo)
                    for i in d.offending_notes
                    if i < len(tab.notes)
                ]
                where = f"pitches {offending}"
            else:
                where = f"note-indices {d.offending_notes}"
            rows.append(
                f"  bar {d.measure} beat {d.beat}: {d.violation_type} "
                f"(over {d.overage:.1f}; {where}; try {d.suggested_relaxations})"
            )
        body = "\n".join(rows) if rows else "  (none)"
        head = f"The arrangement is {result.verdict}. Oracle diagnostics:\n{body}"
    return f"{head}\n\nCurrent target notes:\n{_target_summary(target)}"


def edit_schema_prompt() -> str:
    return (
        "Reply with ONE JSON object describing a single edit that makes the "
        "arrangement more playable while PRESERVING THE MELODY. Schema:\n"
        '{"op": "drop_note|octave_shift|revoice|drop_inner", '
        '"target_onset": "<onset as a fraction string, e.g. \\"1/2\\">", '
        '"target_pitch": <MIDI int of the note to edit>, '
        '"arg": <octave_shift: +12 or -12; revoice: new MIDI pitch; otherwise 0>}\n'
        "Never target a melody note. Prefer dropping or re-octaving a harmony or "
        "bass note over touching the melody."
    )
