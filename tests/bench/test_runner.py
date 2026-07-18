import hashlib
import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import cast

import pytest

import fretsure.bench.report as report_module
import fretsure.bench.runner as runner_module
from fretsure.bench.artifacts import manifest_to_dict
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.precall import (
    BenchmarkPreCallConfig,
    PreCallConfigError,
    build_pre_call_config,
    current_runtime_identity,
)
from fretsure.bench.preregistration import preregistration_from_bytes
from fretsure.bench.report import ReplayMode
from fretsure.bench.runner import (
    MAX_BENCHMARK_BARS,
    MAX_BENCHMARK_ITEMS,
    BenchmarkInputError,
    BenchmarkV2Config,
    BenchReport,
    collect_benchmark_v2,
    main,
    replay_benchmark_v2,
    report_to_dict,
    run_benchmark,
)
from fretsure.llm.client import ConstantLLM
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION
from fretsure.oracle.input import ORACLE_INPUT_SCHEMA_VERSION
from fretsure.oracle.profiles import MEDIAN_HAND


class _ClosableConstant(ConstantLLM):
    def __init__(self, model_id: str, *, readable: bool = True) -> None:
        super().__init__("noop")
        self._test_model_id = model_id
        self._readable = readable
        self.closes = 0

    @property
    def model_id(self) -> str:
        if not self._readable:
            raise RuntimeError("SECRET model id getter")
        return self._test_model_id

    def close(self) -> None:
        self.closes += 1


class _ClosableFailure(_ClosableConstant):
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        raise RuntimeError("deterministic live-like failure")


def test_run_benchmark_reproducible() -> None:
    a = run_benchmark(seed=1, items=2, bars=1, llm_factory=lambda: ConstantLLM("noop"))
    b = run_benchmark(seed=1, items=2, bars=1, llm_factory=lambda: ConstantLLM("noop"))
    assert a == b


def test_run_benchmark_reports_ablation() -> None:
    r = run_benchmark(
        seed=3,
        items=2,
        bars=1,
        llm_factory=lambda: ConstantLLM("noop"),
    )
    assert isinstance(r, BenchReport)
    assert r.full.items == 2
    assert set(r.ablation) >= {"full", "-repair", "-critic", "-best_of_n"}
    assert r.checker_version.startswith("oracle@")
    assert r.fidelity_checker_version == FIDELITY_CHECKER_VERSION
    assert r.profile_fingerprint == MEDIAN_HAND.fingerprint
    assert r.input_schema_version == ORACLE_INPUT_SCHEMA_VERSION
    assert r.llm_model_id == "constant-stub"
    assert report_to_dict(r)["fidelity_checker_version"] == FIDELITY_CHECKER_VERSION
    assert report_to_dict(r)["profile_fingerprint"] == MEDIAN_HAND.fingerprint
    assert report_to_dict(r)["input_schema_version"] == ORACLE_INPUT_SCHEMA_VERSION
    assert report_to_dict(r)["llm_model_id"] == "constant-stub"

    with pytest.raises(BenchmarkInputError, match="factory returned 'constant-stub'"):
        run_benchmark(
            seed=3,
            items=1,
            bars=1,
            llm_factory=lambda: ConstantLLM("noop"),
            llm_model_id="wrong-model",
        )

    class NamedConstant(ConstantLLM):
        def __init__(self, model_id: str) -> None:
            super().__init__("noop")
            self._model_id = model_id

        @property
        def model_id(self) -> str:
            return self._model_id

    model_ids = iter(("first-model", "second-model"))
    with pytest.raises(BenchmarkInputError, match="inconsistent model ids"):
        run_benchmark(
            seed=3,
            items=1,
            bars=1,
            llm_factory=lambda: NamedConstant(next(model_ids)),
        )


def test_factory_product_is_closed_when_model_id_cannot_be_read() -> None:
    llm = _ClosableConstant("unused", readable=False)

    with pytest.raises(BenchmarkInputError, match="could not be read"):
        run_benchmark(seed=1, items=1, bars=1, llm_factory=lambda: llm)

    assert llm.closes == 1


def test_factory_product_is_closed_when_expected_model_id_mismatches() -> None:
    llm = _ClosableConstant("actual-model")

    with pytest.raises(BenchmarkInputError, match="factory returned 'actual-model'"):
        run_benchmark(
            seed=1,
            items=1,
            bars=1,
            llm_factory=lambda: llm,
            llm_model_id="expected-model",
        )

    assert llm.closes == 1


