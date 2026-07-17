"""Verifier-guided repair loop — the agent spine.

oracle-as-environment, LLM-as-policy: solve the target, read the oracle's
localized diagnostics, ask the LLM for ONE edit-DSL edit, apply it (melody
protected), re-solve, and repeat until GREEN or a fixed budget. The LLM is
injected (``LLMClient``) so the whole loop is deterministic under ``FakeLLM``.
"""

from dataclasses import dataclass

from fretsure.agent.edit_dsl import MelodyProtected, apply_edit, parse_edit
from fretsure.agent.model_calls import ModelCallScopeFactory, model_call_scope
from fretsure.agent.tools import diagnostics_to_prompt, edit_schema_prompt, solve_and_check
from fretsure.agent.trace import (
    Trace,
    TraceEvent,
    edit_detail,
    edit_trace_payload,
    infeasible_detail,
    infeasible_trace_payload,
    oracle_detail,
    oracle_trace_payload,
    target_checkpoint,
)
from fretsure.ir import Note
from fretsure.llm.client import LLMClient, LLMIntegrityError, extract_json
from fretsure.oracle.core import OracleResult
from fretsure.oracle.input import (
    MAX_AGENT_REPAIR_ITERS,
    ensure_repair_iterations,
    ensure_solver_input,
)
from fretsure.oracle.profiles import Profile
from fretsure.solver.api import Infeasible
from fretsure.tab import Tab

_SYSTEM = (
    "You are a fingerstyle guitar arranger. A deterministic oracle has judged an "
    "arrangement not-yet-playable and given localized diagnostics. Propose ONE minimal "
    "edit (as a single JSON object) that eases the problem while PRESERVING THE MELODY. "
    "Prefer dropping or re-octaving a harmony or bass note. Reply with only the JSON."
)

REPAIR_MAX_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class RepairSnapshot:
    """One immutable solver/oracle state in a verifier-guided repair run."""

    iteration: int
    target: tuple[Note, ...]
    tab: Tab | None
    oracle: OracleResult | None
    infeasible: Infeasible | None

    def __post_init__(self) -> None:
        if type(self.iteration) is not int or not 0 <= self.iteration <= MAX_AGENT_REPAIR_ITERS:
            raise ValueError("iteration must be an exact bounded integer")
        if type(self.target) is not tuple:
            raise ValueError("target must be an exact tuple")
        if (self.tab is None) == (self.infeasible is None):
            raise ValueError("a repair snapshot must contain exactly one solver result")
        if self.tab is None and self.oracle is not None:
            raise ValueError("an infeasible snapshot cannot contain an oracle result")
        if self.tab is not None and self.oracle is None:
            raise ValueError("a tab snapshot requires its oracle result")

    @property
    def solved(self) -> Tab | Infeasible:
        """Return the exact solver result represented by this checkpoint."""

        if self.tab is not None:
            return self.tab
        assert self.infeasible is not None
        return self.infeasible


@dataclass(frozen=True, slots=True)
class RepairResult:
    """Terminal repair result with an explicit iteration-zero counterfactual."""

    tab: Tab | None
    target: tuple[Note, ...]
    oracle: OracleResult | None
    infeasible: Infeasible | None
    iterations: int
    trace: Trace
    iteration_zero: RepairSnapshot
    terminal: RepairSnapshot
    model_calls: int
    solve_calls: int

    def __post_init__(self) -> None:
        if type(self.iterations) is not int or not 0 <= self.iterations <= MAX_AGENT_REPAIR_ITERS:
            raise ValueError("iterations must be an exact bounded integer")
        if type(self.model_calls) is not int or not 0 <= self.model_calls <= MAX_AGENT_REPAIR_ITERS:
            raise ValueError("model_calls must be an exact bounded integer")
        if (
            type(self.solve_calls) is not int
            or not 1 <= self.solve_calls <= MAX_AGENT_REPAIR_ITERS + 1
        ):
            raise ValueError("solve_calls must be an exact bounded integer")
        if self.solve_calls != self.iterations + 1:
            raise ValueError("solve_calls must include every iteration from zero to terminal")
        if self.model_calls not in (self.iterations, self.iterations + 1):
            raise ValueError("model_calls disagree with the bounded repair progression")
        if self.iteration_zero.iteration != 0:
            raise ValueError("iteration_zero must be the first solve")
        if self.terminal.iteration != self.iterations or self.terminal.target != self.target:
            raise ValueError("terminal snapshot must match compatibility fields")
        if (
            self.terminal.tab != self.tab
            or self.terminal.oracle != self.oracle
            or self.terminal.infeasible != self.infeasible
        ):
            raise ValueError("terminal snapshot must match the terminal solver result")


