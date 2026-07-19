#!/usr/bin/env python3
"""Provider-free full-scale gate for the Task 9 operational runner.

The ordinary ``fretsure-bench --stub`` path validates corpus and report
determinism.  This gate instead drives the exact operational coordinator,
four-worker admission, lane WAL, READY merge, SIGINT drain, and resume path.
Its context remains a deterministic stub, so it cannot construct a proxy
client or make a provider request.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final, cast

import fretsure.bench.runner as runner_module
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.precall import (
    BENCHMARK_PRE_CALL_CONFIG_VERSION,
    FORMAL_SHORT_CONTEXT_MAX_INPUT_TOKENS,
    BenchmarkPreCallConfig,
)
from fretsure.bench.preregistration import (
    BenchmarkPreregistration,
    preregistration_from_bytes,
)
from fretsure.bench.runner import BenchmarkV2Context, BenchmarkV2Result
from fretsure.llm.client import MAX_PROXY_OUTPUT_TOKENS

DEFAULT_PREREGISTRATION: Final = (
    Path(__file__).resolve().parents[1]
    / "docs/experiments/2026-07-18-benchmark-v2-operational-prereg.json"
)


def _object(value: object, field: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def build_operational_stub_context(
    preregistration: BenchmarkPreregistration,
) -> BenchmarkV2Context:
    """Attach only the operational controls needed by the offline gate.

    This deliberately is not a valid live pre-call authorization.  The base
    context stays ``stub=True`` and therefore selects only the deterministic
    failing LLM stub while exercising the production operational coordinator.
    """

    base = runner_module.build_benchmark_v2_preregistered_context(preregistration)
    wire = preregistration.to_dict()
    budgets = _object(wire.get("budgets"), "preregistration.budgets")
    provider = _object(
        budgets.get("provider_policy"),
        "preregistration.budgets.provider_policy",
    )
    contract = _object(
        wire.get("collection_execution"),
        "preregistration.collection_execution",
    )
    timeout = provider.get("request_timeout_seconds")
    overhead = provider.get("recorded_attempt_elapsed_overhead_seconds")
    if type(timeout) is not float or timeout <= 0.0:
        raise ValueError("operational request timeout must be a positive exact float")
    if type(overhead) is not float or overhead < 0.0:
        raise ValueError("operational elapsed overhead must be a nonnegative exact float")

    model_id = base.requested_model_id
    offline_binding = BenchmarkPreCallConfig(
        canonical_json_bytes(
            {
                "billing_envelope": {
                    "wire": {
                        "billable_token_ceiling_per_attempt": {
                            "cache_creation_input_tokens": (FORMAL_SHORT_CONTEXT_MAX_INPUT_TOKENS),
                            "cache_read_input_tokens": FORMAL_SHORT_CONTEXT_MAX_INPUT_TOKENS,
                            "input_tokens": FORMAL_SHORT_CONTEXT_MAX_INPUT_TOKENS,
                            "output_tokens": MAX_PROXY_OUTPUT_TOKENS,
                        }
                    }
                },
                "collection_execution": {
                    "contract": contract,
                    "request_timeout_seconds": timeout,
                },
                "model": {
                    "allowed_returned_model_id": model_id,
                    "requested_model_id": model_id,
                },
                "preregistration": wire,
                "run_id": base.plan.run_id,
                "schema": BENCHMARK_PRE_CALL_CONFIG_VERSION,
            }
        )
    )
    return replace(base, pre_call_config=offline_binding)


def collect_operational_stub(
    *,
    preregistration: BenchmarkPreregistration,
    output_dir: Path,
    resume: bool = False,
) -> BenchmarkV2Result:
    """Run the provider-free full schedule through operational concurrency."""

    context = build_operational_stub_context(preregistration)
    with runner_module._deferred_operational_sigint() as stop_requested:
        return runner_module._collect_operational_concurrent(
            context=context,
            output_dir=output_dir,
            resume=resume,
            agent_llm_factory=None,
            raw_llm_factory=None,
            stop_requested=stop_requested,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="task9-operational-stub-gate")
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREGISTRATION)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        preregistration = preregistration_from_bytes(args.prereg.read_bytes())
        result = collect_operational_stub(
            preregistration=preregistration,
            output_dir=args.output_dir,
            resume=args.resume,
        )
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "report_sha256": None,
                "run_id": result.receipt.run_id,
                "status": result.receipt.status.value,
            },
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
