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

from fretsure.agent.edit_dsl import (
    InvalidEditTarget,
    MelodyProtected,
    apply_edit,
    parse_edit,
)
from fretsure.agent.tools import diagnostics_to_prompt, edit_schema_prompt
from fretsure.agent.trace import (
    Trace,
    TraceEvent,
    edit_detail,
    edit_trace_payload,
    infeasible_detail,
    infeasible_trace_payload,
    tab_checkpoint,
    target_checkpoint,
)
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


def _checkpoint_digest(checkpoint: dict[str, object]) -> str:
    digest = checkpoint["sha256"]
    assert type(digest) is str
    return digest


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
        target_state = target_checkpoint(current)
        target_digest = _checkpoint_digest(target_state)
        solved = solve_fingering(current, tuning, capo, tier.profile, tempo_bpm=tempo_bpm)
        if isinstance(solved, Tab):
            tr: TierResult | None = check_tier(solved, tier, tempo_bpm=tempo_bpm)
            assert tr is not None
            terminal_reason = (
                "TIER_MET"
                if tr.meets
                else "BUDGET_EXHAUSTED"
                if iterations == max_iters
                else None
            )
            trace.add(
                "ORACLE",
                f"The deterministic tier checker returned meets={tr.meets} for {tier.name}.",
                event="TIER_CHECKED",
                iteration=iterations,
                tier=tier.name,
                meets=tr.meets,
                tier_violation_count=len(tr.tier_violations),
                target_sha256=target_digest,
                tab_checkpoint=tab_checkpoint(solved),
                terminal_reason=terminal_reason,
            )
            if tr.meets:
                return SimplifyResult(solved, current, tr, iterations, trace)
            context = _tier_prompt(solved, tr, current, tier, tempo_bpm)
        else:
            tr = None
            trace.add(
                "SOLVE",
                infeasible_detail(solved),
                event="SOLVER_RETURNED_NO_TAB",
                iteration=iterations,
                status="NO_TAB",
                target_sha256=target_digest,
                target_note_count=len(current),
                infeasible=infeasible_trace_payload(solved),
                terminal_reason=(
                    "BUDGET_EXHAUSTED" if iterations == max_iters else None
                ),
            )
            context = diagnostics_to_prompt(solved, current)

        if iterations == max_iters:
            tab = solved if isinstance(solved, Tab) else None
            return SimplifyResult(tab, current, tr, iterations, trace)

        user = f"{context}\n\n{edit_schema_prompt()}"
        try:
            reply = llm.complete(system=_SIMPLIFY_SYSTEM, user=user)
        except Exception:  # noqa: BLE001 - public trace records only a stable code
            trace.add(
                "REASON",
                "The model call failed; simplification stopped without transport details.",
                event="MODEL_CALL_FAILED",
                iteration=iterations + 1,
                reason_code="LLM_TRANSPORT_FAILURE",
                target_sha256=target_digest,
            )
            tab = solved if isinstance(solved, Tab) else None
            return SimplifyResult(tab, current, tr, iterations, trace)
        try:
            edit_object = extract_json(reply)
        except (AttributeError, TypeError, ValueError):
            trace.add(
                "EDIT",
                "The model response did not contain an accepted JSON edit.",
                event="MODEL_EDIT_INVALID",
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
                iteration=iterations + 1,
                trigger="MODEL_EDIT_INVALID",
                target_checkpoint=target_state,
            )
            continue

        try:
            updated = apply_edit(current, edit)
        except InvalidEditTarget:
            trace.add(
                "EDIT",
                "The model JSON did not satisfy the edit schema.",
                event="MODEL_EDIT_INVALID",
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
                iteration=iterations + 1,
                trigger="MODEL_EDIT_INVALID",
                target_checkpoint=target_state,
            )
            continue
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
                detail = "The targeted edit was applied to the simplification state."
        structured_edit = edit_trace_payload(edit)
        trace.add(
            "REASON",
            edit_detail(edit),
            event="REPAIR_EDIT_PROPOSED",
            iteration=iterations + 1,
            edit=structured_edit,
            based_on_diagnostic_codes=["TIER_GATE"],
        )
        after_state = target_checkpoint(updated)
        after_digest = _checkpoint_digest(after_state)
        trace.add(
            "EDIT",
            detail,
            event=trace_event,
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
            "Run the bounded solver and tier checker again for the post-edit target.",
            event="RECHECK_STARTED",
            iteration=iterations + 1,
            trigger=trace_event,
            target_checkpoint=after_state,
        )

    raise AssertionError("unreachable")
