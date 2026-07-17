"""Pure pre-call bindings for an authorized benchmark-v2 live run.

The external runner-ready gate supplies Git, lock, package, runtime, and analysis
digests.  This module validates and records those values; it never invokes Git,
searches import paths, or reads ambient repository files.
"""

from __future__ import annotations

import hashlib
import platform
import re
import sys
from dataclasses import dataclass
from typing import Final, NoReturn, cast

import fretsure
from fretsure.bench.artifacts import parse_canonical_json_bytes
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.preregistration import (
    BenchmarkPreregistration,
    preregistration_from_dict,
)
from fretsure.llm.client import LLMModelIdError, validate_llm_model_id

BENCHMARK_PRE_CALL_CONFIG_VERSION: Final = "benchmark-pre-call-config@0.1.0"
MAX_COLLECTION_ATTEMPT: Final = 999_999
SINGLE_ATTEMPT_CEILING_SCOPE: Final = "single_collection_attempt_nontransferable"
_PROMPT_BINDING_DOMAIN = b"fretsure:benchmark-pre-call-prompts@0.1.0\0"
_SCHEMA_BINDING_DOMAIN = b"fretsure:benchmark-pre-call-schemas@0.1.0\0"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ANALYSIS_KINDS = frozenset({"analysis_module_sha256", "wheel_record_sha256"})
_MAX_BUDGET = (1 << 63) - 1


