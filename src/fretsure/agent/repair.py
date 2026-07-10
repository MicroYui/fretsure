"""Verifier-guided repair loop — the agent spine.

oracle-as-environment, LLM-as-policy: solve the target, read the oracle's
localized diagnostics, ask the LLM for ONE edit-DSL edit, apply it (melody
protected), re-solve, and repeat until GREEN or a fixed budget. The LLM is
injected (``LLMClient``) so the whole loop is deterministic under ``FakeLLM``.
"""

from dataclasses import dataclass

from fretsure.agent.edit_dsl import MelodyProtected, apply_edit, parse_edit
from fretsure.agent.tools import diagnostics_to_prompt, edit_schema_prompt, solve_and_check
from fretsure.agent.trace import Trace
from fretsure.ir import Note
from fretsure.llm.client import LLMClient, extract_json
from fretsure.oracle.core import OracleResult
from fretsure.oracle.profiles import Profile
from fretsure.solver.api import Infeasible
from fretsure.tab import Tab

_SYSTEM = (
    "You are a fingerstyle guitar arranger. A deterministic oracle has judged an "
    "arrangement not-yet-playable and given localized diagnostics. Propose ONE minimal "
    "edit (as a single JSON object) that eases the problem while PRESERVING THE MELODY. "
    "Prefer dropping or re-octaving a harmony or bass note. Reply with only the JSON."
)


@dataclass(frozen=True)
class RepairResult:
    tab: Tab | None
    target: tuple[Note, ...]
    oracle: OracleResult | None
    infeasible: Infeasible | None
    iterations: int
    trace: Trace


def _terminal(
    solved: Tab | Infeasible,
    oracle: OracleResult | None,
    target: tuple[Note, ...],
    iterations: int,
    trace: Trace,
) -> RepairResult:
    if isinstance(solved, Tab):
        return RepairResult(solved, target, oracle, None, iterations, trace)
    return RepairResult(None, target, None, solved, iterations, trace)


def repair(
    target: tuple[Note, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    llm: LLMClient,
    *,
    tempo_bpm: float = 90.0,
    max_iters: int = 8,
) -> RepairResult:
    current = tuple(sorted(target, key=lambda n: (n.onset, n.pitch)))
    trace = Trace()
    max_iters = max(0, max_iters)
    for iterations in range(max_iters + 1):
        solved, oracle = solve_and_check(current, tuning, capo, profile, tempo_bpm=tempo_bpm)
        verdict = oracle.verdict if oracle is not None else "INFEASIBLE"
        trace.add("SOLVE", verdict, verdict=verdict)

        if isinstance(solved, Tab) and oracle is not None and oracle.verdict == "GREEN":
            trace.add("ORACLE", "GREEN — done", verdict="GREEN")
            return _terminal(solved, oracle, current, iterations, trace)
        if iterations == max_iters:
            trace.add("ORACLE", f"budget reached at {verdict}", verdict=verdict)
            return _terminal(solved, oracle, current, iterations, trace)

        if isinstance(solved, Tab):
            assert oracle is not None
            prompt_ctx: OracleResult | Infeasible = oracle
            tab_for_prompt: Tab | None = solved
        else:
            prompt_ctx = solved
            tab_for_prompt = None
        diag = diagnostics_to_prompt(prompt_ctx, current, tab=tab_for_prompt)
        user = f"{diag}\n\n{edit_schema_prompt()}"
        try:
            reply = llm.complete(system=_SYSTEM, user=user)
        except Exception as exc:  # noqa: BLE001 - LLM transport failure: stop gracefully
            trace.add("REASON", f"LLM call failed, stopping repair: {exc}")
            return _terminal(solved, oracle, current, iterations, trace)
        trace.add("REASON", reply[:200])

        try:
            edit = parse_edit(extract_json(reply))
        except ValueError as exc:
            trace.add("EDIT", f"unparseable edit skipped: {exc}")
            continue
        try:
            current = apply_edit(current, edit)
            trace.add("EDIT", f"{edit.op} pitch={edit.target_pitch}", op=edit.op)
        except MelodyProtected as exc:
            trace.add("EDIT", f"melody-protected, skipped: {exc}")

    raise AssertionError("unreachable")  # loop always returns at iterations == max_iters
