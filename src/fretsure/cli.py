"""CLI for the real-file MusicXML/MXL product vertical slice."""

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from fretsure.agent.arranger import ArrangementCapacityError, ensure_llm_capacity
from fretsure.importers import (
    ImportDiagnostic,
    ImportFailure,
    ImportSuccess,
    import_musicxml,
)
from fretsure.ir import MusicIR
from fretsure.llm.client import ConstantLLM, LLMClient
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION, FaithfulnessGate
from fretsure.oracle.input import (
    MAX_TEMPO_BPM as MAX_PIPELINE_TEMPO_BPM,
)
from fretsure.oracle.input import (
    MIN_TEMPO_BPM as MIN_PIPELINE_TEMPO_BPM,
)
from fretsure.pipeline import PipelineOptions, PipelineResult, run_pipeline

EXIT_OK = 0
EXIT_IMPORT_ERROR = 2
EXIT_PIPELINE_ERROR = 3
EXIT_OUTPUT_ERROR = 4


def _terminal_safe(value: object) -> str:
    """Escape non-printing Unicode so untrusted score metadata cannot control a TTY."""
    rendered: list[str] = []
    for character in str(value):
        if character.isprintable():
            rendered.append(character)
            continue
        codepoint = ord(character)
        if codepoint <= 0xFF:
            rendered.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            rendered.append(f"\\u{codepoint:04x}")
        else:
            rendered.append(f"\\U{codepoint:08x}")
    return "".join(rendered)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if (
        not math.isfinite(parsed)
        or not MIN_PIPELINE_TEMPO_BPM <= parsed <= MAX_PIPELINE_TEMPO_BPM
    ):
        raise argparse.ArgumentTypeError(
            "must be finite and in "
            f"{MIN_PIPELINE_TEMPO_BPM:g}..{MAX_PIPELINE_TEMPO_BPM:g} BPM"
        )
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fretsure-arrange",
        description=(
            "Import a supported MusicXML or MXL lead sheet, arrange it for guitar, and "
            "run the versioned playability and faithfulness checkers."
        ),
    )
    parser.add_argument("file", type=Path, metavar="SCORE")
    parser.add_argument("--llm", action="store_true", help="use the local LLM proxy")
    parser.add_argument("--trace-jsonl", type=Path, metavar="PATH")
    parser.add_argument("--n", type=_positive_int, default=4, help="best-of-N candidates")
    parser.add_argument(
        "--max-iters",
        type=_nonnegative_int,
        default=8,
        help="maximum verifier-guided repair edits per candidate",
    )
    parser.add_argument(
        "--tempo-bpm",
        type=_positive_float,
        default=None,
        help="explicitly override the source tempo",
    )
    parser.add_argument(
        "--no-critic",
        action="store_true",
        help="disable taste-only critic scoring (playability checks are unchanged)",
    )
    return parser


def _make_llm(use_llm: bool) -> tuple[LLMClient, str]:
    if use_llm:
        from fretsure.llm.client import ProxyLLM

        return ProxyLLM(), "ProxyLLM (local proxy)"
    return ConstantLLM("noop"), "ConstantLLM (deterministic offline fallback)"


def _location(diagnostic: ImportDiagnostic) -> str:
    location = diagnostic.location
    if location is None:
        return ""
    fields = (
        ("part", location.part_id),
        ("measure", location.measure),
        ("voice", location.voice),
        ("element", location.element),
        ("archive-member", location.archive_member),
    )
    rendered = ", ".join(
        f"{name}={_terminal_safe(value)}" for name, value in fields if value is not None
    )
    return f" ({rendered})" if rendered else ""


def _diagnostic_line(diagnostic: ImportDiagnostic) -> str:
    return (
        f"[{diagnostic.severity.value}] {diagnostic.code.value}"
        f"{_location(diagnostic)}: {_terminal_safe(diagnostic.message)}"
    )


def _ir_summary(ir: MusicIR) -> list[str]:
    voices = {
        voice: sum(1 for note in ir.notes if note.voice == voice)
        for voice in ("melody", "bass", "harmony")
    }
    return [
        f"  title           : {_terminal_safe(ir.meta.title)}",
        f"  key / meter     : {_terminal_safe(ir.meta.key)} / "
        f"{ir.meta.time_sig[0]}/{ir.meta.time_sig[1]}",
        f"  notes           : {len(ir.notes)} "
        f"(melody={voices['melody']}, bass={voices['bass']}, harmony={voices['harmony']})",
        f"  chord symbols   : {len(ir.chords)}",
        f"  source          : {_terminal_safe(ir.meta.source)}",
        f"  rights/license  : {_terminal_safe(ir.meta.license)}",
    ]


def _faithfulness_lines(gate: FaithfulnessGate | None) -> list[str]:
    if gate is None:
        return [
            "  unavailable — no tablature was produced",
            f"  checker {FIDELITY_CHECKER_VERSION}",
        ]
    return [
        f"  melody-F1 {gate.melody_f1:.2f}   bass-root {gate.bass_root:.2f}   "
        f"harmony {gate.harmony:.2f}",
        f"  gate {'PASS' if gate.passed else 'FAIL'}   checker {FIDELITY_CHECKER_VERSION}",
    ]