def _terminal(
    solved: Tab | Infeasible,
    oracle: OracleResult | None,
    target: tuple[Note, ...],
    iterations: int,
    trace: Trace,
    iteration_zero: RepairSnapshot,
    model_calls: int,
    solve_calls: int,
) -> RepairResult:
    terminal = _snapshot_repair_state(solved, oracle, target, iterations)
    if isinstance(solved, Tab):
        return RepairResult(
            solved,
            target,
            oracle,
            None,
            iterations,
            trace,
            iteration_zero,
            terminal,
            model_calls,
            solve_calls,
        )
    return RepairResult(
        None,
        target,
        None,
        solved,
        iterations,
        trace,
        iteration_zero,
        terminal,
        model_calls,
        solve_calls,
    )


def _snapshot_repair_state(
    solved: Tab | Infeasible,
    oracle: OracleResult | None,
    target: tuple[Note, ...],
    iteration: int,
) -> RepairSnapshot:
    if isinstance(solved, Tab):
        return RepairSnapshot(iteration, target, solved, oracle, None)
    return RepairSnapshot(iteration, target, None, None, solved)


def _checkpoint_digest(checkpoint: dict[str, object]) -> str:
    digest = checkpoint["sha256"]
    assert type(digest) is str
    return digest


def _diagnostic_codes(result: OracleResult | Infeasible) -> list[str]:
    if isinstance(result, Infeasible):
        return [result.code.value]
    return list(dict.fromkeys(item.violation_type for item in result.diagnostics))