def test_factory_product_is_closed_when_arms_return_inconsistent_models() -> None:
    created: list[_ClosableConstant] = []
    model_ids = iter(("first-model", "second-model"))

    def factory() -> _ClosableConstant:
        llm = _ClosableConstant(next(model_ids))
        created.append(llm)
        return llm

    with pytest.raises(BenchmarkInputError, match="inconsistent model ids"):
        run_benchmark(seed=1, items=1, bars=1, llm_factory=factory)

    assert [llm.closes for llm in created] == [1, 1]


def test_run_benchmark_full_arranges_generated() -> None:
    # generated lead sheets (rule-stub fallback under the stub LLM) should arrange
    r = run_benchmark(seed=5, items=2, bars=1, llm_factory=lambda: ConstantLLM("noop"))
    assert r.full.green_rate > 0.0


def test_run_benchmark_stamps_one_detached_profile_snapshot() -> None:
    source_profile = replace(MEDIAN_HAND, version="bench-snapshot@0.1")
    expected_fingerprint = source_profile.fingerprint
    mutated = False

    def factory() -> ConstantLLM:
        nonlocal mutated
        if not mutated:
            object.__setattr__(source_profile, "version", "mutated@0.1")
            object.__setattr__(source_profile, "hand_span_mm", 200.0)
            mutated = True
        return ConstantLLM("noop")

    report = run_benchmark(
        seed=5,
        items=1,
        bars=1,
        llm_factory=factory,
        profile=source_profile,
    )

    assert mutated
    assert report.profile_version == "bench-snapshot@0.1"
    assert report.profile_fingerprint == expected_fingerprint


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"seed": True}, "seed"),
        ({"seed": 1 << 63}, "seed"),
        ({"items": 0}, "items"),
        ({"items": -1}, "items"),
        ({"items": True}, "items"),
        ({"items": MAX_BENCHMARK_ITEMS + 1}, "items"),
        ({"bars": 0}, "bars"),
        ({"bars": True}, "bars"),
        ({"bars": MAX_BENCHMARK_BARS + 1}, "bars"),
        ({"items": 100, "bars": 64}, "items*bars"),
        ({"paired": 1}, "paired"),
        ({"llm_model_id": ""}, "llm_model_id"),
        ({"llm_model_id": "bad\nmodel"}, "llm_model_id"),
        ({"llm_model_id": "x" * 129}, "llm_model_id"),
        ({"llm_model_id": 5}, "llm_model_id"),
    ],
)
def test_benchmark_rejects_invalid_or_unbounded_controls_before_factory(
    changes: dict[str, object],
    field: str,
) -> None:
    calls = 0

    def factory() -> ConstantLLM:
        nonlocal calls
        calls += 1
        return ConstantLLM("noop")

    kwargs: dict[str, object] = {
        "seed": 1,
        "items": 1,
        "bars": 1,
        "paired": False,
    }
    kwargs.update(changes)
    with pytest.raises(BenchmarkInputError) as caught:
        run_benchmark(llm_factory=factory, **kwargs)  # type: ignore[arg-type]

    assert caught.value.field == field
    assert calls == 0


def _v2_config() -> BenchmarkV2Config:
    return BenchmarkV2Config(
        family_count=1,
        bars=1,
        bootstrap_repetitions=11,
        sign_flip_draws=11,
    )


def _canonical_bytes(path: Path) -> dict[str, bytes]:
    return {value.name: value.read_bytes() for value in (path / "canonical").iterdir()}