def _oracle_lines(result: PipelineResult) -> list[str]:
    oracle = result.arrangement.oracle
    if oracle is None:
        return ["  unavailable — no feasible tablature was produced"]
    if oracle.verdict == "GREEN":
        meaning = "model-relative GREEN certification; not a real-player guarantee"
    elif oracle.verdict == "AMBER":
        meaning = "NOT certified; borderline under the pessimistically tightened profile"
    else:
        meaning = "rejected by the versioned model"
    return [
        f"  {oracle.verdict} — {meaning}",
        f"  checker {oracle.checker_version}, profile {oracle.profile_version}",
        f"  profile SHA-256 {oracle.profile_fingerprint}",
        f"  input schema {oracle.input_schema_version}",
    ]


def _render_success(
    imported: ImportSuccess,
    result: PipelineResult,
    *,
    source_path: Path,
    engine: str,
    trace_path: Path | None,
) -> str:
    lines = [
        "=" * 72,
        "Fretsure — MusicXML/MXL to versioned-model-checked fingerstyle tab",
        "=" * 72,
        f"FILE              : {_terminal_safe(source_path)}",
        f"IMPORTER          : {_terminal_safe(imported.importer_version)}",
        f"SOURCE SHA-256    : {_terminal_safe(imported.sha256)}",
    ]
    if imported.provenance is not None:
        lines.append(
            f"ROOT XML SHA-256  : {_terminal_safe(imported.provenance.root_sha256)}"
        )
        if imported.provenance.root_member is not None:
            lines.append(
                f"ROOTFILE MEMBER   : {_terminal_safe(imported.provenance.root_member)}"
            )
    lines.extend(
        [
            f"LLM engine        : {_terminal_safe(engine)}",
            "",
            "IMPORT WARNINGS",
        ]
    )
    if imported.warnings:
        lines.extend(f"  {_diagnostic_line(warning)}" for warning in imported.warnings)
    else:
        lines.append("  none")
    lines.extend(["", "IR SUMMARY", *_ir_summary(imported.ir)])
    lines.extend(
        [
            f"  source tempo    : {result.source_tempo_bpm:g} bpm",
            f"  effective tempo : {result.effective_tempo_bpm:g} bpm",
            "",
            "ARRANGED TAB (high-e on top)",
        ]
    )
    if result.ascii is None:
        lines.append("  (no feasible arrangement found)")
    else:
        lines.extend(f"  {line}" for line in result.ascii.splitlines())
    lines.extend(["", "ORACLE VERDICT", *_oracle_lines(result)])
    lines.extend(["", "FAITHFULNESS TO INPUT", *_faithfulness_lines(result.faithfulness)])
    lines.extend(
        [
            "",
            "TRACE",
            f"  steps : {len(result.trace.steps)}",
            f"  JSONL : {_terminal_safe(trace_path)}"
            if trace_path is not None
            else "  JSONL : not requested",
            "=" * 72,
        ]
    )
    return "\n".join(lines)


def _write_trace(path: Path, result: PipelineResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.trace.to_jsonl(), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        imported = import_musicxml(args.file)
    except Exception as exc:  # noqa: BLE001 - a CLI boundary must not dump a traceback
        print(f"fretsure-arrange: importer failed: {_terminal_safe(exc)}", file=sys.stderr)
        return EXIT_IMPORT_ERROR

    if isinstance(imported, ImportFailure):
        print("fretsure-arrange: MusicXML import failed", file=sys.stderr)
        for diagnostic in imported.diagnostics:
            print(f"  {_diagnostic_line(diagnostic)}", file=sys.stderr)
        return EXIT_IMPORT_ERROR

    try:
        if args.llm:
            ensure_llm_capacity(imported.ir)
        llm, engine = _make_llm(args.llm)
        result = run_pipeline(
            imported.ir,
            llm,
            options=PipelineOptions(
                n=args.n,
                max_iters=args.max_iters,
                use_critic=not args.no_critic,
                tempo_override_bpm=args.tempo_bpm,
            ),
        )
    except (ArrangementCapacityError, ImportError, RuntimeError, ValueError) as exc:
        print(f"fretsure-arrange: pipeline failed: {_terminal_safe(exc)}", file=sys.stderr)
        return EXIT_PIPELINE_ERROR

    if args.trace_jsonl is not None:
        try:
            _write_trace(args.trace_jsonl, result)
        except OSError as exc:
            print(
                f"fretsure-arrange: could not write trace: {_terminal_safe(exc)}",
                file=sys.stderr,
            )
            return EXIT_OUTPUT_ERROR

    print(
        _render_success(
            imported,
            result,
            source_path=args.file,
            engine=engine,
            trace_path=args.trace_jsonl,
        )
    )
    return EXIT_OK if result.arrangement.tab is not None else EXIT_PIPELINE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
