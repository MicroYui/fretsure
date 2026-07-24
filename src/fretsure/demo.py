"""One-command demo: arrange a sample lead sheet and check it under the model.

``fretsure-demo`` runs the whole product on a bundled (procedurally generated,
exact-item-memorization-resistant) lead sheet and prints the input, the arranged
ASCII tab, the oracle verdict, and the faithfulness gate. It defaults to a
deterministic stub LLM so it always works offline; ``--llm`` uses the local proxy
for a real arrangement.
GREEN means that the printed tab passes the pessimistically tightened, versioned
model/profile with the displayed fingering; real-player calibration remains separate.
"""

import argparse
from dataclasses import dataclass

from fretsure.agent.harness import ArrangeResult
from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.ir import MusicIR
from fretsure.llm.client import ConstantLLM, LLMClient, managed_llm_client
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION, FaithfulnessGate
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.pipeline import PipelineOptions, run_pipeline
from fretsure.render.ascii import render_ascii

SAMPLE_SEED = 7


def sample_ir(*, seed: int = SAMPLE_SEED, bars: int = 4) -> MusicIR:
    """A bundled, seeded lead sheet — never existed before, so uncontaminated."""
    return generate_leadsheet(GenConfig(key="C", bars=bars, seed=seed))


@dataclass(frozen=True)
class DemoResult:
    result: ArrangeResult
    gate: FaithfulnessGate | None
    source_tempo_bpm: float | None = None
    effective_tempo_bpm: float | None = None


def run_demo(
    ir: MusicIR,
    llm: LLMClient,
    *,
    profile: Profile = MEDIAN_HAND,
    n: int = 1,
    incremental_agent: bool = False,
) -> DemoResult:
    pipeline = run_pipeline(
        ir,
        llm,
        options=PipelineOptions(profile=profile, n=n),
        incremental_agent=incremental_agent,
    )
    return DemoResult(
        pipeline.arrangement,
        pipeline.faithfulness,
        pipeline.source_tempo_bpm,
        pipeline.effective_tempo_bpm,
    )


def _fmt_input(ir: MusicIR) -> str:
    mel = [n for n in ir.notes if n.voice == "melody"]
    chords = "  ".join(f"{c.symbol}@{c.onset}" for c in ir.chords)
    melody = " ".join(str(n.pitch) for n in mel)
    return (
        f"  key {ir.meta.key}  {ir.meta.time_sig[0]}/{ir.meta.time_sig[1]}  "
        f"tempo {ir.meta.tempo_bpm:g}\n"
        f"  chords : {chords}\n"
        f"  melody : {melody}  (MIDI)"
    )


def _faithfulness_score(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"


def render_demo(demo: DemoResult, ir: MusicIR, *, engine: str) -> str:
    res, gate = demo.result, demo.gate
    source_tempo = (
        ir.meta.tempo_bpm if demo.source_tempo_bpm is None else demo.source_tempo_bpm
    )
    effective_tempo = source_tempo if demo.effective_tempo_bpm is None else demo.effective_tempo_bpm
    lines = [
        "=" * 66,
        "  Fretsure demo — lead sheet -> versioned-model-checked fingerstyle tab",
        "=" * 66,
        f"LLM engine        : {engine}",
        f"Source tempo      : {source_tempo:g} bpm",
        f"Effective tempo   : {effective_tempo:g} bpm",
        "",
        "INPUT (lead sheet)",
        _fmt_input(ir),
        "",
        "ARRANGED TAB (high-e on top)",
    ]
    if res.tab is None or res.oracle is None:
        lines.append("  (no feasible arrangement found)")
        return "\n".join(lines)

    lines.append("\n".join("  " + ln for ln in render_ascii(res.tab).splitlines()))
    verdict = res.oracle.verdict
    prof = res.oracle.profile_version
    proven = {
        "GREEN": f"passes the pessimistically tightened versioned model/profile ({prof})",
        "AMBER": f"borderline — passes optimistic but NOT the pessimistic tightening of {prof}",
        "RED": "rejected by the versioned model (this should never be returned)",
    }[verdict]
    lines += [
        "",
        "ORACLE VERDICT",
        f"  {verdict} — {proven}",
        f"  checker {res.oracle.checker_version}, profile {res.oracle.profile_version}",
        f"  profile SHA-256 {res.oracle.profile_fingerprint}",
        f"  input schema {res.oracle.input_schema_version}",
    ]
    if gate is not None:
        evaluated = ", ".join(gate.evaluated_dimensions) or "none"
        unavailable = ", ".join(gate.unavailable_dimensions) or "none"
        lines += [
            "",
            "FAITHFULNESS TO INPUT",
            f"  melody-F1 {_faithfulness_score(gate.melody_f1)}   "
            f"bass-root {_faithfulness_score(gate.bass_root)}   "
            f"harmony {_faithfulness_score(gate.harmony)}",
            f"  available-dimension gate {'PASS' if gate.passed else 'FAIL'} "
            f"({len(gate.evaluated_dimensions)}/3 evaluated)",
            f"  evaluated: {evaluated}; unavailable: {unavailable}",
            f"  checker {FIDELITY_CHECKER_VERSION}",
        ]
    certified = {
        "GREEN": "This is a model-relative GREEN certification, not a real-player guarantee.",
        "AMBER": "The oracle did NOT certify this tab: it is borderline under the "
        "pessimistically tightened profile and needs more repair or a human check.",
        "RED": "The oracle rejected this tab under the model (it should never be returned).",
    }[verdict]
    lines += [
        "",
        "WHAT THIS ESTABLISHES UNDER THE MODEL" if verdict == "GREEN" else "WHAT THIS MEANS",
        "  The proposal path does not decide feasibility: a deterministic oracle checked",
        "  every note/frame against simplified geometry and limited timing/rate predicates and",
        f"  returned {verdict}. {certified}",
        "=" * 66,
    ]
    return "\n".join(lines)


def _make_llm(use_llm: bool) -> tuple[LLMClient, str]:
    if use_llm:
        from fretsure.llm.client import ProxyLLM

        proxy = ProxyLLM()
        return proxy, f"ProxyLLM ({proxy.model_id} via local proxy; real arrangement)"
    stub = ConstantLLM("noop")
    return stub, f"ConstantLLM ({stub.model_id}; deterministic stub, offline)"


def main() -> None:
    parser = argparse.ArgumentParser(prog="fretsure-demo")
    parser.add_argument("--llm", action="store_true", help="use the local LLM proxy")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument("--bars", type=int, default=4)
    parser.add_argument("--n", type=int, default=1, help="candidate count (default: 1)")
    args = parser.parse_args()

    ir = sample_ir(seed=args.seed, bars=args.bars)
    llm, engine = _make_llm(args.llm)
    with managed_llm_client(llm):
        demo = run_demo(ir, llm, n=args.n, incremental_agent=args.llm)
    print(render_demo(demo, ir, engine=engine))


if __name__ == "__main__":
    main()
