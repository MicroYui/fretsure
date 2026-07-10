"""One-command demo: arrange a sample lead sheet into a provably-playable tab.

``fretsure-demo`` runs the whole product on a bundled (procedurally-generated,
contamination-proof) lead sheet and prints the input, the arranged ASCII tab, the
oracle verdict, and the faithfulness gate. It defaults to a deterministic stub LLM
so it always works offline; ``--llm`` uses the local proxy for a real arrangement.
The guarantee shown is the product's core claim: the printed tab is GREEN, i.e.
provably playable by the pessimistic hand profile, with melody/bass preserved.
"""

import argparse
from dataclasses import dataclass

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult, arrange
from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.ir import MusicIR
from fretsure.llm.client import ConstantLLM, LLMClient
from fretsure.metrics.fidelity import FaithfulnessGate, faithfulness
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.render.ascii import render_ascii

SAMPLE_SEED = 7


def sample_ir(*, seed: int = SAMPLE_SEED, bars: int = 4) -> MusicIR:
    """A bundled, seeded lead sheet — never existed before, so uncontaminated."""
    return generate_leadsheet(GenConfig(key="C", bars=bars, seed=seed))


@dataclass(frozen=True)
class DemoResult:
    result: ArrangeResult
    gate: FaithfulnessGate | None


def run_demo(
    ir: MusicIR, llm: LLMClient, *, profile: Profile = MEDIAN_HAND, n: int = 4
) -> DemoResult:
    res = arrange(ir, ArrangeGoal(), llm, profile=profile, n=n)
    gate = faithfulness(ir, res.tab) if res.tab is not None else None
    return DemoResult(res, gate)


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


def render_demo(demo: DemoResult, ir: MusicIR, *, engine: str) -> str:
    res, gate = demo.result, demo.gate
    lines = [
        "=" * 66,
        "  Fretsure demo — lead sheet -> provably-playable fingerstyle tab",
        "=" * 66,
        f"LLM engine        : {engine}",
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
        "GREEN": f"provably playable by the pessimistic hand (a conservative tightening of {prof})",
        "AMBER": f"borderline — passes optimistic but NOT the pessimistic tightening of {prof}",
        "RED": "unplayable (this should never be returned)",
    }[verdict]
    lines += [
        "",
        "ORACLE VERDICT",
        f"  {verdict} — {proven}",
        f"  checker {res.oracle.checker_version}, profile {res.oracle.profile_version}",
    ]
    if gate is not None:
        lines += [
            "",
            "FAITHFULNESS TO INPUT",
            f"  melody-F1 {gate.melody_f1:.2f}   bass-root {gate.bass_root:.2f}   "
            f"harmony {gate.harmony:.2f}   gate {'PASS' if gate.passed else 'FAIL'}",
        ]
    lines += [
        "",
        "WHAT THIS PROVES",
        "  The tab above is not an LLM opinion: a deterministic, millimetre-geometry",
        "  oracle checked every note/frame against a conservatively-tightened hand and",
        f"  returned {verdict}. The LLM only proposed intent; playability is machine-certified.",
        "=" * 66,
    ]
    return "\n".join(lines)


def _make_llm(use_llm: bool) -> tuple[LLMClient, str]:
    if use_llm:
        from fretsure.llm.client import ProxyLLM

        return ProxyLLM(), "ProxyLLM (local proxy, real arrangement)"
    return ConstantLLM("noop"), "ConstantLLM (deterministic stub, offline)"


def main() -> None:
    parser = argparse.ArgumentParser(prog="fretsure-demo")
    parser.add_argument("--llm", action="store_true", help="use the local LLM proxy")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument("--bars", type=int, default=4)
    parser.add_argument("--n", type=int, default=4, help="best-of-N candidates")
    args = parser.parse_args()

    ir = sample_ir(seed=args.seed, bars=args.bars)
    llm, engine = _make_llm(args.llm)
    demo = run_demo(ir, llm, n=args.n)
    print(render_demo(demo, ir, engine=engine))


if __name__ == "__main__":
    main()