@lru_cache
def _formal_pre_call(*, input_token_ceiling: int = 272_000):
    root = Path(__file__).resolve().parents[2]
    preregistration = preregistration_from_bytes(
        (root / "docs/experiments/2026-07-17-benchmark-v2-prereg.json").read_bytes()
    )
    envelope_path = (
        root
        / "docs/experiments/2026-07-18-gpt-5.6-sol-formal-billing-envelope.json"
    )
    envelope = cast(dict[str, object], json.loads(envelope_path.read_text()))
    ceilings = cast(
        dict[str, object], envelope["billable_token_ceiling_per_attempt"]
    )
    for field in (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        ceilings[field] = input_token_ceiling
    envelope_bytes = canonical_json_bytes(envelope)
    pricing_sha256 = cast(str, envelope["pricing_contract_raw_sha256"])
    return build_pre_call_config(
        preregistration,
        collection_attempt=1,
        execution_git_sha="1" * 40,
        uv_lock_sha256="2" * 64,
        analysis_binding_kind="analysis_module_sha256",
        analysis_code_sha256="3" * 64,
        runtime_identity=current_runtime_identity(),
        formal_billing_envelope=envelope,
        formal_billing_envelope_raw_sha256=hashlib.sha256(envelope_bytes).hexdigest(),
        cost_status="available",
        currency="USD",
        maximum_spend_microunits=538_865_486_400,
        pricing_contract_sha256=pricing_sha256,
        formal_budget_gate_raw_sha256="4" * 64,
    )


def test_v2_config_rejects_seeds_that_cannot_fit_frozen_report_offsets() -> None:
    with pytest.raises(BenchmarkInputError) as bootstrap:
        BenchmarkV2Config(
            bootstrap_seed=runner_module._max_v2_bootstrap_seed(1) + 1,
        )
    assert bootstrap.value.field == "bootstrap_seed"

    with pytest.raises(BenchmarkInputError) as sign_flip:
        BenchmarkV2Config(
            sign_flip_seed=runner_module.MAX_BENCHMARK_V2_SIGN_FLIP_SEED + 1,
        )
    assert sign_flip.value.field == "sign_flip_seed"


def test_live_scalar_config_fails_before_creating_output(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    with pytest.raises(BenchmarkInputError) as caught:
        collect_benchmark_v2(
            config=replace(_v2_config(), stub=False),
            output_dir=output,
        )

    assert caught.value.field == "pre_call_config"
    assert not output.exists()


def test_formal_request_guard_enforces_utf8_plus_256_and_output_ceiling() -> None:
    pre_call = _formal_pre_call(input_token_ceiling=300)
    guard = runner_module._formal_observation_request_guard(pre_call)

    guard("é".encode() * 20, b"abcd", 16_384)
    with pytest.raises(runner_module.FormalRequestCeilingError) as input_error:
        guard(b"x" * 45, b"", 1)
    assert input_error.value.field == "input_tokens"
    assert input_error.value.upper_bound == 301
    assert input_error.value.ceiling == 300

    with pytest.raises(runner_module.FormalRequestCeilingError) as output_error:
        guard(b"", b"", 16_385)
    assert output_error.value.field == "output_tokens"


def test_live_requires_exact_spend_before_client_or_output(
    tmp_path: Path,
) -> None:
    pre_call = _formal_pre_call()
    calls = 0

    def forbidden() -> ConstantLLM:
        nonlocal calls
        calls += 1
        raise AssertionError("authorization failure must precede client creation")

    for index, supplied in enumerate((None, 538_865_486_399, 538_865_486_401)):
        output = tmp_path / f"unauthorized-{index}"
        with pytest.raises(ValueError):
            collect_benchmark_v2(
                pre_call_config=pre_call,
                output_dir=output,
                agent_llm_factory=forbidden,
                raw_llm_factory=forbidden,
                authorized_maximum_spend_microunits=supplied,
            )
        assert not output.exists()

    forged_wire = pre_call.to_dict()
    forged_wire["budget"]["cost"]["maximum_spend_microunits"] = 1  # type: ignore[index]
    forged_wire["billing_envelope"]["raw_sha256"] = "0" * 64  # type: ignore[index]
    forged = BenchmarkPreCallConfig(canonical_json_bytes(forged_wire))
    forged_output = tmp_path / "forged"
    with pytest.raises(PreCallConfigError):
        collect_benchmark_v2(
            pre_call_config=forged,
            output_dir=forged_output,
            agent_llm_factory=forbidden,
            raw_llm_factory=forbidden,
            authorized_maximum_spend_microunits=1,
        )
    assert not forged_output.exists()
    assert calls == 0


def test_live_like_collection_is_raw_only_then_two_replays_are_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _formal_pre_call()
    base = runner_module.build_benchmark_v2_context(
        replace(
            _v2_config(),
            requested_model_id=pre_call.requested_model_id,
            run_id="task9-live-like-raw-only",
        )
    )
    live_like = replace(
        base,
        config=replace(base.config, stub=False),
        pre_call_config=pre_call,
    )
    monkeypatch.setattr(
        runner_module,
        "build_benchmark_v2_live_context",
        lambda _config: live_like,
    )
    created: list[_ClosableFailure] = []

    def factory() -> _ClosableFailure:
        client = _ClosableFailure(live_like.requested_model_id)
        created.append(client)
        return client

    source = tmp_path / "source"
    result = collect_benchmark_v2(
        pre_call_config=pre_call,
        output_dir=source,
        agent_llm_factory=factory,
        raw_llm_factory=factory,
        authorized_maximum_spend_microunits=pre_call.maximum_spend_microunits,
    )

    assert result.report is None
    assert set(_canonical_bytes(source)) == {
        "blobs.jsonl",
        "config.json",
        "observations.json",
        "receipt.json",
        "rows.jsonl",
    }
    assert [client.closes for client in created] == [1, 1]

    outputs = [tmp_path / "replay-a", tmp_path / "replay-b"]
    for output in outputs:
        replay_benchmark_v2(
            config_path=source / "canonical/config.json",
            receipt_path=source / "canonical/receipt.json",
            rows_path=source / "canonical/rows.jsonl",
            blobs_path=source / "canonical/blobs.jsonl",
            observations_path=source / "canonical/observations.json",
            output_dir=output,
        )
    assert _canonical_bytes(outputs[0]) == _canonical_bytes(outputs[1])


def test_formal_guard_failure_writes_terminal_abort_receipt_without_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _formal_pre_call(input_token_ceiling=256)
    base = runner_module.build_benchmark_v2_context(
        replace(
            _v2_config(),
            requested_model_id=pre_call.requested_model_id,
            run_id="task9-guard-abort",
        )
    )
    live_like = replace(
        base,
        config=replace(base.config, stub=False),
        pre_call_config=pre_call,
    )
    monkeypatch.setattr(
        runner_module,
        "build_benchmark_v2_live_context",
        lambda _config: live_like,
    )
    clients: list[_ClosableConstant] = []

    def factory() -> _ClosableConstant:
        client = _ClosableConstant(live_like.requested_model_id)
        clients.append(client)
        return client

    output = tmp_path / "guard-abort"
    with pytest.raises(runner_module.FormalRequestCeilingError):
        collect_benchmark_v2(
            pre_call_config=pre_call,
            output_dir=output,
            agent_llm_factory=factory,
            raw_llm_factory=factory,
            authorized_maximum_spend_microunits=pre_call.maximum_spend_microunits,
        )

    receipt = json.loads((output / "abort-receipt.json").read_text())
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["reason_code"] == "formal_billing_envelope_violation"
    assert not (output / "canonical").exists()
    assert (output / "journal.jsonl").read_bytes() == b""
    assert [client.closes for client in clients] == [1, 1]


def test_post_call_row_conversion_failure_writes_terminal_abort_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _formal_pre_call()
    base = runner_module.build_benchmark_v2_context(
        replace(
            _v2_config(),
            requested_model_id=pre_call.requested_model_id,
            run_id="task9-post-call-abort",
        )
    )
    live_like = replace(
        base,
        config=replace(base.config, stub=False),
        pre_call_config=pre_call,
    )
    monkeypatch.setattr(
        runner_module,
        "build_benchmark_v2_live_context",
        lambda _config: live_like,
    )
    monkeypatch.setattr(
        runner_module,
        "completed_unit_to_row_bundle",
        lambda *_args: (_ for _ in ()).throw(
            runner_module.ReportInputError("fixture", "injected post-call failure")
        ),
    )

    def factory() -> _ClosableFailure:
        return _ClosableFailure(live_like.requested_model_id)

    output = tmp_path / "post-call-abort"
    with pytest.raises(runner_module.ReportInputError):
        collect_benchmark_v2(
            pre_call_config=pre_call,
            output_dir=output,
            agent_llm_factory=factory,
            raw_llm_factory=factory,
            authorized_maximum_spend_microunits=pre_call.maximum_spend_microunits,
        )

    receipt = json.loads((output / "abort-receipt.json").read_text())
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["reason_code"] == "report_input_integrity_failure"
    assert (output / "journal.jsonl").stat().st_size > 0
    assert not (output / "canonical").exists()


def test_stub_rejects_client_factories_before_call_or_output(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"

    def forbidden() -> ConstantLLM:
        raise AssertionError("stub factory must not be called")

    with pytest.raises(BenchmarkInputError) as caught:
        collect_benchmark_v2(
            config=_v2_config(),
            output_dir=output,
            agent_llm_factory=forbidden,
            raw_llm_factory=forbidden,
        )

    assert caught.value.field == "llm_factory"
    assert not output.exists()


def test_preregistered_mixed_context_is_self_contained_and_replayable() -> None:
    root = Path(__file__).resolve().parents[2]
    preregistration = preregistration_from_bytes(
        (root / "docs/experiments/2026-07-17-benchmark-v2-prereg.json").read_bytes()
    )

    context = runner_module.build_benchmark_v2_preregistered_context(preregistration)
    restored = runner_module.benchmark_v2_context_from_manifest(context.manifest)

    assert len(context.plan.items) == 503
    assert context.manifest.run_id == (
        "benchmark-v2-formal-20260717-stub-attempt-001"
    )
    assert len(context.manifest.expected_rows) == 503 * 21
    assert {item.layer for item in context.plan.items} == {
        "procedural",
        "public_classical",
        "public_midi",
    }
    assert context.manifest == restored.manifest
    assert canonical_json_bytes(manifest_to_dict(context.manifest))
    assert context.manifest.parameters["corpus"] == {
        "source": "parameters.preregistration.wire.corpus.snapshot"
    }
    assert context.manifest.parameters["experiment"] == {
        "source": "parameters.preregistration.wire.schedule"
    }
    analysis = cast(dict[str, object], context.manifest.parameters["analysis"])
    execution = cast(dict[str, object], context.manifest.parameters["execution"])
    assert analysis["binding_kind"] == "preregistered_analysis_contract_sha256"
    assert analysis["analysis_contract_sha256"] == context.manifest.analysis_code_sha256
    assert execution == {
        "analysis_binding": {
            "kind": "preregistered_analysis_contract_sha256",
            "sha256": context.manifest.analysis_code_sha256,
        },
        "execution_git_sha": None,
        "mode": "stub",
    }


def test_formal_context_round_trip_embeds_the_billing_envelope() -> None:
    pre_call = _formal_pre_call()

    context = runner_module.build_benchmark_v2_live_context(pre_call)
    restored = runner_module.benchmark_v2_context_from_manifest(context.manifest)

    assert restored.manifest == context.manifest
    assert restored.pre_call_config == pre_call
    embedded = cast(dict[str, object], context.manifest.parameters["pre_call"])
    envelope = cast(dict[str, object], embedded["billing_envelope"])
    assert envelope["raw_sha256"] == (
        "5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d"
    )


def test_v2_client_creation_closes_first_client_when_second_factory_fails() -> None:
    context = runner_module.build_benchmark_v2_context(_v2_config())
    agent = _ClosableConstant(context.requested_model_id)

    def fail() -> ConstantLLM:
        raise RuntimeError("raw factory failed")

    with pytest.raises(RuntimeError, match="raw factory failed"):
        runner_module._create_v2_clients(context, lambda: agent, fail)

    assert agent.closes == 1


def test_v2_client_creation_rejects_manifest_model_drift_and_closes_both() -> None:
    context = runner_module.build_benchmark_v2_context(_v2_config())
    agent = _ClosableConstant("different-model")
    raw = _ClosableConstant("different-model")

    with pytest.raises(BenchmarkInputError) as caught:
        runner_module._create_v2_clients(context, lambda: agent, lambda: raw)

    assert caught.value.field == "llm_model_id"
    assert agent.closes == raw.closes == 1


def test_v2_stub_collection_is_byte_identical_and_full_replay_matches(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    replay = tmp_path / "replay"
    config = _v2_config()

    collected = collect_benchmark_v2(config=config, output_dir=first)
    collect_benchmark_v2(config=config, output_dir=second)

    assert collected.receipt.observed_rows == 21
    assert collected.receipt.observed_calls > 0
    assert set(_canonical_bytes(first)) == {
        "blobs.jsonl",
        "config.json",
        "observations.json",
        "receipt.json",
        "report.json",
        "report.md",
        "rows.jsonl",
    }
    assert _canonical_bytes(first) == _canonical_bytes(second)
    observations = json.loads((first / "canonical" / "observations.json").read_text())
    assert observations["calls"]
    assert all(value["elapsed_microseconds"] is None for value in observations["calls"])
    assert all(set(value["usage"].values()) == {None} for value in observations["calls"])

    replayed = replay_benchmark_v2(
        config_path=first / "canonical" / "config.json",
        receipt_path=first / "canonical" / "receipt.json",
        rows_path=first / "canonical" / "rows.jsonl",
        blobs_path=first / "canonical" / "blobs.jsonl",
        observations_path=first / "canonical" / "observations.json",
        output_dir=replay,
    )
    assert replayed.report == collected.report
    assert (replay / "canonical" / "report.json").read_bytes() == (
        first / "canonical" / "report.json"
    ).read_bytes()
    assert (replay / "canonical" / "report.md").read_bytes() == (
        first / "canonical" / "report.md"
    ).read_bytes()


def test_v2_resume_from_committed_unit_matches_one_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interrupted = tmp_path / "interrupted"
    expected = tmp_path / "expected"
    config = _v2_config()
    original = runner_module.ArtifactStore.commit_unit
    injected = False

    def stop_after_commit(
        store: runner_module.ArtifactStore,
        schedule_index: int,
        row: object,
        blobs: object,
    ) -> None:
        nonlocal injected
        original(store, schedule_index, row, blobs)  # type: ignore[arg-type]
        if schedule_index == 3 and not injected:
            injected = True
            raise RuntimeError("injected callback stop")

    monkeypatch.setattr(runner_module.ArtifactStore, "commit_unit", stop_after_commit)
    with pytest.raises(RuntimeError, match="injected callback stop"):
        collect_benchmark_v2(config=config, output_dir=interrupted)
    assert not (interrupted / "canonical").exists()

    monkeypatch.setattr(runner_module.ArtifactStore, "commit_unit", original)
    collect_benchmark_v2(config=config, output_dir=interrupted, resume=True)
    collect_benchmark_v2(config=config, output_dir=expected)
    assert _canonical_bytes(interrupted) == _canonical_bytes(expected)


def test_v2_fast_replay_is_explicit_and_does_not_call_solver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "fast"
    collect_benchmark_v2(config=_v2_config(), output_dir=source)
    monkeypatch.setattr(
        report_module,
        "solve_fingering",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("solver called")),
    )

    result = replay_benchmark_v2(
        config_path=source / "canonical" / "config.json",
        receipt_path=source / "canonical" / "receipt.json",
        rows_path=source / "canonical" / "rows.jsonl",
        blobs_path=source / "canonical" / "blobs.jsonl",
        observations_path=source / "canonical" / "observations.json",
        output_dir=output,
        mode=ReplayMode.FAST_REAGGREGATE,
    )

    assert result.report.mode is ReplayMode.FAST_REAGGREGATE
    wire = json.loads((output / "canonical" / "report.json").read_text())
    assert wire["mode"] == "fast_reaggregate"
    assert wire["replay_policy"] == "explicit_trust_of_stored_scores"


def test_v2_cli_requires_explicit_collection_mode_and_output(tmp_path: Path) -> None:
    output = tmp_path / "cli"
    assert (
        main(
            [
                "--stub",
                "--output-dir",
                str(output),
                "--bootstrap-repetitions",
                "11",
                "--sign-flip-draws",
                "11",
            ]
        )
        == 0
    )
    with pytest.raises(SystemExit) as caught:
        main(["--output-dir", str(tmp_path / "missing-mode")])
    assert caught.value.code == 2

    with pytest.raises(SystemExit) as missing_pre_call:
        main(["--live", "--output-dir", str(tmp_path / "missing-pre-call")])
    assert missing_pre_call.value.code == 2

    with pytest.raises(SystemExit) as wrong_binding:
        main(
            [
                "--stub",
                "--pre-call-config",
                str(tmp_path / "not-read.json"),
                "--output-dir",
                str(tmp_path / "wrong-binding"),
            ]
        )
    assert wrong_binding.value.code == 2


def test_v2_cli_redacts_live_integrity_abort_as_exit_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pre_call_path = tmp_path / "pre-call.json"
    pre_call_path.write_bytes(b"ignored by fixture")
    monkeypatch.setattr(
        runner_module,
        "pre_call_config_from_bytes",
        lambda _data: _formal_pre_call(),
    )
    monkeypatch.setattr(
        runner_module,
        "collect_benchmark_v2",
        lambda **_kwargs: (_ for _ in ()).throw(
            runner_module.LLMIntegrityError("stable integrity failure")
        ),
    )

    assert (
        main(
            [
                "--live",
                "--pre-call-config",
                str(pre_call_path),
                "--authorized-maximum-spend-microunits",
                "538865486400",
                "--output-dir",
                str(tmp_path / "output"),
            ]
        )
        == 1
    )
    assert capsys.readouterr().err == "stable integrity failure\n"
