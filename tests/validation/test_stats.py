import os
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Never

import pytest

import fretsure.oracle.validation.stats as stats_module
from fretsure.oracle.profiles import LARGE_HAND, MEDIAN_HAND, SMALL_HAND
from fretsure.oracle.validation.stats import (
    MAX_STAT_COUNT,
    CohenKappaResult,
    ConfusionMatrix,
    GreenFalseAcceptResult,
    LabeledDataError,
    LabeledDataErrorCode,
    StatisticsInputError,
    clopper_pearson_upper_bound,
    cohen_kappa,
    cohen_kappa_result,
    confusion_from_labeled,
    green_false_accept_estimate,
    green_false_accept_upper_bound,
    load_labeled,
    wilson_ci,
)
from fretsure.tab import Tab, TabNote

FIXTURE = str(Path(__file__).resolve().parents[2] / "data" / "gold" / "sample_labeled.jsonl")


def test_fixture_exists() -> None:
    assert os.path.exists(FIXTURE)


def test_upper_bound_zero_false_accepts_matches_closed_form() -> None:
    cm = ConfusionMatrix(10, 0, 0, 0, 0, 0)
    ub = green_false_accept_upper_bound(cm, 0.975)
    assert ub is not None
    assert abs(ub - (1 - 0.025**0.1)) < 1e-9  # closed form for x=0
    assert abs(ub - 0.3085) < 1e-3


def test_upper_bound_with_a_false_accept_is_positive() -> None:
    cm = ConfusionMatrix(5, 1, 0, 0, 0, 0)  # 1 false accept in 6 GREEN
    ub = green_false_accept_upper_bound(cm)
    assert ub is not None
    assert 0.0 < ub < 1.0


def test_upper_bound_empty_green_is_undefined() -> None:
    cm = ConfusionMatrix(0, 0, 3, 3, 0, 0)
    assert green_false_accept_upper_bound(cm) is None


def test_green_false_accept_canonical_estimate_is_complete_and_frozen() -> None:
    result = green_false_accept_estimate(ConfusionMatrix(9, 1, 0, 0, 0, 0), 0.975)

    assert isinstance(result, GreenFalseAcceptResult)
    assert result.status == "estimated"
    assert result.x == 1
    assert result.n_green == 10
    assert result.confidence == 0.975
    assert result.observed_rate == 0.1
    assert result.upper_bound is not None
    assert 0.1 < result.upper_bound < 1.0
    assert result.method == "clopper-pearson-one-sided"
    with pytest.raises(AttributeError):
        result.status = "no_green"  # type: ignore[misc]


def test_green_false_accept_canonical_no_green_preserves_denominator() -> None:
    result = green_false_accept_estimate(ConfusionMatrix(0, 0, 4, 3, 1, 2))

    assert result == GreenFalseAcceptResult(
        status="no_green",
        x=0,
        n_green=0,
        confidence=0.975,
        observed_rate=None,
        upper_bound=None,
        method="clopper-pearson-one-sided",
    )


def test_clopper_pearson_public_primitive_matches_canonical_result() -> None:
    cm = ConfusionMatrix(7, 2, 0, 0, 0, 0)
    result = green_false_accept_estimate(cm, 0.9)
    assert result.upper_bound == clopper_pearson_upper_bound(2, 9, 0.9)
    assert clopper_pearson_upper_bound(0, 0) is None


def test_fixture_has_no_green_false_accepts() -> None:
    cm = confusion_from_labeled(load_labeled(FIXTURE), MEDIAN_HAND)
    assert cm.green_unplayable == 0  # the soundness guarantee on the sample
    assert cm.green_playable >= 1  # coverage: some GREEN present
    assert cm.red_unplayable >= 1  # coverage: some RED present


def test_cohen_kappa_perfect_is_one() -> None:
    cm = ConfusionMatrix(5, 0, 0, 5, 0, 0)
    value = cohen_kappa(cm)
    assert value is not None
    assert abs(value - 1.0) < 1e-9


