"""M0 end-to-end vertical slice: lead sheet -> rule proposal -> solve -> oracle -> ASCII.

Derisks the whole data flow. The proposer is a rule stub (Plan 3 replaces it with
the LLM arranger + repair loop); the guarantee here is only that the slice runs
deterministically and yields an oracle-passing (non-RED) fingerstyle tab.
"""

from dataclasses import dataclass

from fretsure.arrange.propose import propose_fingerstyle
from fretsure.ir import MusicIR
from fretsure.oracle.core import OracleResult, check_playability
from fretsure.oracle.profiles import Profile
from fretsure.render.ascii import render_ascii
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab


@dataclass(frozen=True)
class M0Result:
    tab: Tab | None
    oracle: OracleResult | None
    infeasible: Infeasible | None
    ascii: str | None


def run_m0(
    ir: MusicIR,
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    tempo_bpm: float = 90.0,
) -> M0Result:
    notes = propose_fingerstyle(ir)
    solved = solve_fingering(notes, tuning, capo, profile, tempo_bpm=tempo_bpm)
    if isinstance(solved, Infeasible):
        return M0Result(tab=None, oracle=None, infeasible=solved, ascii=None)
    oracle = check_playability(solved, profile, tempo_bpm=tempo_bpm)
    return M0Result(tab=solved, oracle=oracle, infeasible=None, ascii=render_ascii(solved))