class PreCallConfigError(ValueError):
    """A pre-call config is malformed or differs from its preregistration."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid benchmark pre-call config {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise PreCallConfigError(field, detail)


def _object(value: object, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or frozenset(cast(dict[object, object], value)) != keys:
        _fail(field, "must contain the exact keys")
    return cast(dict[str, object], value)


def _text(value: object, field: str, *, maximum: int = 128) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or not value.isprintable()
    ):
        _fail(field, "must be one bounded printable string")
    return value


def _sha(value: object, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(field, "must be one lowercase SHA-256 digest")
    return value


def _git_sha(value: object, field: str) -> str:
    if type(value) is not str or _GIT_SHA.fullmatch(value) is None:
        _fail(field, "must be one lowercase Git commit id")
    return value


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _MAX_BUDGET:
        _fail(field, f"must be an exact integer in {minimum}..{_MAX_BUDGET}")
    return value


def _collection_attempt(value: object, field: str = "collection_attempt") -> int:
    attempt = _integer(value, field, minimum=1)
    if attempt > MAX_COLLECTION_ATTEMPT:
        _fail(field, f"must not exceed {MAX_COLLECTION_ATTEMPT}")
    return attempt


def _attempt_run_id(formal_experiment_id: object, attempt: int) -> str:
    formal = _text(formal_experiment_id, "preregistration.run_id")
    return _text(f"{formal}-attempt-{attempt:03d}", "run_id")


def _model(value: object, field: str) -> str:
    try:
        return validate_llm_model_id(value)
    except LLMModelIdError:
        _fail(field, "must be one bounded model identifier")


def _digest(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(value)).hexdigest()


def _runtime_wire() -> dict[str, str]:
    return {
        "architecture": platform.machine(),
        "operating_system": platform.system(),
        "package_version": fretsure.__version__,
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
    }


def current_runtime_identity() -> dict[str, str]:
    """Return only the non-host runtime fields required by the frozen contract."""

    return dict(_runtime_wire())


def _prereg_components(preregistration: BenchmarkPreregistration) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    wire = preregistration.to_dict()
    model = _object(
        wire.get("model_and_prompts"),
        "preregistration.model_and_prompts",
        frozenset({"allowed_returned_model_rule", "prompts", "requested_model"}),
    )
    versions = cast(dict[str, object], wire.get("versions"))
    if type(versions) is not dict or not versions:
        _fail("preregistration.versions", "must be a nonempty exact object")
    budgets = cast(dict[str, object], wire.get("budgets"))
    if type(budgets) is not dict:
        _fail("preregistration.budgets", "must be an exact object")
    full = cast(dict[str, object], budgets.get("full_corpus"))
    if type(full) is not dict:
        _fail("preregistration.budgets.full_corpus", "must be an exact object")
    return wire, model, versions, budgets


def _prompt_bindings(model: dict[str, object]) -> dict[str, object]:
    prompts = model.get("prompts")
    if type(prompts) is not list or not prompts:
        _fail("preregistration.model_and_prompts.prompts", "must be a nonempty array")
    by_stage: dict[str, str] = {}
    for index, raw in enumerate(prompts):
        if type(raw) is not dict:
            _fail(f"preregistration.prompts[{index}]", "must be an exact object")
        prompt = cast(dict[str, object], raw)
        stage = _text(prompt.get("stage"), f"preregistration.prompts[{index}].stage")
        digest = _sha(
            prompt.get("template_sha256"),
            f"preregistration.prompts[{index}].template_sha256",
        )
        if stage in by_stage:
            _fail("preregistration.prompts", "contains a duplicate stage")
        by_stage[stage] = digest
    return dict(sorted(by_stage.items()))


def _reservation_from_prereg(
    budgets: dict[str, object],
) -> dict[str, int]:
    raw = cast(dict[str, object], budgets.get("reserve_before_next_scheduled_unit"))
    if type(raw) is not dict:
        _fail("preregistration.budgets.reservation", "must be an exact object")
    provider = cast(dict[str, object], budgets.get("provider_policy"))
    if type(provider) is not dict:
        _fail("preregistration.budgets.provider_policy", "must be an exact object")
    attempts = _integer(raw.get("attempts"), "preregistration.reservation.attempts", minimum=1)
    request_timeout = provider.get("request_timeout_seconds")
    retry_backoff = provider.get("retry_backoff_seconds")
    if type(request_timeout) is int:
        exact_request_timeout = float(request_timeout)
    elif type(request_timeout) is float:
        exact_request_timeout = request_timeout
    else:
        _fail("preregistration.provider_policy.request_timeout_seconds", "must be positive")
    if exact_request_timeout <= 0:
        _fail("preregistration.provider_policy.request_timeout_seconds", "must be positive")
    if type(retry_backoff) is not list or any(
        type(value) not in (int, float) for value in retry_backoff
    ):
        _fail("preregistration.provider_policy.retry_backoff_seconds", "must be numeric")
    logical_calls = _integer(
        raw.get("logical_calls"), "preregistration.reservation.logical_calls", minimum=1
    )
    wall_seconds = attempts * exact_request_timeout + logical_calls * sum(
        float(value) for value in retry_backoff
    )
    return {
        "attempt_reserved_output_tokens": _integer(
            raw.get("requested_output_tokens"),
            "preregistration.reservation.requested_output_tokens",
            minimum=1,
        )
        * 3,
        "attempts": attempts,
        "logical_calls": logical_calls,
        "requested_output_tokens": _integer(
            raw.get("requested_output_tokens"),
            "preregistration.reservation.requested_output_tokens",
            minimum=1,
        ),
        "response_text_bytes": _integer(
            raw.get("response_text_bytes"),
            "preregistration.reservation.response_text_bytes",
            minimum=1,
        ),
        "transport_response_bytes": _integer(
            raw.get("transport_response_bytes"),
            "preregistration.reservation.transport_response_bytes",
            minimum=1,
        ),
        "recorded_provider_call_elapsed_microseconds": int(wall_seconds * 1_000_000),
    }


def _maximum_budget_from_prereg(
    budgets: dict[str, object],
) -> dict[str, int]:
    full = cast(dict[str, object], budgets.get("full_corpus"))
    if type(full) is not dict:
        _fail("preregistration.budgets.full_corpus", "must be an exact object")
    wall_seconds = _integer(
        budgets.get("recorded_provider_call_elapsed_ceiling_seconds"),
        "preregistration.budgets.recorded_provider_call_elapsed_ceiling_seconds",
        minimum=1,
    )
    return {
        "max_attempt_reserved_output_tokens": _integer(
            full.get("attempt_reserved_output_tokens"),
            "preregistration.budgets.full_corpus.attempt_reserved_output_tokens",
            minimum=1,
        ),
        "max_attempts": _integer(
            full.get("maximum_attempts"),
            "preregistration.budgets.full_corpus.maximum_attempts",
            minimum=1,
        ),
        "max_logical_calls": _integer(
            full.get("logical_calls_total"),
            "preregistration.budgets.full_corpus.logical_calls_total",
            minimum=1,
        ),
        "max_requested_output_tokens": _integer(
            full.get("requested_output_tokens_total"),
            "preregistration.budgets.full_corpus.requested_output_tokens_total",
            minimum=1,
        ),
        "max_response_text_bytes": _integer(
            full.get("response_text_bytes"),
            "preregistration.budgets.full_corpus.response_text_bytes",
            minimum=1,
        ),
        "max_transport_response_bytes": _integer(
            full.get("transport_response_bytes"),
            "preregistration.budgets.full_corpus.transport_response_bytes",
            minimum=1,
        ),
        "max_recorded_provider_call_elapsed_microseconds": wall_seconds * 1_000_000,
    }


def _validate_cost(value: object) -> dict[str, object]:
    cost = _object(
        value,
        "budget.cost",
        frozenset(
            {
                "currency",
                "maximum_spend_microunits",
                "pricing_contract_sha256",
                "status",
            }
        ),
    )
    status = cost.get("status")
    if status == "cost_contract_unavailable":
        if any(
            cost.get(field) is not None
            for field in ("currency", "maximum_spend_microunits", "pricing_contract_sha256")
        ):
            _fail("budget.cost", "unavailable cost fields must be null")
        return cost
    if status != "available":
        _fail("budget.cost.status", "must be available or cost_contract_unavailable")
    _text(cost.get("currency"), "budget.cost.currency", maximum=8)
    _integer(
        cost.get("maximum_spend_microunits"),
        "budget.cost.maximum_spend_microunits",
        minimum=1,
    )
    _sha(cost.get("pricing_contract_sha256"), "budget.cost.pricing_contract_sha256")
    return cost


@dataclass(frozen=True, slots=True)
class BenchmarkPreCallConfig:
    """One canonical live pre-call config, including its preregistration."""

    wire_json: bytes

    def __post_init__(self) -> None:
        if type(self.wire_json) is not bytes:
            _fail("wire_json", "must be exact bytes")
        parsed = parse_canonical_json_bytes(self.wire_json)
        if type(parsed) is not dict:
            _fail("wire_json", "must encode one canonical object")

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], parse_canonical_json_bytes(self.wire_json))

    @property
    def preregistration(self) -> BenchmarkPreregistration:
        return preregistration_from_dict(self.to_dict()["preregistration"])

    @property
    def analysis_code_sha256(self) -> str:
        execution = cast(dict[str, object], self.to_dict()["execution"])
        binding = cast(dict[str, object], execution["analysis_binding"])
        return cast(str, binding["sha256"])

    @property
    def requested_model_id(self) -> str:
        model = cast(dict[str, object], self.to_dict()["model"])
        return cast(str, model["requested_model_id"])

    @property
    def collection_attempt(self) -> int:
        return cast(int, self.to_dict()["collection_attempt"])

    @property
    def run_id(self) -> str:
        return cast(str, self.to_dict()["run_id"])

    @property
    def allowed_returned_model_id(self) -> str:
        model = cast(dict[str, object], self.to_dict()["model"])
        return cast(str, model["allowed_returned_model_id"])

    @property
    def has_priced_attempt_ceiling(self) -> bool:
        budget = cast(dict[str, object], self.to_dict()["budget"])
        cost = cast(dict[str, object], budget["cost"])
        return cost["status"] == "available"


def _validate_wire(value: object) -> BenchmarkPreCallConfig:
    obj = _object(
        value,
        "$",
        frozenset(
            {
                "budget",
                "collection_attempt",
                "contract_bindings",
                "execution",
                "mode",
                "model",
                "preregistration",
                "preregistration_raw_sha256",
                "run_id",
                "schema",
            }
        ),
    )
    if obj["schema"] != BENCHMARK_PRE_CALL_CONFIG_VERSION:
        _fail("schema", "has the wrong version")
    if obj["mode"] != "live_collection":
        _fail("mode", "must equal live_collection")
    preregistration = preregistration_from_dict(obj["preregistration"])
    prereg_wire, prereg_model, versions, prereg_budgets = _prereg_components(preregistration)
    prereg_sha = hashlib.sha256(preregistration.wire_json).hexdigest()
    if _sha(obj["preregistration_raw_sha256"], "preregistration_raw_sha256") != prereg_sha:
        _fail("preregistration_raw_sha256", "does not bind the embedded preregistration")
    collection_attempt = _collection_attempt(obj["collection_attempt"])
    if obj["run_id"] != _attempt_run_id(
        prereg_wire.get("run_id"), collection_attempt
    ):
        _fail("run_id", "does not equal the artifact id derived from collection_attempt")

    execution = _object(
        obj["execution"],
        "execution",
        frozenset(
            {
                "analysis_binding",
                "architecture",
                "execution_git_sha",
                "operating_system",
                "package_version",
                "python_version",
                "uv_lock_sha256",
            }
        ),
    )
    _git_sha(execution["execution_git_sha"], "execution.execution_git_sha")
    _sha(execution["uv_lock_sha256"], "execution.uv_lock_sha256")
    for field in ("architecture", "operating_system", "package_version", "python_version"):
        _text(execution[field], f"execution.{field}")
    if execution["package_version"] != prereg_wire.get("package_target_version"):
        _fail("execution.package_version", "does not equal the preregistered target")
    analysis = _object(
        execution["analysis_binding"],
        "execution.analysis_binding",
        frozenset({"kind", "sha256"}),
    )
    if analysis["kind"] not in _ANALYSIS_KINDS:
        _fail("execution.analysis_binding.kind", "is unsupported")
    _sha(analysis["sha256"], "execution.analysis_binding.sha256")

    model = _object(
        obj["model"],
        "model",
        frozenset({"allowed_returned_model_id", "requested_model_id", "returned_model_rule"}),
    )
    requested = _model(model["requested_model_id"], "model.requested_model_id")
    allowed = _model(model["allowed_returned_model_id"], "model.allowed_returned_model_id")
    rule = cast(dict[str, object], prereg_model["allowed_returned_model_rule"])
    if (
        model["returned_model_rule"] != "exact_equal"
        or requested != prereg_model["requested_model"]
        or allowed != requested
        or rule != {"operator": "exact_equal", "value": requested}
    ):
        _fail("model", "does not match the preregistered exact model rule")

    prompt_templates = _prompt_bindings(prereg_model)
    bindings = _object(
        obj["contract_bindings"],
        "contract_bindings",
        frozenset(
            {
                "prompt_contract_sha256",
                "prompt_template_sha256",
                "schema_versions",
                "schema_versions_sha256",
            }
        ),
    )
    if bindings["prompt_template_sha256"] != prompt_templates:
        _fail("contract_bindings.prompt_template_sha256", "differs from preregistration")
    if _sha(
        bindings["prompt_contract_sha256"], "contract_bindings.prompt_contract_sha256"
    ) != _digest(_PROMPT_BINDING_DOMAIN, prereg_model["prompts"]):
        _fail("contract_bindings.prompt_contract_sha256", "does not bind the prompts")
    if bindings["schema_versions"] != versions:
        _fail("contract_bindings.schema_versions", "differs from preregistration")
    if _sha(
        bindings["schema_versions_sha256"], "contract_bindings.schema_versions_sha256"
    ) != _digest(_SCHEMA_BINDING_DOMAIN, versions):
        _fail("contract_bindings.schema_versions_sha256", "does not bind the schemas")

    maximum = _maximum_budget_from_prereg(prereg_budgets)
    reservation = _reservation_from_prereg(prereg_budgets)
    budget = _object(
        obj["budget"],
        "budget",
        frozenset(
            {
                *maximum,
                "ceiling_scope",
                "cost",
                "scheduled_unit_reservation",
            }
        ),
    )
    if budget["ceiling_scope"] != SINGLE_ATTEMPT_CEILING_SCOPE:
        _fail("budget.ceiling_scope", "must make every ceiling attempt-local")
    for field, preregistered in maximum.items():
        actual = _integer(budget[field], f"budget.{field}", minimum=1)
        if actual > preregistered:
            _fail(f"budget.{field}", "exceeds the preregistered ceiling")
        reservation_field = field.removeprefix("max_")
        if actual < reservation[reservation_field]:
            _fail(f"budget.{field}", "cannot reserve one complete candidate unit")
    if budget["scheduled_unit_reservation"] != reservation:
        _fail("budget.scheduled_unit_reservation", "differs from preregistration")
    _validate_cost(budget["cost"])
    return BenchmarkPreCallConfig(canonical_json_bytes(obj))


def build_pre_call_config(
    preregistration: BenchmarkPreregistration,
    *,
    collection_attempt: int,
    execution_git_sha: str,
    uv_lock_sha256: str,
    analysis_binding_kind: str,
    analysis_code_sha256: str,
    runtime_identity: dict[str, str],
    cost_status: str = "cost_contract_unavailable",
    currency: str | None = None,
    maximum_spend_microunits: int | None = None,
    pricing_contract_sha256: str | None = None,
) -> BenchmarkPreCallConfig:
    """Build a canonical config from externally accepted release bindings."""

    if type(preregistration) is not BenchmarkPreregistration:
        _fail("preregistration", "must be an exact BenchmarkPreregistration")
    prereg, model, versions, budgets = _prereg_components(preregistration)
    exact_attempt = _collection_attempt(collection_attempt)
    runtime = _object(
        runtime_identity,
        "runtime_identity",
        frozenset({"architecture", "operating_system", "package_version", "python_version"}),
    )
    maximum = _maximum_budget_from_prereg(budgets)
    reservation = _reservation_from_prereg(budgets)
    prompt_templates = _prompt_bindings(model)
    wire: dict[str, object] = {
        "budget": {
            **maximum,
            "ceiling_scope": SINGLE_ATTEMPT_CEILING_SCOPE,
            "cost": {
                "currency": currency,
                "maximum_spend_microunits": maximum_spend_microunits,
                "pricing_contract_sha256": pricing_contract_sha256,
                "status": cost_status,
            },
            "scheduled_unit_reservation": reservation,
        },
        "collection_attempt": exact_attempt,
        "contract_bindings": {
            "prompt_contract_sha256": _digest(_PROMPT_BINDING_DOMAIN, model["prompts"]),
            "prompt_template_sha256": prompt_templates,
            "schema_versions": versions,
            "schema_versions_sha256": _digest(_SCHEMA_BINDING_DOMAIN, versions),
        },
        "execution": {
            "analysis_binding": {
                "kind": analysis_binding_kind,
                "sha256": analysis_code_sha256,
            },
            **runtime,
            "execution_git_sha": execution_git_sha,
            "uv_lock_sha256": uv_lock_sha256,
        },
        "mode": "live_collection",
        "model": {
            "allowed_returned_model_id": model["requested_model"],
            "requested_model_id": model["requested_model"],
            "returned_model_rule": "exact_equal",
        },
        "preregistration": prereg,
        "preregistration_raw_sha256": hashlib.sha256(preregistration.wire_json).hexdigest(),
        "run_id": _attempt_run_id(prereg["run_id"], exact_attempt),
        "schema": BENCHMARK_PRE_CALL_CONFIG_VERSION,
    }
    return _validate_wire(wire)


def pre_call_config_from_dict(value: object) -> BenchmarkPreCallConfig:
    return _validate_wire(value)


def pre_call_config_from_bytes(data: object) -> BenchmarkPreCallConfig:
    if type(data) is not bytes:
        _fail("$", "must be exact bytes")
    try:
        value = parse_canonical_json_bytes(data)
    except ValueError as error:
        raise PreCallConfigError("$", "must be canonical benchmark JSON") from error
    return _validate_wire(value)


def validate_current_runtime(config: BenchmarkPreCallConfig) -> None:
    """Compare only package/Python/OS/architecture; external digests stay declarations."""

    if type(config) is not BenchmarkPreCallConfig:
        _fail("config", "must be an exact BenchmarkPreCallConfig")
    execution = cast(dict[str, object], config.to_dict()["execution"])
    actual = _runtime_wire()
    for field, value in actual.items():
        if execution[field] != value:
            _fail(f"execution.{field}", "does not match the current runtime")


def require_live_authorization(config: BenchmarkPreCallConfig) -> None:
    """Require the external gate's priced declaration before constructing a client.

    ``cost.status=available`` records what the external gate accepted for this one
    attempt. Parsing this declaration does not itself prove user authorization, and
    a later attempt requires a new declaration that externally accounts for prior
    spend.
    """

    if type(config) is not BenchmarkPreCallConfig:
        _fail("config", "must be an exact BenchmarkPreCallConfig")
    if not config.has_priced_attempt_ceiling:
        _fail("budget.cost", "live collection requires an explicit priced spend ceiling")


def preregistered_artifact_budget(
    preregistration: BenchmarkPreregistration,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return detached full-run ceilings and the complete-unit reservation."""

    if type(preregistration) is not BenchmarkPreregistration:
        _fail("preregistration", "must be an exact BenchmarkPreregistration")
    _wire, _model, _versions, budgets = _prereg_components(preregistration)
    return dict(_maximum_budget_from_prereg(budgets)), dict(
        _reservation_from_prereg(budgets)
    )