def test_cohen_kappa_empty_is_explicitly_undefined() -> None:
    cm = ConfusionMatrix(0, 0, 0, 0, 2, 3)
    result = cohen_kappa_result(cm)

    assert result == CohenKappaResult(
        status="undefined",
        value=None,
        n=0,
        reason="no_certified_observations",
    )
    assert cohen_kappa(cm) is None


def test_cohen_kappa_degenerate_marginals_are_explicitly_undefined() -> None:
    cm = ConfusionMatrix(5, 0, 0, 0, 0, 0)
    result = cohen_kappa_result(cm)

    assert result.status == "undefined"
    assert result.value is None
    assert result.n == 5
    assert result.reason == "degenerate_marginals"
    assert cohen_kappa(cm) is None


def test_cohen_kappa_within_bounds_on_fixture() -> None:
    cm = confusion_from_labeled(load_labeled(FIXTURE), MEDIAN_HAND)
    value = cohen_kappa(cm)
    assert value is not None
    assert -1.0 <= value <= 1.0


def test_wilson_ci_bounds() -> None:
    lo, hi = wilson_ci(5, 10)
    assert 0.0 <= lo < hi <= 1.0
    assert lo < 0.5 < hi


def test_wilson_ci_empty_is_full_range() -> None:
    assert wilson_ci(0, 0) == (0.0, 1.0)


@pytest.mark.parametrize(
    ("successes", "n"),
    [
        (-1, 1),
        (2, 1),
        (0, -1),
        (True, 1),
        (0, False),
        (1.0, 2),
        (1, 2.0),
    ],
)
def test_wilson_rejects_invalid_or_non_exact_integer_counts(successes: object, n: object) -> None:
    with pytest.raises(StatisticsInputError):
        wilson_ci(successes, n)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "confidence",
    [
        0.0,
        1.0,
        -0.1,
        1.1,
        float("nan"),
        float("inf"),
        True,
        "0.95",
        None,
        pytest.param(10**10000, id="overflowing-int"),
    ],
)
def test_all_interval_entrypoints_reject_invalid_confidence(confidence: object) -> None:
    cm = ConfusionMatrix(1, 0, 0, 0, 0, 0)
    with pytest.raises(StatisticsInputError):
        green_false_accept_estimate(cm, confidence)  # type: ignore[arg-type]
    with pytest.raises(StatisticsInputError):
        green_false_accept_upper_bound(cm, confidence)  # type: ignore[arg-type]
    with pytest.raises(StatisticsInputError):
        clopper_pearson_upper_bound(0, 1, confidence)  # type: ignore[arg-type]
    with pytest.raises(StatisticsInputError):
        wilson_ci(0, 1, confidence)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("x", "n"),
    [
        (-1, 1),
        (2, 1),
        (0, -1),
        (True, 1),
        (0, False),
        (1.0, 2),
        (1, 2.0),
    ],
)
def test_clopper_pearson_rejects_invalid_or_non_exact_integer_counts(x: object, n: object) -> None:
    with pytest.raises(StatisticsInputError):
        clopper_pearson_upper_bound(x, n)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", [-1, True, 1.0, "1", None])
def test_confusion_matrix_rejects_invalid_cell_counts(bad_value: object) -> None:
    values: list[object] = [0, 0, 0, 0, 0, 0]
    values[2] = bad_value
    with pytest.raises(StatisticsInputError, match="red_playable"):
        ConfusionMatrix(*values)  # type: ignore[arg-type]


def test_statistical_counts_have_an_explicit_resource_ceiling() -> None:
    with pytest.raises(StatisticsInputError, match="public count limit"):
        wilson_ci(MAX_STAT_COUNT + 1, MAX_STAT_COUNT + 1)
    with pytest.raises(StatisticsInputError, match="public count limit"):
        ConfusionMatrix(MAX_STAT_COUNT + 1, 0, 0, 0, 0, 0)


class _HostileRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr must not escape the typed error boundary")


def test_statistics_error_is_safe_for_a_hostile_value_repr() -> None:
    with pytest.raises(StatisticsInputError) as exc_info:
        wilson_ci(_HostileRepr(), 1)  # type: ignore[arg-type]

    assert str(exc_info.value).startswith("invalid successes:")


