import inspect
import sys
from dataclasses import FrozenInstanceError

import pytest

import fretsure.bench.reliability as reliability_module
from fretsure.bench.reliability import (
    MAX_RELIABILITY_SAMPLES,
    PassKResult,
    ReliabilityInputError,
    pass_at_k,
    pass_hat_k,
    pass_hat_k_estimate,
    pass_hat_k_item,
    wilson,
)


@pytest.mark.parametrize("function", [pass_at_k, pass_hat_k_item])
@pytest.mark.parametrize(
    ("args", "field"),
    [
        ((True, 1, 1), "n"),
        ((1.0, 1, 1), "n"),
        ((1, False, 1), "c"),
        ((1, 1.0, 1), "c"),
        ((1, 1, True), "k"),
        ((1, 1, 1.0), "k"),
    ],
)
def test_item_estimators_reject_non_exact_integers(
    function: object,
    args: tuple[object, object, object],
    field: str,
) -> None:
    with pytest.raises(ReliabilityInputError) as caught:
        function(*args)  # type: ignore[operator]

    assert caught.value.field == field


@pytest.mark.parametrize("function", [pass_at_k, pass_hat_k_item])
@pytest.mark.parametrize(
    ("args", "field"),
    [
        ((0, 0, 1), "n"),
        ((-1, 0, 1), "n"),
        ((5, -1, 1), "c"),
        ((5, 6, 1), "c"),
        ((5, 3, 0), "k"),
        ((5, 3, -1), "k"),
        ((5, 3, 6), "k"),
    ],
)
def test_item_estimators_reject_invalid_count_domains(
    function: object,
    args: tuple[object, object, object],
    field: str,
) -> None:
    with pytest.raises(ReliabilityInputError) as caught:
        function(*args)  # type: ignore[operator]

    assert caught.value.field == field


def test_pass_hat_k_canonical_empty_result_is_explicitly_undefined() -> None:
    result = pass_hat_k_estimate([], 8)

    assert result == PassKResult(
        status="no_items",
        n_items=0,
        k=8,
        value=None,
    )
    assert pass_hat_k([], 8) is None


def test_pass_hat_k_canonical_estimate_preserves_evidence_count() -> None:
    result = pass_hat_k_estimate([(10, 10), (10, 0)], 8)

    assert result == PassKResult(
        status="estimated",
        n_items=2,
        k=8,
        value=0.5,
    )
    assert pass_hat_k([(10, 10), (10, 0)], 8) == result.value


def test_pass_hat_k_result_is_frozen() -> None:
    result = pass_hat_k_estimate([], 1)

    with pytest.raises(FrozenInstanceError):
        result.value = 0.0  # type: ignore[misc]


@pytest.mark.parametrize("k", [True, 1.0, 0, -1])
def test_pass_hat_k_rejects_invalid_k_even_without_items(k: object) -> None:
    with pytest.raises(ReliabilityInputError) as caught:
        pass_hat_k_estimate([], k)  # type: ignore[arg-type]

    assert caught.value.field == "k"


@pytest.mark.parametrize("per_item", [None, (), ((10, 10),), "10,10"])
def test_pass_hat_k_rejects_non_list_collections(per_item: object) -> None:
    with pytest.raises(ReliabilityInputError) as caught:
        pass_hat_k_estimate(per_item, 1)  # type: ignore[arg-type]

    assert caught.value.field == "per_item"


@pytest.mark.parametrize(
    ("item", "field"),
    [
        ([10, 10], "per_item[0]"),
        ((10,), "per_item[0]"),
        ((10, 10, 10), "per_item[0]"),
        ((True, 1), "per_item[0].n"),
        ((10, False), "per_item[0].c"),
        ((0, 0), "per_item[0].n"),
        ((10, -1), "per_item[0].c"),
        ((10, 11), "per_item[0].c"),
        ((5, 3), "per_item[0].k"),
    ],
)
def test_pass_hat_k_strictly_validates_each_item(
    item: object,
    field: str,
) -> None:
    with pytest.raises(ReliabilityInputError) as caught:
        pass_hat_k_estimate([item], 6)  # type: ignore[list-item]

    assert caught.value.field == field


@pytest.mark.parametrize(
    ("args", "field"),
    [
        ((True, 1, 0.95), "successes"),
        ((1, False, 0.95), "n"),
        ((2, 1, 0.95), "successes"),
        ((1, 1, float("nan")), "confidence"),
    ],
)
def test_wilson_exposes_the_reliability_typed_boundary(
    args: tuple[object, object, object],
    field: str,
) -> None:
    with pytest.raises(ReliabilityInputError) as caught:
        wilson(*args)  # type: ignore[arg-type]

    assert caught.value.field == field


