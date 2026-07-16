"""Verifiable simplify-to-tier loop.

Structurally the Plan 3 repair loop, but the gate is the stricter
:func:`check_tier` and the solve runs under the tier's tightened profile. The LLM
reads the playability diagnostics + the tier violations and emits edit-DSL edits
(drop inner voices, re-octave bass) until the tab meets the tier — melody
protected. On the success path (``tier_result.meets``) the output is checker-proven
to meet the requested tier; if ``max_iters`` is exhausted the last (non-meeting) tab
is returned with ``meets=False``, so callers must check it.
"""

from dataclasses import dataclass

from fretsure.agent.edit_dsl import MelodyProtected, apply_edit, parse_edit
from fretsure.agent.tools import diagnostics_to_prompt, edit_schema_prompt
from fretsure.agent.trace import Trace
from fretsure.difficulty.checker import TierResult, check_tier
from fretsure.difficulty.tiers import Tier, snapshot_tier
from fretsure.ir import Note
from fretsure.llm.client import LLMClient, extract_json
from fretsure.oracle.core import check_playability
from fretsure.oracle.input import ensure_repair_iterations, ensure_solver_input
from fretsure.solver.api import solve_fingering
from fretsure.tab import Tab

_SIMPLIFY_SYSTEM = (
    "You are simplifying a guitar arrangement to a target difficulty tier. Drop inner "
    "voices, re-octave the bass, or thin the texture to meet the tier while PRESERVING "
    "THE MELODY. Reply with only ONE JSON edit."
)


@dataclass(frozen=True)
class SimplifyResult:
    tab: Tab | None
    target: tuple[Note, ...]
    tier_result: TierResult | None
    iterations: int
    trace: Trace


def _tier_prompt(
    tab: Tab,
    tier_result: TierResult,
    target: tuple[Note, ...],
    tier: Tier,
    tempo_bpm: float,
) -> str:
    oracle = check_playability(tab, tier.profile, tempo_bpm=tempo_bpm)
    base = diagnostics_to_prompt(oracle, target, tab=tab)
    barre = "allowed" if tier.allow_barre else "NOT allowed"
    tv = "\n".join(f"  - {v}" for v in tier_result.tier_violations) or "  (none)"
    return (
        f"Target tier: {tier.name} (max position {tier.max_position}, barre {barre}, "
        f"at most {tier.max_simultaneous} notes per onset).\n{base}\nTier violations:\n{tv}"
    )


def simplify_to_tier(
    target: tuple[Note, ...],
    tier: Tier,
    tuning: tuple[int, ...],
    capo: int,
    llm: LLMClient,
    *,
    tempo_bpm: float = 90.0,
    max_iters: int = 8,
) -> SimplifyResult:
    tier = snapshot_tier(tier)
    max_iters = ensure_repair_iterations(max_iters)
    target, tuning, capo, profile, tempo_bpm, _beam = ensure_solver_input(
        target,
        tuning,
        capo,
        tier.profile,
        tempo_bpm=tempo_bpm,
    )
    tier = snapshot_tier(tier, profile=profile)
    current = tuple(sorted(target, key=lambda n: (n.onset, n.pitch)))
    trace = Trace()
    for iterations in range(max_iters + 1):
        solved = solve_fingering(current, tuning, capo, tier.profile, tempo_bpm=tempo_bpm)
        if isinstance(solved, Tab):
            tr: TierResult | None = check_tier(solved, tier, tempo_bpm=tempo_bpm)
            assert tr is not None
            trace.add("ORACLE", f"tier={tier.name} meets={tr.meets}", meets=tr.meets)
            if tr.meets:
                return SimplifyResult(solved, current, tr, iterations, trace)
            context = _tier_prompt(solved, tr, current, tier, tempo_bpm)
        else:
            tr = None
            trace.add("ORACLE", "INFEASIBLE")
            context = diagnostics_to_prompt(solved, current)

        if iterations == max_iters:
            tab = solved if isinstance(solved, Tab) else None
            return SimplifyResult(tab, current, tr, iterations, trace)

        user = f"{context}\n\n{edit_schema_prompt()}"
        try:
            reply = llm.complete(system=_SIMPLIFY_SYSTEM, user=user)
        except Exception as exc:  # noqa: BLE001 - LLM transport failure: stop gracefully
            trace.add("REASON", f"LLM call failed, stopping: {exc}")
            tab = solved if isinstance(solved, Tab) else None
            return SimplifyResult(tab, current, tr, iterations, trace)
        trace.add("REASON", reply[:200])
        try:
            edit = parse_edit(extract_json(reply))
            current = apply_edit(current, edit)
            trace.add("EDIT", f"{edit.op} pitch={edit.target_pitch}", op=edit.op)
        except MelodyProtected as exc:
            trace.add("EDIT", f"melody-protected, skipped: {exc}")
        except ValueError as exc:
            trace.add("EDIT", f"unparseable edit skipped: {exc}")

    raise AssertionError("unreachable")