def pre_call_artifact_budget(
    config: BenchmarkPreCallConfig,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return the already validated live ceilings and unit reservation."""

    if type(config) is not BenchmarkPreCallConfig:
        _fail("config", "must be an exact BenchmarkPreCallConfig")
    budget = cast(dict[str, object], config.to_dict()["budget"])
    maximum = {
        name: cast(int, budget[name])
        for name in (
            "max_attempt_reserved_output_tokens",
            "max_attempts",
            "max_logical_calls",
            "max_requested_output_tokens",
            "max_response_text_bytes",
            "max_transport_response_bytes",
            "max_recorded_provider_call_elapsed_microseconds",
        )
    }
    reservation = cast(dict[str, int], budget["scheduled_unit_reservation"])
    return maximum, dict(reservation)


__all__ = [
    "BENCHMARK_PRE_CALL_CONFIG_VERSION",
    "MAX_COLLECTION_ATTEMPT",
    "SINGLE_ATTEMPT_CEILING_SCOPE",
    "BenchmarkPreCallConfig",
    "PreCallConfigError",
    "build_pre_call_config",
    "current_runtime_identity",
    "pre_call_config_from_bytes",
    "pre_call_config_from_dict",
    "pre_call_artifact_budget",
    "preregistered_artifact_budget",
    "require_live_authorization",
    "validate_current_runtime",
]