def repair(
    target: tuple[Note, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    llm: LLMClient,
    *,
    tempo_bpm: float = 90.0,
    beats_per_bar: int = 4,
    max_iters: int = 8,
    candidate_index: int | None = None,
    call_scope_factory: ModelCallScopeFactory | None = None,
) -> RepairResult:
    max_iters = ensure_repair_iterations(max_iters)
    target, tuning, capo, profile, tempo_bpm, _beam = ensure_solver_input(
        target,
        tuning,
        capo,
        profile,
        tempo_bpm=tempo_bpm,
    )
    current = tuple(sorted(target, key=lambda n: (n.onset, n.pitch)))
    trace = Trace()
    iteration_zero: RepairSnapshot | None = None
    model_calls = 0
    solve_calls = 0
    for iterations in range(max_iters + 1):
        target_state = target_checkpoint(current)
        target_digest = _checkpoint_digest(target_state)
        solved, oracle = solve_and_check(
            current,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )
        solve_calls += 1
        if iterations == 0:
            iteration_zero = _snapshot_repair_state(solved, oracle, current, 0)
        if isinstance(solved, Tab):
            trace.add(
                "SOLVE",
                "Solver returned a tablature candidate.",
                event="SOLVER_RETURNED_TAB",
                candidate_index=candidate_index,
                iteration=iterations,
                status="TAB",
                target_sha256=target_digest,
                target_note_count=len(current),
            )
            assert oracle is not None
            terminal_reason = (
                "GREEN"
                if oracle.verdict == "GREEN"
                else "BUDGET_EXHAUSTED"
                if iterations == max_iters
                else None
            )
            trace.add(
                "ORACLE",
                oracle_detail(oracle),
                event="PLAYABILITY_CHECKED",
                candidate_index=candidate_index,
                iteration=iterations,
                **oracle_trace_payload(
                    oracle,
                    solved,
                    terminal_reason=terminal_reason,
                ),
            )
        else:
            terminal_reason = "BUDGET_EXHAUSTED" if iterations == max_iters else None
            trace.add(
                "SOLVE",
                infeasible_detail(solved),
                event="SOLVER_RETURNED_NO_TAB",
                candidate_index=candidate_index,
                iteration=iterations,
                status="NO_TAB",
                target_sha256=target_digest,
                target_note_count=len(current),
                infeasible=infeasible_trace_payload(solved),
                terminal_reason=terminal_reason,
            )

        if isinstance(solved, Tab) and oracle is not None and oracle.verdict == "GREEN":
            assert iteration_zero is not None
            return _terminal(
                solved,
                oracle,
                current,
                iterations,
                trace,
                iteration_zero,
                model_calls,
                solve_calls,
            )
        if iterations == max_iters:
            assert iteration_zero is not None
            return _terminal(
                solved,
                oracle,
                current,
                iterations,
                trace,
                iteration_zero,
                model_calls,
                solve_calls,
            )

        if isinstance(solved, Tab):
            assert oracle is not None
            prompt_ctx: OracleResult | Infeasible = oracle
            tab_for_prompt: Tab | None = solved
        else:
            prompt_ctx = solved
            tab_for_prompt = None
        diag = diagnostics_to_prompt(prompt_ctx, current, tab=tab_for_prompt)
        user = f"{diag}\n\n{edit_schema_prompt()}"
        model_calls += 1
        try:
            with model_call_scope(
                call_scope_factory,
                stage="repair",
                stage_ordinal=iterations,
                candidate_index=candidate_index,
            ):
                reply = llm.complete(system=_SYSTEM, user=user, max_tokens=REPAIR_MAX_TOKENS)
        except LLMIntegrityError:
            raise
        except Exception:  # noqa: BLE001 - public trace records only a stable code
            trace.add(
                "REASON",
                "The model call failed; repair stopped without exposing transport details.",
                event="MODEL_CALL_FAILED",
                candidate_index=candidate_index,
                iteration=iterations + 1,
                reason_code="LLM_TRANSPORT_FAILURE",
                target_sha256=target_digest,
            )
            assert iteration_zero is not None
            return _terminal(
                solved,
                oracle,
                current,
                iterations,
                trace,
                iteration_zero,
                model_calls,
                solve_calls,
            )

        try:
            edit_object = extract_json(reply)
        except (AttributeError, TypeError, ValueError):
            trace.add(
                "EDIT",
                "The model response did not contain an accepted JSON edit.",
                event="MODEL_EDIT_INVALID",
                candidate_index=candidate_index,
                iteration=iterations + 1,
                edit=None,
                status="unparseable",
                reason_code="NO_JSON_OBJECT",
                before_target_sha256=target_digest,
                after_target_sha256=target_digest,
                state_changed=False,
            )
            trace.add(
                "RECHECK",
                "Recheck the unchanged target after the rejected model response.",
                event="RECHECK_STARTED",
                candidate_index=candidate_index,
                iteration=iterations + 1,
                trigger="MODEL_EDIT_INVALID",
                target_checkpoint=target_state,
            )
            continue
        try:
            edit = parse_edit(edit_object)
        except (TypeError, ValueError):
            trace.add(
                "EDIT",
                "The model JSON did not satisfy the edit schema.",
                event="MODEL_EDIT_INVALID",
                candidate_index=candidate_index,
                iteration=iterations + 1,
                edit=None,
                status="unparseable",
                reason_code="INVALID_EDIT_SCHEMA",
                before_target_sha256=target_digest,
                after_target_sha256=target_digest,
                state_changed=False,
            )
            trace.add(
                "RECHECK",
                "Recheck the unchanged target after the rejected edit.",
                event="RECHECK_STARTED",
                candidate_index=candidate_index,
                iteration=iterations + 1,
                trigger="MODEL_EDIT_INVALID",
                target_checkpoint=target_state,
            )
            continue

        structured_edit = edit_trace_payload(edit)
        trace.add(
            "REASON",
            edit_detail(edit),
            event="REPAIR_EDIT_PROPOSED",
            candidate_index=candidate_index,
            iteration=iterations + 1,
            edit=structured_edit,
            based_on_diagnostic_codes=_diagnostic_codes(prompt_ctx),
        )
        try:
            updated = apply_edit(current, edit)
        except MelodyProtected:
            updated = current
            trace_event: TraceEvent = "EDIT_REJECTED"
            status = "rejected"
            reason_code: str | None = "MELODY_PROTECTED"
            detail = "The edit was rejected because melody notes are protected."
        else:
            if updated == current:
                trace_event = "EDIT_REJECTED"
                status = "noop"
                reason_code = "TARGET_NOT_FOUND"
                detail = "The edit matched no target note and changed no state."
            else:
                trace_event = "EDIT_APPLIED"
                status = "applied"
                reason_code = None
                detail = "The targeted edit was applied to the repair state."
        after_state = target_checkpoint(updated)
        after_digest = _checkpoint_digest(after_state)
        trace.add(
            "EDIT",
            detail,
            event=trace_event,
            candidate_index=candidate_index,
            iteration=iterations + 1,
            edit=structured_edit,
            status=status,
            reason_code=reason_code,
            before_target_sha256=target_digest,
            after_target_sha256=after_digest,
            state_changed=updated != current,
        )
        current = updated
        trace.add(
            "RECHECK",
            "Run the bounded solver and oracle again for the post-edit target.",
            event="RECHECK_STARTED",
            candidate_index=candidate_index,
            iteration=iterations + 1,
            trigger=trace_event,
            target_checkpoint=after_state,
        )

    raise AssertionError("unreachable")  # loop always returns at iterations == max_iters