@pytest.mark.parametrize("function", [pass_at_k, pass_hat_k_item])
def test_item_estimators_enforce_sample_resource_limit(function: object) -> None:
    with pytest.raises(ReliabilityInputError) as caught:
        function(  # type: ignore[operator]
            MAX_RELIABILITY_SAMPLES + 1,
            1,
            1,
        )

    assert caught.value.field == "n"


def test_empty_estimate_still_enforces_public_k_ceiling() -> None:
    with pytest.raises(ReliabilityInputError) as caught:
        pass_hat_k_estimate([], MAX_RELIABILITY_SAMPLES + 1)

    assert caught.value.field == "k"


def test_estimate_enforces_item_and_total_work_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reliability_module, "MAX_RELIABILITY_ITEMS", 1)
    with pytest.raises(ReliabilityInputError, match="item count"):
        pass_hat_k_estimate([(2, 2), (2, 2)], 1)

    monkeypatch.setattr(reliability_module, "MAX_RELIABILITY_ITEMS", 10)
    monkeypatch.setattr(reliability_module, "MAX_RELIABILITY_WORK", 3)
    with pytest.raises(ReliabilityInputError, match="work limit"):
        pass_hat_k_estimate([(2, 2), (2, 2)], 2)


_HOSTILE_HOOK_CALLS: list[str] = []


class _HostileMeta(type):
    def __getattribute__(cls, name: str) -> object:
        if name == "__name__":
            _HOSTILE_HOOK_CALLS.append("metaclass name")
            raise AssertionError("type-name hook must not run")
        return super().__getattribute__(name)


class _HostileValue(metaclass=_HostileMeta):
    def __repr__(self) -> str:
        _HOSTILE_HOOK_CALLS.append("repr")
        raise AssertionError("repr must not run")

    def __str__(self) -> str:
        _HOSTILE_HOOK_CALLS.append("str")
        raise AssertionError("str must not run")


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (pass_at_k, (_HostileValue(), 1, 1)),
        (pass_hat_k_item, (1, _HostileValue(), 1)),
        (pass_hat_k_estimate, (_HostileValue(), 1)),
        (pass_hat_k, ([_HostileValue()], 1)),
        (wilson, (_HostileValue(), 1)),
    ],
)
def test_public_estimators_reject_hostile_values_without_executing_hooks(
    function: object,
    args: tuple[object, ...],
) -> None:
    _HOSTILE_HOOK_CALLS.clear()

    with pytest.raises(ReliabilityInputError):
        function(*args)  # type: ignore[operator]

    assert _HOSTILE_HOOK_CALLS == []


def test_estimate_uses_an_inert_snapshot_under_trace_hook_mutation() -> None:
    per_item = [(2, 2), (2, 0)]
    mutated = False

    def mutate_after_snapshot(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal mutated
        if (
            not mutated
            and event == "line"
            and getattr(frame, "f_code", None) is pass_hat_k_estimate.__code__
            and "item_snapshot" in getattr(frame, "f_locals", {})
        ):
            per_item[:] = [(2, 2)]
            mutated = True
        return mutate_after_snapshot

    sys.settrace(mutate_after_snapshot)
    try:
        result = pass_hat_k_estimate(per_item, 1)
    finally:
        sys.settrace(None)


    assert mutated
    assert result == PassKResult(status="estimated", n_items=2, k=1, value=0.5)


def test_snapshot_prefix_stays_bounded_if_list_grows_after_length_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    per_item = [(2, 2)]
    source, start_line = inspect.getsourcelines(pass_hat_k_estimate)
    snapshot_line = start_line + next(
        index for index, line in enumerate(source) if "item_snapshot = tuple" in line
    )
    mutated = False

    def grow_at_snapshot(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal mutated
        if (
            not mutated
            and event == "line"
            and getattr(frame, "f_code", None) is pass_hat_k_estimate.__code__
            and getattr(frame, "f_lineno", None) == snapshot_line
        ):
            per_item.extend([(2, 2)] * 10_000)
            mutated = True
        return grow_at_snapshot

    monkeypatch.setattr(reliability_module, "MAX_RELIABILITY_ITEMS", 2)
    sys.settrace(grow_at_snapshot)
    try:
        with pytest.raises(ReliabilityInputError, match="item count"):
            pass_hat_k_estimate(per_item, 1)
    finally:
        sys.settrace(None)

    assert mutated