class _HostileNameMeta(type):
    def __getattribute__(cls, name: str) -> object:
        if name == "__name__":
            raise RuntimeError("metaclass name lookup must not escape")
        return type.__getattribute__(cls, name)


class _HostileNameAndRepr(metaclass=_HostileNameMeta):
    def __repr__(self) -> Never:
        raise RuntimeError("repr must not escape")


class _HostileFloat(float):
    def __float__(self) -> Never:
        raise RuntimeError("float conversion must not execute")


def test_statistics_error_does_not_inspect_hostile_metaclass_name() -> None:
    with pytest.raises(StatisticsInputError) as exc_info:
        wilson_ci(_HostileNameAndRepr(), 1)  # type: ignore[arg-type]

    assert str(exc_info.value).startswith("invalid successes:")


def test_interval_entrypoints_reject_float_subclass_without_conversion() -> None:
    confidence = _HostileFloat(0.95)
    with pytest.raises(StatisticsInputError):
        clopper_pearson_upper_bound(0, 1, confidence)
    with pytest.raises(StatisticsInputError):
        wilson_ci(0, 1, confidence)


class _HostileConfusionLike:
    @property
    def green_playable(self) -> Never:
        raise RuntimeError("duck-typed property must not execute")

    def __repr__(self) -> Never:
        raise RuntimeError("repr must not escape")


def test_statistics_reject_duck_confusion_matrix_without_property_access() -> None:
    value = _HostileConfusionLike()
    with pytest.raises(StatisticsInputError):
        green_false_accept_estimate(value)  # type: ignore[arg-type]
    with pytest.raises(StatisticsInputError):
        cohen_kappa_result(value)  # type: ignore[arg-type]


def test_statistics_revalidate_low_level_forged_confusion_matrix() -> None:
    forged = object.__new__(ConfusionMatrix)
    for name in (
        "green_playable",
        "green_unplayable",
        "red_playable",
        "red_unplayable",
        "amber_playable",
        "amber_unplayable",
    ):
        object.__setattr__(forged, name, 0)
    object.__setattr__(forged, "green_playable", -99)

    with pytest.raises(StatisticsInputError):
        green_false_accept_estimate(forged)
    with pytest.raises(StatisticsInputError):
        cohen_kappa_result(forged)


def test_confusion_matrix_estimators_use_detached_count_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ConfusionMatrix(1, 0, 0, 1, 0, 0)
    real_validate = stats_module._validate_confusion_matrix

    def mutate_source_after_barrier(value: object) -> ConfusionMatrix:
        snapshot = real_validate(value)
        object.__setattr__(source, "green_playable", 0)
        return snapshot

    monkeypatch.setattr(stats_module, "_validate_confusion_matrix", mutate_source_after_barrier)
    estimate = green_false_accept_estimate(source)

    assert estimate.status == "estimated"
    assert estimate.n_green == 1


