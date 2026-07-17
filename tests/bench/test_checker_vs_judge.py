from __future__ import annotations

import hashlib
from dataclasses import replace
from fractions import Fraction as F

import pytest

import fretsure.bench.checker_vs_judge as judge_module
from fretsure.bench.checker_vs_judge import (
    CROSS_PROVIDER_AUTH_VERSION,
    FINGERING_POLICY,
    JUDGE_MAX_TOKENS,
    JUDGE_REPETITIONS,
    JUDGE_TEMPERATURE,
    PROMPT_SPECS,
    RUBRIC_PROMPT_SHA256,
    RUBRIC_PROMPT_VERSION,
    SOFTWARE_FIXTURE_EVIDENCE_VERSION,
    TAB_TIME_UNIT,
    TEMPO_UNIT,
    ZERO_SHOT_PROMPT_SHA256,
    ZERO_SHOT_PROMPT_VERSION,
    AgreementStatus,
    BinaryVerdict,
    CallAccounting,
    CrossProviderAuthorization,
    CrossProviderStatus,
    JudgeClient,
    JudgeInputError,
    JudgeItem,
    JudgeResult,
    LabelProvenance,
    LabelSource,
    PromptCondition,
    ReferenceLabel,
    build_judge_schedule,
    judge_proposition_sha256,
    parse_judge_result,
    run_software_fixture,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.llm.client import FakeLLM
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab, TabNote, tab_to_json

_RED = Tab(
    (
        TabNote(F(0), F(1), 0, 1, 1, "p"),
        TabNote(F(0), F(1), 1, 15, 4, "i"),
    ),
    STANDARD_TUNING,
    0,
)
_GREEN = Tab(
    (
        TabNote(F(0), F(1), 0, 2, 1, "p"),
        TabNote(F(0), F(1), 1, 2, 1, "i"),
    ),
    STANDARD_TUNING,
    0,
)


def _record_sha(item_id: str) -> str:
    return hashlib.sha256(item_id.encode("ascii")).hexdigest()


def _provenance(
    item_id: str,
    *,
    source: LabelSource = LabelSource.SOFTWARE_FIXTURE,
) -> LabelProvenance:
    return LabelProvenance(
        source,
        "checker-judge-fixtures",
        "guitarist-label-protocol@0.1.0",
        _record_sha(item_id),
    )


def _label(
    item_id: str,
    verdict: BinaryVerdict = BinaryVerdict.UNPLAYABLE,
    *,
    family_id: str | None = None,
    adversarial_class: str = "stretch-near-miss",
    tab: Tab = _RED,
    tempo_bpm: float = 120.0,
    meter: tuple[int, int] = (3, 4),
) -> ReferenceLabel:
    return ReferenceLabel(
        item_id,
        family_id or f"family-{item_id}",
        adversarial_class,
        MEDIAN_HAND.version,
        MEDIAN_HAND.fingerprint,
        judge_proposition_sha256(
            tab,
            MEDIAN_HAND,
            tempo_bpm=tempo_bpm,
            meter=meter,
        ),
        verdict,
        AgreementStatus.SOFTWARE_FIXTURE,
        0,
        _provenance(item_id),
    )


def _item(
    item_id: str = "fixture-red",
    *,
    tab: Tab = _RED,
    verdict: BinaryVerdict = BinaryVerdict.UNPLAYABLE,
    tempo_bpm: float = 120.0,
    meter: tuple[int, int] = (3, 4),
) -> JudgeItem:
    return JudgeItem(
        tab,
        MEDIAN_HAND,
        _label(
            item_id,
            verdict,
            tab=tab,
            tempo_bpm=tempo_bpm,
            meter=meter,
        ),
        tempo_bpm,
        meter,
    )


def _judge(
    replies: list[str],
    *,
    judge_id: str = "judge-a",
    provider_id: str = "provider-a",
    accounting: CallAccounting | None = None,
) -> tuple[JudgeClient, FakeLLM]:
    llm = FakeLLM(replies)
    reader = None if accounting is None else lambda: accounting
    return JudgeClient(judge_id, provider_id, llm, reader), llm


class _NamedFakeLLM:
    def __init__(self, model_id: str, replies: list[str]) -> None:
        self._model_id = model_id
        self._fake = FakeLLM(replies)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def calls(self) -> list[dict[str, object]]:
        return self._fake.calls

    def mutate_model_id(self, model_id: str) -> None:
        self._model_id = model_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        return self._fake.complete(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )


class _FailingFakeLLM:
    def __init__(self) -> None:
        self.call_count = 0

    @property
    def model_id(self) -> str:
        return "fake-with-failures"

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        self.call_count += 1
        if self.call_count <= 4:
            raise RuntimeError("fixture call failure")
        return "PLAYABLE"


class _MutatingFailureLLM:
    def __init__(self) -> None:
        self._model_id = "stable-model@1"
        self.call_count = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        self.call_count += 1
        self._model_id = "mutated-model@2"
        raise RuntimeError("fixture call failure after model mutation")


def test_parser_accepts_only_two_exact_builtin_strings() -> None:
    assert parse_judge_result("PLAYABLE") is JudgeResult.PLAYABLE
    assert parse_judge_result("UNPLAYABLE") is JudgeResult.UNPLAYABLE

    for malformed in (
        "playable",
        " PLAYABLE",
        "PLAYABLE\n",
        "Yes, PLAYABLE.",
        "This is UNPLAYABLE",
        "",
        None,
        BinaryVerdict.PLAYABLE,
    ):
        assert parse_judge_result(malformed) is JudgeResult.INVALID


def test_prompt_versions_and_hashes_are_frozen_exactly() -> None:
    assert [(spec.condition, spec.version, spec.sha256) for spec in PROMPT_SPECS] == [
        (
            PromptCondition.ZERO_SHOT,
            ZERO_SHOT_PROMPT_VERSION,
            "f634b851f8a7a115402363c93547877edf9945ec7e7893ac8ba2c09d46c7f89e",
        ),
        (
            PromptCondition.RUBRIC,
            RUBRIC_PROMPT_VERSION,
            "361c34103c4e16ac87c9cc62b0c7822623778ab3ea5c21994a18fec367acccbc",
        ),
    ]
    assert ZERO_SHOT_PROMPT_SHA256 == PROMPT_SPECS[0].sha256
    assert RUBRIC_PROMPT_SHA256 == PROMPT_SPECS[1].sha256


def test_case_prompt_binds_tab_profile_tempo_and_meter_but_not_label_or_checker() -> None:
    item = _item()
    judge, fake = _judge(["PLAYABLE"] * 10)

    result = run_software_fixture((item,), (judge,))

    assert len(fake.calls) == 10
    first_user = fake.calls[0]["user"]
    assert isinstance(first_user, str)
    assert tab_to_json(item.tab) in first_user
    assert f"profile_sha256={MEDIAN_HAND.fingerprint}" in first_user
    assert f"tab_time_unit={TAB_TIME_UNIT}" in first_user
    assert "tempo_bpm=120" in first_user
    assert f"tempo_unit={TEMPO_UNIT}" in first_user
    assert "beats_per_bar=" not in first_user
    assert "meter=3/4" in first_user
    assert "bar_duration_quarter_notes=3/1" in first_user
    assert f"fingering_policy={FINGERING_POLICY}" in first_user
    assert "fingering_scope=exact_supplied_left_and_right_hand" in first_user
    assert "refingering_allowed=false" in first_user
    assert item.label.adversarial_class not in first_user
    assert item.label.provenance.record_sha256 not in first_user
    assert "checker_verdict" not in first_user
    assert "GREEN" not in first_user and "RED" not in first_user
    assert all(call["max_tokens"] == JUDGE_MAX_TOKENS for call in fake.calls)
    assert all(call["temperature"] == JUDGE_TEMPERATURE == 0.8 for call in fake.calls)
    assert [call["system"] for call in fake.calls[:5]] == [PROMPT_SPECS[0].system] * 5
    assert [call["system"] for call in fake.calls[5:]] == [PROMPT_SPECS[1].system] * 5
    assert all(
        "exact supplied left- and right-hand fingering" in spec.system
        for spec in PROMPT_SPECS
    )
    assert all("Do not refinger" in spec.system for spec in PROMPT_SPECS)
    assert result.rows[0].request_sha256 != result.rows[5].request_sha256


def test_six_eight_prompt_uses_quarter_note_units_and_exact_bar_duration() -> None:
    item = _item(meter=(6, 8))
    judge, fake = _judge(["PLAYABLE"] * 10)

    run_software_fixture((item,), (judge,))

    first_user = fake.calls[0]["user"]
    assert isinstance(first_user, str)
    assert "beats_per_bar=6" not in first_user
    assert f"tab_time_unit={TAB_TIME_UNIT}" in first_user
    assert f"tempo_unit={TEMPO_UNIT}" in first_user
    assert "meter=6/8" in first_user
    assert "bar_duration_quarter_notes=3/1" in first_user


def test_formal_labels_distinguish_human_agreement_from_software_fixture() -> None:
    fixture = _label("fixture")
    human = ReferenceLabel(
        "human-item",
        "human-family",
        "control-playable",
        MEDIAN_HAND.version,
        MEDIAN_HAND.fingerprint,
        judge_proposition_sha256(_GREEN, MEDIAN_HAND),
        BinaryVerdict.PLAYABLE,
        AgreementStatus.AGREED,
        2,
        _provenance("human-item", source=LabelSource.HUMAN),
    )
    uncertain = ReferenceLabel(
        "uncertain-item",
        "uncertain-family",
        "uncertain-observation",
        MEDIAN_HAND.version,
        MEDIAN_HAND.fingerprint,
        judge_proposition_sha256(_GREEN, MEDIAN_HAND),
        None,
        AgreementStatus.UNCERTAIN,
        1,
        _provenance("uncertain-item", source=LabelSource.HUMAN),
    )

    assert fixture.is_human_gold is False
    assert human.is_human_gold is True
    assert uncertain.is_human_gold is False
    with pytest.raises(JudgeInputError, match="human label state"):
        replace(uncertain, labeler_count=0)
    with pytest.raises(JudgeInputError, match="software fixtures require"):
        ReferenceLabel(
            "bad",
            "family-bad",
            "control",
            MEDIAN_HAND.version,
            MEDIAN_HAND.fingerprint,
            judge_proposition_sha256(_RED, MEDIAN_HAND),
            BinaryVerdict.PLAYABLE,
            AgreementStatus.AGREED,
            2,
            _provenance("bad"),
        )


def test_task6_runner_rejects_human_labels_before_any_model_call() -> None:
    human = ReferenceLabel(
        "human-item",
        "human-family",
        "control-playable",
        MEDIAN_HAND.version,
        MEDIAN_HAND.fingerprint,
        judge_proposition_sha256(_GREEN, MEDIAN_HAND),
        BinaryVerdict.PLAYABLE,
        AgreementStatus.AGREED,
        2,
        _provenance("human-item", source=LabelSource.HUMAN),
    )
    judge, fake = _judge(["PLAYABLE"] * 10)

    with pytest.raises(JudgeInputError, match="software fixtures only"):
        run_software_fixture((JudgeItem(_GREEN, MEDIAN_HAND, human),), (judge,))

    assert fake.calls == []


def test_reference_label_binds_exact_tab_tempo_meter_and_profile_before_calls() -> None:
    label = _label("bound")
    assert label.proposition_sha256 == (
        "51f9adaace6a297e1460839d6d2084c06be481d007cd6104ea70168aa83dedc2"
    )
    judge, fake = _judge(["PLAYABLE"] * 10)

    with pytest.raises(JudgeInputError, match="proposition_sha256"):
        run_software_fixture(
            (JudgeItem(_GREEN, MEDIAN_HAND, label, 120.0, (3, 4)),),
            (judge,),
        )
    with pytest.raises(JudgeInputError, match="proposition_sha256"):
        run_software_fixture(
            (JudgeItem(_RED, MEDIAN_HAND, label, 121.0, (3, 4)),),
            (judge,),
        )
    with pytest.raises(JudgeInputError, match="proposition_sha256"):
        run_software_fixture(
            (JudgeItem(_RED, MEDIAN_HAND, label, 120.0, (4, 4)),),
            (judge,),
        )
    with pytest.raises(JudgeInputError, match="supported meter"):
        run_software_fixture(
            (JudgeItem(_RED, MEDIAN_HAND, label, 120.0, (3, 3)),),
            (judge,),
        )

    assert fake.calls == []


def test_complete_schedule_is_identity_only_zero_based_and_prebuilt_before_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = (
        _item("item-a"),
        _item("item-b", tab=_GREEN, verdict=BinaryVerdict.PLAYABLE),
    )
    judge_a, fake_a = _judge(["PLAYABLE"] * 20, judge_id="judge-a")
    judge_b, fake_b = _judge(["UNPLAYABLE"] * 20, judge_id="judge-b")
    original_schedule = judge_module._schedule  # noqa: SLF001
    schedule_ready = False

    def tracked_schedule(*args: object, **kwargs: object) -> object:
        nonlocal schedule_ready
        result = original_schedule(*args, **kwargs)  # type: ignore[arg-type]
        schedule_ready = True
        return result

    class GuardedFake(_NamedFakeLLM):
        def complete(self, **kwargs: object) -> str:
            assert schedule_ready
            return super().complete(**kwargs)  # type: ignore[arg-type]

    guarded = GuardedFake("guarded-model", ["PLAYABLE"] * 20)
    judge_a = JudgeClient("judge-a", "provider-a", guarded)
    monkeypatch.setattr(judge_module, "_schedule", tracked_schedule)

    schedule = build_judge_schedule(items, (judge_a, judge_b))
    schedule_ready = False
    result = run_software_fixture(items, (judge_a, judge_b))

    assert result.schedule == schedule
    assert len(schedule) == len(result.rows) == 40
    assert [entry.call_index for entry in schedule] == list(range(40))
    assert [entry.repetition for entry in schedule[:10]] == [
        0,
        0,
        1,
        1,
        2,
        2,
        3,
        3,
        4,
        4,
    ]
    assert [entry.judge_id for entry in schedule[:10]] == ["judge-a", "judge-b"] * 5
    assert all(entry.item_id == "item-a" for entry in schedule[:20])
    assert all(entry.condition is PromptCondition.ZERO_SHOT for entry in schedule[:10])
    assert all(entry.condition is PromptCondition.RUBRIC for entry in schedule[10:20])
    assert len(result.cells) == 8
    assert all(len(cell.results) == JUDGE_REPETITIONS == 5 for cell in result.cells)
    assert len(guarded.calls) == 20
    assert len(fake_a.calls) == 0
    assert len(fake_b.calls) == 20


def test_different_fake_outcomes_cannot_change_schedule_or_request_order() -> None:
    item = _item()
    first_judge, _first_fake = _judge(["PLAYABLE"] * 10)
    second_judge, _second_fake = _judge(["UNPLAYABLE", "invalid"] * 5)

    first = run_software_fixture((item,), (first_judge,))
    second = run_software_fixture((item,), (second_judge,))

    assert first.schedule == second.schedule
    assert [row.request_sha256 for row in first.rows] == [
        row.request_sha256 for row in second.rows
    ]
    assert [row.result for row in first.rows] != [row.result for row in second.rows]


def test_request_hash_binds_model_output_limit_and_temperature() -> None:
    base = judge_module._request_sha256(  # noqa: SLF001
        "system",
        "user",
        model_id="model-a",
        max_tokens=8,
        temperature=0.8,
    )
    assert base != judge_module._request_sha256(  # noqa: SLF001
        "system",
        "user",
        model_id="model-b",
        max_tokens=8,
        temperature=0.8,
    )
    assert base != judge_module._request_sha256(  # noqa: SLF001
        "system",
        "user",
        model_id="model-a",
        max_tokens=9,
        temperature=0.8,
    )
    assert base != judge_module._request_sha256(  # noqa: SLF001
        "system",
        "user",
        model_id="model-a",
        max_tokens=8,
        temperature=0.7,
    )


def test_fake_flips_and_invalids_use_unordered_successful_pair_disagreement() -> None:
    # zero_shot: P, U, INVALID, P, U; rubric: five P.
    replies = [
        "PLAYABLE",
        "UNPLAYABLE",
        "not exact",
        "PLAYABLE",
        "UNPLAYABLE",
        *("PLAYABLE" for _ in range(5)),
    ]
    judge, _fake = _judge(replies)

    result = run_software_fixture((_item(),), (judge,))
    zero, rubric = result.cells

    assert zero.results == (
        JudgeResult.PLAYABLE,
        JudgeResult.UNPLAYABLE,
        JudgeResult.INVALID,
        JudgeResult.PLAYABLE,
        JudgeResult.UNPLAYABLE,
    )
    assert zero.successful_count == 5
    assert zero.call_failed_count == 0
    assert zero.invalid_count == 1
    assert zero.comparable_pairs == 10
    assert zero.disagreement_pairs == 8
    assert zero.flip_rate == 0.8
    assert rubric.results == (JudgeResult.PLAYABLE,) * 5
    assert rubric.flip_rate == 0.0


def test_call_failures_are_preserved_and_excluded_from_flip_denominator() -> None:
    fake = _FailingFakeLLM()
    judge = JudgeClient("judge-a", "provider-a", fake)

    result = run_software_fixture((_item(),), (judge,))
    zero = result.cells[0]

    assert zero.results == (JudgeResult.CALL_FAILED,) * 4 + (JudgeResult.PLAYABLE,)
    assert zero.successful_count == 1
    assert zero.call_failed_count == 4
    assert zero.invalid_count == 0
    assert zero.comparable_pairs == 0
    assert zero.disagreement_pairs == 0
    assert zero.flip_rate is None
    assert all(row.reply_sha256 is None for row in result.rows[:4])


def test_model_mutation_during_failed_call_rejects_the_prepared_stamp() -> None:
    fake = _MutatingFailureLLM()
    judge = JudgeClient("judge-a", "provider-a", fake)

    with pytest.raises(JudgeInputError, match="model_id"):
        run_software_fixture((_item(),), (judge,))

    assert fake.call_count == 1


def test_model_mutation_during_accounting_stops_before_the_second_call() -> None:
    fake = _NamedFakeLLM("stable-model@1", ["PLAYABLE"] * 10)
    accounting_calls = 0

    def accounting_reader() -> CallAccounting:
        nonlocal accounting_calls
        accounting_calls += 1
        if accounting_calls == 1:
            fake.mutate_model_id("mutated-model@2")
        return CallAccounting(False, None, None, False, None, None)

    judge = JudgeClient(
        "judge-a",
        "provider-a",
        fake,
        accounting_reader,
    )

    with pytest.raises(JudgeInputError, match="model_id"):
        run_software_fixture((_item(),), (judge,))

    assert len(fake.calls) == 1
    assert accounting_calls == 1


def test_each_row_preserves_formal_label_checker_model_result_and_accounting() -> None:
    accounting = CallAccounting(
        usage_available=True,
        input_tokens=101,
        output_tokens=1,
        cost_available=True,
        cost_microusd=7,
        pricing_contract_version="fixture-pricing@0.1.0",
    )
    judge, _fake = _judge(["UNPLAYABLE"] * 10, accounting=accounting)

    result = run_software_fixture((_item(),), (judge,))
    row = result.rows[0]

    assert result.evidence_version == SOFTWARE_FIXTURE_EVIDENCE_VERSION
    assert result.evidence_status == "SOFTWARE_FIXTURE_ONLY"
    assert row.family_id == "family-fixture-red"
    assert row.adversarial_class == "stretch-near-miss"
    assert row.profile_version == MEDIAN_HAND.version
    assert row.profile_fingerprint == MEDIAN_HAND.fingerprint
    assert row.proposition_sha256 == _item().label.proposition_sha256
    assert row.fingering_policy == FINGERING_POLICY == "EXHIBITED_ONLY"
    assert row.tab_sha256 == hashlib.sha256(
        b"fretsure:checker-judge-tab@0.1.0\0" + tab_to_json(_RED).encode("utf-8")
    ).hexdigest()
    assert row.tempo_bpm == 120.0
    assert row.meter == (3, 4)
    assert row.reference_verdict is BinaryVerdict.UNPLAYABLE
    assert row.agreement_status is AgreementStatus.SOFTWARE_FIXTURE
    assert row.label_provenance.source is LabelSource.SOFTWARE_FIXTURE
    assert row.checker_verdict == "RED"
    assert row.checker_version == "oracle@0.2.0"
    assert row.model_id == "fake-scripted"
    assert row.max_tokens == JUDGE_MAX_TOKENS
    assert row.temperature == JUDGE_TEMPERATURE
    assert row.result is JudgeResult.UNPLAYABLE
    assert row.reply_sha256 is not None
    assert row.accounting == accounting
    assert not hasattr(result, "oracle_correct")
    assert not hasattr(result, "judge_correct")


def test_cost_requires_versioned_pricing_and_fake_default_is_explicitly_unavailable() -> None:
    with pytest.raises(JudgeInputError, match="pricing_contract_version"):
        CallAccounting(True, 1, 1, True, 9, None)
    with pytest.raises(JudgeInputError, match="input and output"):
        CallAccounting(True, 1, None, False, None, None)
    with pytest.raises(JudgeInputError, match="input and output"):
        CallAccounting(True, None, 1, False, None, None)
    with pytest.raises(JudgeInputError, match="unavailable cost"):
        CallAccounting(False, None, None, False, 0, None)

    judge, _fake = _judge(["PLAYABLE"] * 10)
    result = run_software_fixture((_item(),), (judge,))
    assert all(row.accounting.usage_available is False for row in result.rows)
    assert all(row.accounting.cost_available is False for row in result.rows)
    assert all(row.accounting.cost_microusd is None for row in result.rows)
    assert all(row.accounting.pricing_contract_version is None for row in result.rows)


def test_cross_provider_is_unavailable_by_default_and_unauthorized_calls_are_blocked() -> None:
    one, _one_fake = _judge(["PLAYABLE"] * 10)
    single = run_software_fixture((_item(),), (one,))
    assert single.cross_provider.status is CrossProviderStatus.UNAVAILABLE
    assert single.cross_provider.reason == "requires_multiple_providers"

    first = _NamedFakeLLM("model-a@1", ["PLAYABLE"] * 10)
    second = _NamedFakeLLM("model-b@1", ["UNPLAYABLE"] * 10)
    judges = (
        JudgeClient("judge-a", "provider-a", first),
        JudgeClient("judge-b", "provider-b", second),
    )
    with pytest.raises(JudgeInputError, match="explicit call budget"):
        run_software_fixture((_item(),), judges)

    assert first.calls == []
    assert second.calls == []


def test_cross_provider_requires_exact_versioned_models_and_sufficient_budget() -> None:
    first = _NamedFakeLLM("model-a@1", ["PLAYABLE"] * 10)
    second = _NamedFakeLLM("model-b@1", ["UNPLAYABLE"] * 10)
    judges = (
        JudgeClient("judge-a", "provider-a", first),
        JudgeClient("judge-b", "provider-b", second),
    )
    authorization = CrossProviderAuthorization(
        CROSS_PROVIDER_AUTH_VERSION,
        20,
        (("judge-a", "model-a@1"), ("judge-b", "model-b@1")),
    )

    result = run_software_fixture(
        (_item(),),
        judges,
        cross_provider_authorization=authorization,
    )

    assert result.cross_provider.status is CrossProviderStatus.UNAVAILABLE
    assert result.cross_provider.reason == "software_fixture_only"
    assert result.cross_provider.providers == ("provider-a", "provider-b")
    assert result.cross_provider.authorization_version == CROSS_PROVIDER_AUTH_VERSION
    assert len(first.calls) == len(second.calls) == 10
    assert result.rows[0].request_sha256 != result.rows[1].request_sha256

    underfunded = CrossProviderAuthorization(
        CROSS_PROVIDER_AUTH_VERSION,
        19,
        authorization.model_bindings,
    )
    fresh_first = _NamedFakeLLM("model-a@1", ["PLAYABLE"] * 10)
    fresh_second = _NamedFakeLLM("model-b@1", ["UNPLAYABLE"] * 10)
    with pytest.raises(JudgeInputError, match="below the frozen schedule"):
        run_software_fixture(
            (_item(),),
            (
                JudgeClient("judge-a", "provider-a", fresh_first),
                JudgeClient("judge-b", "provider-b", fresh_second),
            ),
            cross_provider_authorization=underfunded,
        )
    assert fresh_first.calls == [] and fresh_second.calls == []


def test_cross_provider_rejects_same_model_id_before_calls_even_with_authorization() -> None:
    first = _NamedFakeLLM("shared-model@1", ["PLAYABLE"] * 10)
    second = _NamedFakeLLM("shared-model@1", ["UNPLAYABLE"] * 10)
    authorization = CrossProviderAuthorization(
        CROSS_PROVIDER_AUTH_VERSION,
        20,
        (("judge-a", "shared-model@1"), ("judge-b", "shared-model@1")),
    )

    with pytest.raises(JudgeInputError, match="independently versioned"):
        run_software_fixture(
            (_item(),),
            (
                JudgeClient("judge-a", "provider-a", first),
                JudgeClient("judge-b", "provider-b", second),
            ),
            cross_provider_authorization=authorization,
        )

    assert first.calls == [] and second.calls == []