def test_load_labeled_reports_invalid_json_with_physical_line(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text('\n{"tab": {}, "human_playable": true}\n{"oops":', encoding="utf-8")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_JSON
    assert error.path == str(path)
    assert error.line_number == 3


@pytest.mark.parametrize(
    "payload",
    [
        '{"tab": {"x": NaN}, "human_playable": true}',
        '{"tab": {}, "human_playable": true, "human_playable": false}',
    ],
)
def test_load_labeled_rejects_nonstandard_or_ambiguous_json(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "nonstandard.jsonl"
    path.write_text(payload + "\n", encoding="utf-8")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_JSON
    assert exc_info.value.line_number == 1


def test_load_labeled_rejects_lone_surrogate_as_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "lone-surrogate.jsonl"
    path.write_text(
        '{"tab":{},"human_playable":true,"note":"\\ud800"}\n',
        encoding="utf-8",
    )

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert "valid UTF-8" in error.detail


def test_confusion_rejects_in_memory_lone_surrogate_as_typed_error() -> None:
    rows = load_labeled(FIXTURE)
    row = dict(rows[0])
    row["note"] = "\ud800"

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled([row], MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert "valid UTF-8" in error.detail


@pytest.mark.parametrize(
    "numeric_token",
    ["1e999999", "9" * 129],
)
def test_load_labeled_rejects_nonfinite_or_oversized_numeric_tokens(
    tmp_path: Path,
    numeric_token: str,
) -> None:
    path = tmp_path / "numeric-attack.jsonl"
    path.write_text(
        '{"tab":{"capo":' + numeric_token + '},"human_playable":true}\n',
        encoding="utf-8",
    )

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_JSON
    assert exc_info.value.line_number == 1


def test_load_labeled_wraps_deep_nesting_as_typed_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "deep.jsonl"
    nested = "[" * 2_000 + "0" + "]" * 2_000
    path.write_text(
        '{"tab":{},"human_playable":true,"metadata":' + nested + "}\n",
        encoding="utf-8",
    )

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_JSON
    assert exc_info.value.line_number == 1


def test_load_labeled_rejects_invalid_utf8_with_physical_line(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8.jsonl"
    path.write_bytes(b"\n\xff\n")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_JSON
    assert exc_info.value.line_number == 2


def test_load_labeled_reports_invalid_utf8_character_column(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8-column.jsonl"
    path.write_bytes('  "é"'.encode() + b"\xff\n")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_JSON
    assert exc_info.value.line_number == 1
    assert exc_info.value.column == 6


def test_load_labeled_preserves_physical_json_error_column(tmp_path: Path) -> None:
    path = tmp_path / "leading-space-column.jsonl"
    path.write_text("  {bad}\n", encoding="utf-8")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_JSON
    assert exc_info.value.column == 4


@pytest.mark.parametrize("wrapper", ("\u00a0", "\v", "\f"))
def test_load_labeled_rejects_non_json_whitespace(
    tmp_path: Path,
    wrapper: str,
) -> None:
    path = tmp_path / "non-json-whitespace.jsonl"
    path.write_text(
        wrapper + '{"tab":{},"human_playable":true}' + wrapper + "\n",
        encoding="utf-8",
    )

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_JSON
    assert exc_info.value.line_number == 1


def test_load_labeled_enforces_bounded_physical_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stats_module, "MAX_LABELED_JSON_LINE_BYTES", 64)
    path = tmp_path / "oversized-line.jsonl"
    path.write_bytes(b" " * 65 + b"\n")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert exc_info.value.line_number == 1


def test_load_labeled_enforces_cumulative_file_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b'{"tab":{},"human_playable":true}\n'
    monkeypatch.setattr(
        stats_module,
        "MAX_LABELED_TOTAL_BYTES",
        len(payload) + 1,
    )
    path = tmp_path / "oversized-total.jsonl"
    path.write_bytes(payload * 2)

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert error.line_number == 2
    assert "cumulative JSONL bytes" in error.detail


def test_load_labeled_enforces_cumulative_json_node_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = '{"tab":{},"human_playable":true}\n'
    monkeypatch.setattr(stats_module, "MAX_LABELED_TOTAL_JSON_NODES", 3)
    path = tmp_path / "too-many-json-nodes.jsonl"
    path.write_text(payload * 2, encoding="utf-8")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert error.line_number == 2
    assert "cumulative JSON value count" in error.detail


def test_load_labeled_caps_blank_physical_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stats_module, "MAX_LABELED_PHYSICAL_LINES", 3)
    path = tmp_path / "too-many-blank-lines.jsonl"
    path.write_text("\n" * 4, encoding="utf-8")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert error.line_number == 4
    assert "physical JSONL line count" in error.detail


def test_load_labeled_enforces_cumulative_declared_note_budget_before_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = '{"tab":{"notes":[{},{}]},"human_playable":true}\n'
    monkeypatch.setattr(stats_module, "MAX_LABELED_TOTAL_NOTES", 3)
    path = tmp_path / "oversized-notes.jsonl"
    path.write_text(payload * 2, encoding="utf-8")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert error.line_number == 2
    assert error.field == "tab.notes"
    assert "cumulative declared Tab note count" in error.detail


def test_load_labeled_preserves_public_list_of_plain_dicts_contract() -> None:
    rows = load_labeled(FIXTURE)

    assert isinstance(rows, list)
    assert rows == list(rows)
    assert rows
    assert all(type(row) is dict for row in rows)
    assert all("__source__" not in row for row in rows)


@pytest.mark.parametrize(
    ("payload", "code", "field"),
    [
        ("[1, 2, 3]", LabeledDataErrorCode.ROW_NOT_OBJECT, None),
        ('{"tab": {}}', LabeledDataErrorCode.MISSING_FIELD, "human_playable"),
        ('{"human_playable": true}', LabeledDataErrorCode.MISSING_FIELD, "tab"),
        (
            '{"tab": [], "human_playable": true}',
            LabeledDataErrorCode.INVALID_FIELD_TYPE,
            "tab",
        ),
        (
            '{"tab": {}, "human_playable": "false"}',
            LabeledDataErrorCode.INVALID_FIELD_TYPE,
            "human_playable",
        ),
        (
            '{"tab": {}, "human_playable": 0}',
            LabeledDataErrorCode.INVALID_FIELD_TYPE,
            "human_playable",
        ),
    ],
)
def test_load_labeled_enforces_row_schema(
    tmp_path: Path,
    payload: str,
    code: LabeledDataErrorCode,
    field: str | None,
) -> None:
    path = tmp_path / "bad-schema.jsonl"
    path.write_text(payload + "\n", encoding="utf-8")

    with pytest.raises(LabeledDataError) as exc_info:
        load_labeled(str(path))

    assert exc_info.value.code is code
    assert exc_info.value.line_number == 1
    assert exc_info.value.field == field


def test_confusion_from_labeled_does_not_coerce_human_label_string() -> None:
    rows = load_labeled(FIXTURE)
    bad_row = dict(rows[0])
    bad_row["human_playable"] = "false"

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled([bad_row], MEDIAN_HAND)

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert exc_info.value.field == "human_playable"
    assert exc_info.value.line_number == 1


def test_confusion_wraps_tab_schema_error_with_row_location() -> None:
    rows = load_labeled(FIXTURE)
    invalid_row = dict(rows[1])
    invalid_tab = dict(invalid_row["tab"])  # type: ignore[arg-type]
    invalid_tab["unexpected"] = "must not be ignored"
    invalid_row["tab"] = invalid_tab

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled([rows[0], invalid_row], MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_TAB_SCHEMA
    assert error.path == "<rows>"
    assert error.line_number == 2
    assert error.field == "tab"
    assert "UNKNOWN_FIELD" in error.detail
    assert "$.unexpected" in error.detail


def test_confusion_wraps_canonical_oracle_input_error_with_row_location() -> None:
    rows = load_labeled(FIXTURE)
    invalid_row = dict(rows[1])
    invalid_tab = dict(invalid_row["tab"])  # type: ignore[arg-type]
    invalid_tab["notes"] = []
    invalid_row["tab"] = invalid_tab

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled([rows[0], invalid_row, rows[2]], MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_ORACLE_INPUT
    assert error.path == "<rows>"
    assert error.line_number == 2
    assert error.field == "tab"
    assert "EMPTY_TAB" in error.detail
    assert "tab.notes" in error.detail


@pytest.mark.parametrize(
    ("tab_payload", "code", "detail_fragment"),
    [
        (
            '{"unexpected":"must not be ignored"}',
            LabeledDataErrorCode.INVALID_TAB_SCHEMA,
            "UNKNOWN_FIELD",
        ),
        (
            '{"tuning":[40,45,50,55,59,64],"capo":0,"notes":[]}',
            LabeledDataErrorCode.INVALID_ORACLE_INPUT,
            "EMPTY_TAB",
        ),
    ],
)
def test_loaded_rows_preserve_physical_location_for_late_tab_failures(
    tmp_path: Path,
    tab_payload: str,
    code: LabeledDataErrorCode,
    detail_fragment: str,
) -> None:
    valid_line = Path(FIXTURE).read_text(encoding="utf-8").splitlines()[0]
    invalid_line = f'{{"tab":{tab_payload},"human_playable":true}}'
    path = tmp_path / "late-invalid.jsonl"
    path.write_text(
        "\n".join(("", valid_line, "", invalid_line, "")),
        encoding="utf-8",
    )

    rows = load_labeled(str(path))
    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows, MEDIAN_HAND)

    error = exc_info.value
    assert error.code is code
    assert error.path == str(path)
    assert error.line_number == 4
    assert error.field == "tab"
    assert detail_fragment in error.detail


def test_loaded_rows_drop_physical_provenance_after_in_place_content_swap(
    tmp_path: Path,
) -> None:
    valid_line = Path(FIXTURE).read_text(encoding="utf-8").splitlines()[0]
    invalid_line = '{"tab":{"unexpected":true},"human_playable":true}'
    path = tmp_path / "mutated-provenance.jsonl"
    path.write_text(
        "\n".join(("", valid_line, "", invalid_line, "")),
        encoding="utf-8",
    )
    rows = load_labeled(str(path))
    first_content = dict(rows[0])
    second_content = dict(rows[1])
    rows[0].clear()
    rows[0].update(second_content)
    rows[1].clear()
    rows[1].update(first_content)

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows, MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_TAB_SCHEMA
    assert error.path == "<rows>"
    assert error.line_number == 1


def test_loaded_rows_drop_physical_provenance_after_nested_mutation() -> None:
    rows = load_labeled(FIXTURE)
    tab = rows[0]["tab"]
    assert type(tab) is dict
    tab["unexpected"] = True

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows, MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_TAB_SCHEMA
    assert error.path == "<rows>"
    assert error.line_number == 1


def test_loaded_rows_ignore_tampered_private_source_records_without_hooks() -> None:
    rows = load_labeled(FIXTURE)
    rows._source_records = _HostileList()  # type: ignore[attr-defined]
    invalid_tab = rows[0]["tab"]
    assert type(invalid_tab) is dict
    invalid_tab["unexpected"] = True

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows, MEDIAN_HAND)

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_TAB_SCHEMA
    assert exc_info.value.path == "<rows>"


def test_loaded_rows_ignore_deleted_private_source_records() -> None:
    rows = load_labeled(FIXTURE)
    del rows._source_records  # type: ignore[attr-defined]
    invalid_tab = rows[0]["tab"]
    assert type(invalid_tab) is dict
    invalid_tab["unexpected"] = True

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows, MEDIAN_HAND)

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_TAB_SCHEMA
    assert exc_info.value.path == "<rows>"


def test_confusion_wraps_non_finite_in_memory_tab_value() -> None:
    rows = load_labeled(FIXTURE)
    invalid_row = dict(rows[0])
    invalid_tab = dict(invalid_row["tab"])  # type: ignore[arg-type]
    invalid_tab["capo"] = float("nan")
    invalid_row["tab"] = invalid_tab

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled([invalid_row], MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert error.path == "<rows>"
    assert error.line_number == 1
    assert error.field == "$['tab']['capo']"
    assert "strict JSON" in error.detail


class _HostileDict(dict[str, object]):
    def items(self) -> Never:
        raise AssertionError("untrusted dict subclass was executed")


def test_confusion_rejects_dict_subclass_without_executing_overrides() -> None:
    row = _HostileDict()

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled([row], MEDIAN_HAND)

    assert exc_info.value.code is LabeledDataErrorCode.ROW_NOT_OBJECT


class _HostileList(list[object]):
    def __iter__(self) -> Never:
        raise AssertionError("untrusted list subclass was executed")


class _HostileIterable:
    def __iter__(self) -> Never:
        raise AssertionError("untrusted iterable was executed")


class _HostileRowsMeta(type):
    def __eq__(cls, other: object) -> Never:
        raise AssertionError("untrusted metaclass equality was executed")


class _HostileRowsType(metaclass=_HostileRowsMeta):
    pass


def test_confusion_rejects_outer_list_subclass_without_iterating() -> None:
    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(_HostileList(), MEDIAN_HAND)  # type: ignore[arg-type]

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert exc_info.value.field == "<rows>"


def test_confusion_rejects_generic_iterable_without_iterating() -> None:
    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(_HostileIterable(), MEDIAN_HAND)  # type: ignore[arg-type]

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert exc_info.value.field == "<rows>"


def test_confusion_rejects_hostile_type_without_comparing_metaclass() -> None:
    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(_HostileRowsType(), MEDIAN_HAND)  # type: ignore[arg-type]

    assert exc_info.value.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert exc_info.value.field == "<rows>"


def test_confusion_rejects_nested_list_subclass_without_iterating() -> None:
    rows = load_labeled(FIXTURE)
    invalid_row = dict(rows[0])
    invalid_tab = dict(invalid_row["tab"])  # type: ignore[arg-type]
    invalid_tab["notes"] = _HostileList()
    invalid_row["tab"] = invalid_tab

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled([invalid_row], MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert error.field == "$['tab']['notes']"


def test_confusion_rejects_tuple_notes_instead_of_coercing_to_json_array() -> None:
    rows = load_labeled(FIXTURE)
    invalid_row = dict(rows[0])
    invalid_tab = dict(invalid_row["tab"])  # type: ignore[arg-type]
    invalid_tab["notes"] = tuple(invalid_tab["notes"])  # type: ignore[arg-type]
    invalid_row["tab"] = invalid_tab

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled([invalid_row], MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INVALID_FIELD_TYPE
    assert error.field == "$['tab']['notes']"


def test_confusion_enforces_row_resource_limit_before_counting_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = load_labeled(FIXTURE)
    monkeypatch.setattr(stats_module, "MAX_LABELED_ROWS", 2)

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows[:3], MEDIAN_HAND)

    assert exc_info.value.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert exc_info.value.line_number == 3


def test_confusion_uses_bounded_top_level_row_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [load_labeled(FIXTURE)[0]]
    real_validate = stats_module._validate_labeled_row
    appended = False

    def append_after_snapshot(*args: object, **kwargs: object) -> object:
        nonlocal appended
        result = real_validate(*args, **kwargs)
        if not appended:
            rows.extend([rows[0]] * 9)
            appended = True
        return result

    monkeypatch.setattr(stats_module, "_validate_labeled_row", append_after_snapshot)
    matrix = confusion_from_labeled(rows, MEDIAN_HAND)

    assert appended
    assert sum(
        (
            matrix.green_playable,
            matrix.green_unplayable,
            matrix.red_playable,
            matrix.red_unplayable,
            matrix.amber_playable,
            matrix.amber_unplayable,
        )
    ) == 1


def test_confusion_deep_snapshots_later_rows_before_row_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [dict(row) for row in load_labeled(FIXTURE)[:2]]
    expected = confusion_from_labeled(rows, MEDIAN_HAND)
    real_validate = stats_module._validate_labeled_row
    mutated = False

    def mutate_second_source_row(*args: object, **kwargs: object) -> object:
        nonlocal mutated
        result = real_validate(*args, **kwargs)
        if not mutated:
            rows[1]["human_playable"] = not rows[1]["human_playable"]
            mutated = True
        return result

    monkeypatch.setattr(stats_module, "_validate_labeled_row", mutate_second_source_row)
    actual = confusion_from_labeled(rows, MEDIAN_HAND)

    assert mutated
    assert actual == expected


def test_confusion_uses_one_detached_profile_for_all_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_profile = replace(MEDIAN_HAND, version="stats-snapshot@0.1")
    expected_fingerprint = source_profile.fingerprint
    real_check = stats_module.check_playability
    seen_fingerprints: list[str] = []

    def mutate_and_check(tab: Tab, profile: object) -> object:
        object.__setattr__(source_profile, "version", "mutated@0.1")
        object.__setattr__(source_profile, "hand_span_mm", 200.0)
        seen_fingerprints.append(profile.fingerprint)  # type: ignore[union-attr]
        return real_check(tab, profile)  # type: ignore[arg-type]

    monkeypatch.setattr(stats_module, "check_playability", mutate_and_check)
    confusion_from_labeled(load_labeled(FIXTURE), source_profile)

    assert seen_fingerprints
    assert set(seen_fingerprints) == {expected_fingerprint}


def test_confusion_enforces_cumulative_note_budget_before_next_checker_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = load_labeled(FIXTURE)
    real_check = stats_module.check_playability
    checked_tabs: list[object] = []

    def tracked_check(tab: object, profile: object) -> object:
        checked_tabs.append(tab)
        return real_check(tab, profile)  # type: ignore[arg-type]

    monkeypatch.setattr(stats_module, "check_playability", tracked_check)
    monkeypatch.setattr(stats_module, "MAX_LABELED_TOTAL_NOTES", 3)

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows, MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert error.path == FIXTURE
    assert error.line_number == 2
    assert error.field == "tab.notes"
    assert checked_tabs == []  # aggregate preflight finishes before any checker work


@pytest.mark.parametrize(
    ("limit_name", "budget_field", "detail_fragment"),
    [
        ("MAX_LABELED_TOTAL_BYTES", "scalar_bytes", "in-memory JSON scalar bytes"),
        ("MAX_LABELED_TOTAL_JSON_NODES", "nodes", "in-memory JSON value count"),
    ],
)
def test_confusion_enforces_cumulative_in_memory_tree_budgets_before_checker(
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    budget_field: str,
    detail_fragment: str,
) -> None:
    rows = load_labeled(FIXTURE)
    real_check = stats_module.check_playability
    checked_tabs: list[object] = []

    def tracked_check(tab: object, profile: object) -> object:
        checked_tabs.append(tab)
        return real_check(tab, profile)  # type: ignore[arg-type]

    _row, _digest, first_budget = stats_module._validate_labeled_row(
        rows[0],
        path=FIXTURE,
        line_number=1,
    )
    monkeypatch.setattr(stats_module, "check_playability", tracked_check)
    monkeypatch.setattr(stats_module, limit_name, getattr(first_budget, budget_field))

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows, MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert error.path == FIXTURE
    assert error.line_number == 2
    assert detail_fragment in error.detail
    assert checked_tabs == []  # all rows are deep-snapshotted before checking


def test_confusion_enforces_cumulative_checker_work_before_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = load_labeled(FIXTURE)
    real_check = stats_module.check_playability
    checked_tabs: list[object] = []

    def tracked_check(tab: object, profile: object) -> object:
        checked_tabs.append(tab)
        return real_check(tab, profile)  # type: ignore[arg-type]

    monkeypatch.setattr(stats_module, "check_playability", tracked_check)
    first_tab = stats_module._tab_from_labeled_row(
        rows[0],
        profile=MEDIAN_HAND,
        path=FIXTURE,
        line_number=1,
    )
    first_row_work = stats_module._checker_work_units(first_tab)
    monkeypatch.setattr(
        stats_module,
        "MAX_LABELED_CHECKER_WORK",
        first_row_work,
    )

    with pytest.raises(LabeledDataError) as exc_info:
        confusion_from_labeled(rows, MEDIAN_HAND)

    error = exc_info.value
    assert error.code is LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
    assert error.path == FIXTURE
    assert error.line_number == 2
    assert error.field == "tab"
    assert "cumulative checker work" in error.detail
    assert len(checked_tabs) == 1


def test_checker_work_charges_distinct_onset_sorting_and_three_profiles() -> None:
    notes = tuple(
        TabNote(
            onset=Fraction(index),
            duration=Fraction(1),
            string=index % 6,
            fret=0,
            left_finger=0,
            right_finger=("p", "i", "m", "a")[index % 4],  # type: ignore[arg-type]
        )
        for index in range(20_000)
    )
    tab = Tab(notes=notes, tuning=(40, 45, 50, 55, 59, 64), capo=0)

    assert stats_module._checker_work_units(tab) > stats_module.MAX_LABELED_CHECKER_WORK


def _green_total(profile: object) -> int:
    cm = confusion_from_labeled(load_labeled(FIXTURE), profile)  # type: ignore[arg-type]
    return cm.green_playable + cm.green_unplayable


def test_preset_sensitivity_green_count_monotone() -> None:
    # sensitivity scan: a larger hand can only certify more (or equal) tabs GREEN
    assert _green_total(SMALL_HAND) <= _green_total(MEDIAN_HAND) <= _green_total(LARGE_HAND)
