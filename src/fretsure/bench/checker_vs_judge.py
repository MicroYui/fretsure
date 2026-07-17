"""Collection-ready checker-vs-judge experiment shape.

This module freezes labels, prompts, call order, and repeated-result rows.  The
only executable experiment in Task 6 is explicitly software-fixture evidence;
it emits no checker-vs-human or judge-vs-human accuracy claim.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Literal, cast

from fretsure.llm.client import LLMClient, snapshot_llm_model_id
from fretsure.oracle.core import check_playability
from fretsure.oracle.diagnostics import Verdict as CheckerVerdict
from fretsure.oracle.input import ensure_oracle_input
from fretsure.oracle.profiles import Profile
from fretsure.tab import Tab, tab_from_json, tab_to_json

JUDGE_REPETITIONS = 5
JUDGE_MAX_TOKENS = 8
JUDGE_TEMPERATURE = 0.8
ZERO_SHOT_PROMPT_VERSION = "checker-judge-zero-shot@0.1.0"
RUBRIC_PROMPT_VERSION = "checker-judge-rubric@0.1.0"
CROSS_PROVIDER_AUTH_VERSION = "checker-judge-cross-provider@0.1.0"
SOFTWARE_FIXTURE_EVIDENCE_VERSION = "checker-judge-software-fixture@0.1.0"
FINGERING_POLICY: Literal["EXHIBITED_ONLY"] = "EXHIBITED_ONLY"
TAB_TIME_UNIT = "quarter_note"
TEMPO_UNIT = "quarter_notes_per_minute"

ZERO_SHOT_SYSTEM_PROMPT = (
    "You judge physical playability of guitar tablature for the supplied player profile. "
    "Evaluate the exact supplied left- and right-hand fingering as exhibited. "
    "Do not refinger, replace, or optimize any fingering. "
    "Reply with exactly PLAYABLE or UNPLAYABLE."
)
RUBRIC_SYSTEM_PROMPT = (
    "You apply checker-judge-rubric@0.1.0 to guitar tablature. "
    "Judge physical playability only: simultaneous left-hand span and finger assignment; "
    "position-shift timing; string/fret conflicts and sustain; and right-hand repetition. "
    "Apply every check to the exact supplied left- and right-hand fingering as exhibited. "
    "Do not refinger, replace, or optimize any fingering. "
    "Use the supplied player profile. Do not judge musical quality. "
    "Reply with exactly PLAYABLE or UNPLAYABLE."
)
JUDGE_USER_TEMPLATE = (
    "PLAYER_PROFILE\n"
    "version={profile_version}\n"
    "profile_sha256={profile_fingerprint}\n"
    "hand_span_mm={hand_span_mm}\n"
    "reach_mm={reach_mm}\n"
    "shift_speed_mm_per_s={shift_speed_mm_per_s}\n"
    "right_hand_rate_hz={right_hand_rate_hz}\n"
    "string_length_mm={string_length_mm}\n"
    "max_fret={max_fret}\n"
    "tab_time_unit={tab_time_unit}\n"
    "tempo_bpm={tempo_bpm}\n"
    "tempo_unit={tempo_unit}\n"
    "meter={meter_numerator}/{meter_denominator}\n"
    "bar_duration_quarter_notes={bar_duration_quarter_notes}\n"
    "fingering_policy=EXHIBITED_ONLY\n"
    "fingering_scope=exact_supplied_left_and_right_hand\n"
    "refingering_allowed=false\n"
    "TAB_JSON\n"
    "{tab_json}\n"
)

ZERO_SHOT_PROMPT_SHA256 = "f634b851f8a7a115402363c93547877edf9945ec7e7893ac8ba2c09d46c7f89e"
RUBRIC_PROMPT_SHA256 = "361c34103c4e16ac87c9cc62b0c7822623778ab3ea5c21994a18fec367acccbc"

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROMPT_HASH_DOMAIN = b"fretsure:checker-judge-prompt@0.1.0\0"
_REQUEST_HASH_DOMAIN = b"fretsure:checker-judge-request@0.1.0\0"
_REPLY_HASH_DOMAIN = b"fretsure:checker-judge-reply@0.1.0\0"
_PROPOSITION_HASH_DOMAIN = b"fretsure:checker-judge-proposition@0.1.0\0"
_TAB_HASH_DOMAIN = b"fretsure:checker-judge-tab@0.1.0\0"


class JudgeInputError(ValueError):
    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid checker-vs-judge {field}: {detail}")


class BinaryVerdict(StrEnum):
    PLAYABLE = "PLAYABLE"
    UNPLAYABLE = "UNPLAYABLE"


class JudgeResult(StrEnum):
    PLAYABLE = "PLAYABLE"
    UNPLAYABLE = "UNPLAYABLE"
    INVALID = "INVALID"
    CALL_FAILED = "CALL_FAILED"


class PromptCondition(StrEnum):
    ZERO_SHOT = "zero_shot"
    RUBRIC = "rubric"


class LabelSource(StrEnum):
    HUMAN = "human"
    SOFTWARE_FIXTURE = "software_fixture"


class AgreementStatus(StrEnum):
    PENDING = "pending"
    SINGLE_LABEL = "single_label"
    UNCERTAIN = "uncertain"
    AGREED = "agreed"
    DISAGREED = "disagreed"
    ADJUDICATED = "adjudicated"
    SOFTWARE_FIXTURE = "software_fixture"


class CrossProviderStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


def _identifier(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or _IDENTIFIER.fullmatch(value) is None
    ):
        raise JudgeInputError(field, "must be a bounded identifier")
    return value


def _sha256(value: object, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise JudgeInputError(field, "must be a lowercase SHA-256")
    return value


def _meter(value: object, field: str) -> tuple[int, int]:
    if (
        type(value) is not tuple
        or len(value) != 2
        or type(value[0]) is not int
        or not 1 <= value[0] <= 32
        or type(value[1]) is not int
        or value[1] not in {1, 2, 4, 8, 16, 32, 64}
    ):
        raise JudgeInputError(field, "must be an exact supported meter")
    return value


def _proposition_sha256(
    tab_json: str,
    profile_fingerprint: str,
    tempo_bpm: float,
    meter: tuple[int, int],
) -> str:
    return hashlib.sha256(
        _PROPOSITION_HASH_DOMAIN
        + tab_json.encode("utf-8")
        + b"\0"
        + profile_fingerprint.encode("ascii")
        + b"\0"
        + tempo_bpm.hex().encode("ascii")
        + b"\0"
        + f"{meter[0]}/{meter[1]}".encode("ascii")
        + b"\0"
        + TAB_TIME_UNIT.encode("ascii")
        + b"\0"
        + TEMPO_UNIT.encode("ascii")
        + b"\0"
        + FINGERING_POLICY.encode("ascii")
    ).hexdigest()


def _tab_sha256(tab_json: str) -> str:
    return hashlib.sha256(_TAB_HASH_DOMAIN + tab_json.encode("utf-8")).hexdigest()


def judge_proposition_sha256(
    tab: Tab,
    profile: Profile,
    *,
    tempo_bpm: float = 90.0,
    meter: tuple[int, int] = (4, 4),
) -> str:
    """Bind the exact tab, tempo, meter, and profile named by one label."""

    exact_meter = _meter(meter, "proposition.meter")
    try:
        tab_json = tab_to_json(tab)
        detached_tab = tab_from_json(tab_json)
        _tab, exact_profile, exact_tempo, _beats = ensure_oracle_input(
            detached_tab,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=exact_meter[0],
        )
    except ValueError as error:
        raise JudgeInputError("proposition", str(error)) from None
    return _proposition_sha256(
        tab_json,
        exact_profile.fingerprint,
        exact_tempo,
        exact_meter,
    )


def _prompt_sha256(version: str, system: str, user_template: str) -> str:
    return hashlib.sha256(
        _PROMPT_HASH_DOMAIN
        + version.encode("ascii")
        + b"\0"
        + system.encode("utf-8")
        + b"\0"
        + user_template.encode("utf-8")
    ).hexdigest()


if (
    _prompt_sha256(ZERO_SHOT_PROMPT_VERSION, ZERO_SHOT_SYSTEM_PROMPT, JUDGE_USER_TEMPLATE)
    != ZERO_SHOT_PROMPT_SHA256
    or _prompt_sha256(RUBRIC_PROMPT_VERSION, RUBRIC_SYSTEM_PROMPT, JUDGE_USER_TEMPLATE)
    != RUBRIC_PROMPT_SHA256
):
    raise RuntimeError("checker-vs-judge prompt constants drifted from their frozen hashes")


@dataclass(frozen=True, slots=True)
class PromptSpec:
    condition: PromptCondition
    version: str
    system: str
    user_template: str
    sha256: str


PROMPT_SPECS = (
    PromptSpec(
        PromptCondition.ZERO_SHOT,
        ZERO_SHOT_PROMPT_VERSION,
        ZERO_SHOT_SYSTEM_PROMPT,
        JUDGE_USER_TEMPLATE,
        ZERO_SHOT_PROMPT_SHA256,
    ),
    PromptSpec(
        PromptCondition.RUBRIC,
        RUBRIC_PROMPT_VERSION,
        RUBRIC_SYSTEM_PROMPT,
        JUDGE_USER_TEMPLATE,
        RUBRIC_PROMPT_SHA256,
    ),
)
_PROMPT_BY_CONDITION = {spec.condition: spec for spec in PROMPT_SPECS}


@dataclass(frozen=True, slots=True)
class LabelProvenance:
    source: LabelSource
    dataset_id: str
    protocol_version: str
    record_sha256: str

    def __post_init__(self) -> None:
        if type(self.source) is not LabelSource:
            raise JudgeInputError("label.provenance.source", "must be a LabelSource")
        _identifier(self.dataset_id, "label.provenance.dataset_id")
        _identifier(self.protocol_version, "label.provenance.protocol_version")
        _sha256(self.record_sha256, "label.provenance.record_sha256")


@dataclass(frozen=True, slots=True)
class ReferenceLabel:
    item_id: str
    family_id: str
    adversarial_class: str
    profile_version: str
    profile_fingerprint: str
    proposition_sha256: str
    verdict: BinaryVerdict | None
    agreement_status: AgreementStatus
    labeler_count: int
    provenance: LabelProvenance

    def __post_init__(self) -> None:
        _identifier(self.item_id, "label.item_id")
        _identifier(self.family_id, "label.family_id")
        _identifier(self.adversarial_class, "label.adversarial_class")
        _identifier(self.profile_version, "label.profile_version")
        _sha256(self.profile_fingerprint, "label.profile_fingerprint")
        _sha256(self.proposition_sha256, "label.proposition_sha256")
        if self.verdict is not None and type(self.verdict) is not BinaryVerdict:
            raise JudgeInputError("label.verdict", "must be PLAYABLE, UNPLAYABLE, or null")
        if type(self.agreement_status) is not AgreementStatus:
            raise JudgeInputError("label.agreement_status", "must be an AgreementStatus")
        if type(self.labeler_count) is not int or not 0 <= self.labeler_count <= 100:
            raise JudgeInputError("label.labeler_count", "must be an exact integer in 0..100")
        if type(self.provenance) is not LabelProvenance:
            raise JudgeInputError("label.provenance", "must be a LabelProvenance")
        self._validate_state()

    def _validate_state(self) -> None:
        status = self.agreement_status
        if self.provenance.source is LabelSource.SOFTWARE_FIXTURE:
            if (
                status is not AgreementStatus.SOFTWARE_FIXTURE
                or self.labeler_count != 0
                or self.verdict is None
            ):
                raise JudgeInputError(
                    "label",
                    "software fixtures require a constructed verdict, zero labelers, "
                    "and fixture status",
                )
            return
        if status is AgreementStatus.SOFTWARE_FIXTURE:
            raise JudgeInputError(
                "label.agreement_status",
                "human labels cannot use fixture status",
            )
        valid = {
            AgreementStatus.PENDING: self.labeler_count == 0 and self.verdict is None,
            AgreementStatus.SINGLE_LABEL: self.labeler_count == 1 and self.verdict is not None,
            AgreementStatus.UNCERTAIN: self.labeler_count >= 1 and self.verdict is None,
            AgreementStatus.AGREED: self.labeler_count >= 2 and self.verdict is not None,
            AgreementStatus.DISAGREED: self.labeler_count >= 2 and self.verdict is None,
            AgreementStatus.ADJUDICATED: self.labeler_count >= 2 and self.verdict is not None,
        }
        if not valid.get(status, False):
            raise JudgeInputError("label", "human label state is inconsistent")

    @property
    def is_human_gold(self) -> bool:
        return self.provenance.source is LabelSource.HUMAN and self.agreement_status in {
            AgreementStatus.AGREED,
            AgreementStatus.ADJUDICATED,
        }


@dataclass(frozen=True, slots=True)
class JudgeItem:
    tab: Tab
    profile: Profile
    label: ReferenceLabel
    tempo_bpm: float = 90.0
    meter: tuple[int, int] = (4, 4)


@dataclass(frozen=True, slots=True)
class CallAccounting:
    usage_available: bool
    input_tokens: int | None
    output_tokens: int | None
    cost_available: bool
    cost_microusd: int | None
    pricing_contract_version: str | None

    def __post_init__(self) -> None:
        if type(self.usage_available) is not bool or type(self.cost_available) is not bool:
            raise JudgeInputError("accounting", "availability fields must be exact booleans")
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise JudgeInputError(f"accounting.{name}", "must be a nonnegative integer or null")
        if not self.usage_available and (
            self.input_tokens is not None or self.output_tokens is not None
        ):
            raise JudgeInputError("accounting", "unavailable usage must remain null")
        if self.usage_available and (
            type(self.input_tokens) is not int or type(self.output_tokens) is not int
        ):
            raise JudgeInputError(
                "accounting",
                "available usage requires exact input and output token counts",
            )
        if self.cost_available:
            if type(self.cost_microusd) is not int or self.cost_microusd < 0:
                raise JudgeInputError("accounting.cost_microusd", "available cost is nonnegative")
            _identifier(
                self.pricing_contract_version,
                "accounting.pricing_contract_version",
            )
        elif self.cost_microusd is not None or self.pricing_contract_version is not None:
            raise JudgeInputError("accounting.cost", "unavailable cost and pricing must be null")


UNAVAILABLE_ACCOUNTING = CallAccounting(False, None, None, False, None, None)
AccountingReader = Callable[[], CallAccounting]


@dataclass(frozen=True, slots=True)
class JudgeClient:
    judge_id: str
    provider_id: str
    llm: LLMClient
    accounting_reader: AccountingReader | None = None


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    call_index: int
    item_id: str
    condition: PromptCondition
    repetition: int
    judge_id: str


@dataclass(frozen=True, slots=True)
class JudgeRow:
    call_index: int
    item_id: str
    family_id: str
    adversarial_class: str
    profile_version: str
    profile_fingerprint: str
    proposition_sha256: str
    fingering_policy: Literal["EXHIBITED_ONLY"]
    tab_sha256: str
    tempo_bpm: float
    meter: tuple[int, int]
    reference_verdict: BinaryVerdict
    agreement_status: AgreementStatus
    label_provenance: LabelProvenance
    checker_verdict: CheckerVerdict
    checker_version: str
    condition: PromptCondition
    prompt_version: str
    prompt_sha256: str
    request_sha256: str
    max_tokens: int
    temperature: float
    repetition: int
    judge_id: str
    provider_id: str
    model_id: str
    result: JudgeResult
    reply_sha256: str | None
    accounting: CallAccounting


@dataclass(frozen=True, slots=True)
class CellSummary:
    item_id: str
    condition: PromptCondition
    judge_id: str
    results: tuple[JudgeResult, ...]
    successful_count: int
    call_failed_count: int
    invalid_count: int
    disagreement_pairs: int
    comparable_pairs: int
    flip_rate: float | None


@dataclass(frozen=True, slots=True)
class CrossProviderAuthorization:
    version: str
    max_calls: int
    model_bindings: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CrossProviderAvailability:
    status: CrossProviderStatus
    reason: str | None
    providers: tuple[str, ...]
    authorization_version: str | None


@dataclass(frozen=True, slots=True)
class SoftwareFixtureResult:
    evidence_version: str
    evidence_status: Literal["SOFTWARE_FIXTURE_ONLY"]
    schedule: tuple[ScheduleEntry, ...]
    rows: tuple[JudgeRow, ...]
    cells: tuple[CellSummary, ...]
    cross_provider: CrossProviderAvailability


@dataclass(frozen=True, slots=True)
class _PreparedItem:
    item: JudgeItem
    tab: Tab
    tab_json: str
    tab_sha256: str
    profile: Profile
    tempo_bpm: float


@dataclass(frozen=True, slots=True)
class _PreparedJudge:
    client: JudgeClient
    model_id: str


def parse_judge_result(value: object) -> JudgeResult:
    """Accept only the two exact labels; all other outputs are INVALID."""

    if value == "PLAYABLE" and type(value) is str:
        return JudgeResult.PLAYABLE
    if value == "UNPLAYABLE" and type(value) is str:
        return JudgeResult.UNPLAYABLE
    return JudgeResult.INVALID


def _prepare_items(value: object) -> tuple[_PreparedItem, ...]:
    if type(value) not in (tuple, list):
        raise JudgeInputError("items", "must be an exact tuple or list")
    prepared: list[_PreparedItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(cast(tuple[object, ...] | list[object], value)):
        if type(raw) is not JudgeItem:
            raise JudgeInputError(f"items[{index}]", "must be an exact JudgeItem")
        item = raw
        if type(item.label) is not ReferenceLabel:
            raise JudgeInputError(f"items[{index}].label", "must be a ReferenceLabel")
        meter = _meter(item.meter, f"items[{index}].meter")
        try:
            tab_json = tab_to_json(item.tab)
            detached_tab = tab_from_json(tab_json)
            detached_tab, profile, tempo, _beats = ensure_oracle_input(
                detached_tab,
                item.profile,
                tempo_bpm=item.tempo_bpm,
                beats_per_bar=meter[0],
            )
        except ValueError as error:
            raise JudgeInputError(f"items[{index}]", str(error)) from None
        if item.label.item_id in seen:
            raise JudgeInputError("items", "item_id values must be unique")
        seen.add(item.label.item_id)
        if (
            item.label.profile_version != profile.version
            or item.label.profile_fingerprint != profile.fingerprint
        ):
            raise JudgeInputError(f"items[{index}].label", "does not bind the supplied profile")
        expected_proposition = _proposition_sha256(
            tab_json,
            profile.fingerprint,
            tempo,
            meter,
        )
        if item.label.proposition_sha256 != expected_proposition:
            raise JudgeInputError(
                f"items[{index}].label.proposition_sha256",
                "does not bind the supplied tab, tempo, meter, and profile",
            )
        prepared.append(
            _PreparedItem(
                item,
                detached_tab,
                tab_json,
                _tab_sha256(tab_json),
                profile,
                tempo,
            )
        )
    if not prepared:
        raise JudgeInputError("items", "must not be empty")
    return tuple(prepared)


def _prepare_judges(value: object) -> tuple[_PreparedJudge, ...]:
    if type(value) not in (tuple, list):
        raise JudgeInputError("judges", "must be an exact tuple or list")
    prepared: list[_PreparedJudge] = []
    seen: set[str] = set()
    for index, raw in enumerate(cast(tuple[object, ...] | list[object], value)):
        if type(raw) is not JudgeClient:
            raise JudgeInputError(f"judges[{index}]", "must be an exact JudgeClient")
        judge_id = _identifier(raw.judge_id, f"judges[{index}].judge_id")
        _identifier(raw.provider_id, f"judges[{index}].provider_id")
        if judge_id in seen:
            raise JudgeInputError("judges", "judge_id values must be unique")
        seen.add(judge_id)
        try:
            model_id = snapshot_llm_model_id(raw.llm)
        except ValueError as error:
            raise JudgeInputError(f"judges[{index}].model_id", str(error)) from None
        if raw.accounting_reader is not None and not callable(raw.accounting_reader):
            raise JudgeInputError(f"judges[{index}].accounting_reader", "must be callable")
        prepared.append(_PreparedJudge(raw, model_id))
    if not prepared:
        raise JudgeInputError("judges", "must not be empty")
    return tuple(prepared)


def _schedule(
    items: tuple[_PreparedItem, ...],
    judges: tuple[_PreparedJudge, ...],
) -> tuple[ScheduleEntry, ...]:
    entries: list[ScheduleEntry] = []
    for item in items:
        for spec in PROMPT_SPECS:
            for repetition in range(JUDGE_REPETITIONS):
                for judge in judges:
                    entries.append(
                        ScheduleEntry(
                            len(entries),
                            item.item.label.item_id,
                            spec.condition,
                            repetition,
                            judge.client.judge_id,
                        )
                    )
    return tuple(entries)


def build_judge_schedule(items: object, judges: object) -> tuple[ScheduleEntry, ...]:
    """Precompute the complete identity-only order without evaluating outcomes."""

    return _schedule(_prepare_items(items), _prepare_judges(judges))


def _user_prompt(item: _PreparedItem) -> str:
    profile = item.profile
    bar_duration = Fraction(item.item.meter[0] * 4, item.item.meter[1])
    return JUDGE_USER_TEMPLATE.format(
        profile_version=profile.version,
        profile_fingerprint=profile.fingerprint,
        hand_span_mm=format(profile.hand_span_mm, ".17g"),
        reach_mm=format(profile.reach_mm, ".17g"),
        shift_speed_mm_per_s=format(profile.v_shift_mm_per_s, ".17g"),
        right_hand_rate_hz=format(profile.r_max_hz, ".17g"),
        string_length_mm=format(profile.string_length_mm, ".17g"),
        max_fret=profile.max_fret,
        tab_time_unit=TAB_TIME_UNIT,
        tempo_bpm=format(item.tempo_bpm, ".17g"),
        tempo_unit=TEMPO_UNIT,
        meter_numerator=item.item.meter[0],
        meter_denominator=item.item.meter[1],
        bar_duration_quarter_notes=(
            f"{bar_duration.numerator}/{bar_duration.denominator}"
        ),
        tab_json=item.tab_json,
    )


def _request_sha256(
    system: str,
    user: str,
    *,
    model_id: str,
    max_tokens: int,
    temperature: float,
) -> str:
    return hashlib.sha256(
        _REQUEST_HASH_DOMAIN
        + model_id.encode("utf-8")
        + b"\0"
        + str(max_tokens).encode("ascii")
        + b"\0"
        + temperature.hex().encode("ascii")
        + b"\0"
        + system.encode("utf-8")
        + b"\0"
        + user.encode("utf-8")
    ).hexdigest()


def _reply_sha256(reply: object) -> str | None:
    if type(reply) is not str:
        return None
    return hashlib.sha256(_REPLY_HASH_DOMAIN + reply.encode("utf-8")).hexdigest()


def _accounting(judge: _PreparedJudge) -> CallAccounting:
    reader = judge.client.accounting_reader
    if reader is None:
        return UNAVAILABLE_ACCOUNTING
    value = reader()
    if type(value) is not CallAccounting:
        raise JudgeInputError("accounting_reader", "must return an exact CallAccounting")
    return value


def _require_model_stamp(judge: _PreparedJudge) -> None:
    try:
        current = snapshot_llm_model_id(judge.client.llm)
    except ValueError as error:
        raise JudgeInputError("judge.model_id", str(error)) from None
    if current != judge.model_id:
        raise JudgeInputError("judge.model_id", "changed during collection")


def _cross_provider(
    judges: tuple[_PreparedJudge, ...],
    call_count: int,
    authorization: CrossProviderAuthorization | None,
) -> CrossProviderAvailability:
    providers = tuple(sorted({judge.client.provider_id for judge in judges}))
    if len(providers) < 2:
        return CrossProviderAvailability(
            CrossProviderStatus.UNAVAILABLE,
            "requires_multiple_providers",
            providers,
            None,
        )
    if len({judge.model_id for judge in judges}) != len(judges):
        raise JudgeInputError(
            "cross_provider.model_bindings",
            "different providers require independently versioned model ids",
        )
    if authorization is None:
        raise JudgeInputError(
            "cross_provider",
            "multiple providers require versioned model bindings and an explicit call budget",
        )
    if type(authorization) is not CrossProviderAuthorization:
        raise JudgeInputError("cross_provider", "authorization has the wrong type")
    if authorization.version != CROSS_PROVIDER_AUTH_VERSION:
        raise JudgeInputError("cross_provider.version", "has the wrong version")
    if type(authorization.max_calls) is not int or authorization.max_calls < call_count:
        raise JudgeInputError("cross_provider.max_calls", "is below the frozen schedule")
    expected = tuple(sorted((judge.client.judge_id, judge.model_id) for judge in judges))
    if authorization.model_bindings != expected:
        raise JudgeInputError("cross_provider.model_bindings", "do not match judge model stamps")
    return CrossProviderAvailability(
        CrossProviderStatus.UNAVAILABLE,
        "software_fixture_only",
        providers,
        authorization.version,
    )


def _cell_summaries(rows: tuple[JudgeRow, ...]) -> tuple[CellSummary, ...]:
    keys = tuple(dict.fromkeys((row.item_id, row.condition, row.judge_id) for row in rows))
    summaries: list[CellSummary] = []
    for item_id, condition, judge_id in keys:
        cell_rows = tuple(
            row
            for row in rows
            if row.item_id == item_id
            and row.condition is condition
            and row.judge_id == judge_id
        )
        results = tuple(row.result for row in cell_rows)
        successful = tuple(result for result in results if result is not JudgeResult.CALL_FAILED)
        comparable = len(successful) * (len(successful) - 1) // 2
        disagreements = sum(
            left is not right
            for index, left in enumerate(successful)
            for right in successful[index + 1 :]
        )
        summaries.append(
            CellSummary(
                item_id,
                condition,
                judge_id,
                results,
                len(successful),
                results.count(JudgeResult.CALL_FAILED),
                results.count(JudgeResult.INVALID),
                disagreements,
                comparable,
                None if len(successful) < 2 else disagreements / comparable,
            )
        )
    return tuple(summaries)


def run_software_fixture(
    items: object,
    judges: object,
    *,
    cross_provider_authorization: CrossProviderAuthorization | None = None,
) -> SoftwareFixtureResult:
    """Run the frozen shape with injected clients and emit software evidence only."""

    prepared_items = _prepare_items(items)
    prepared_judges = _prepare_judges(judges)
    for index, item in enumerate(prepared_items):
        if (
            item.item.label.provenance.source is not LabelSource.SOFTWARE_FIXTURE
            or item.item.label.agreement_status is not AgreementStatus.SOFTWARE_FIXTURE
        ):
            raise JudgeInputError(
                f"items[{index}].label",
                "Task 6 execution accepts software fixtures only",
            )
    schedule = _schedule(prepared_items, prepared_judges)
    cross_provider = _cross_provider(
        prepared_judges,
        len(schedule),
        cross_provider_authorization,
    )
    by_item = {item.item.label.item_id: item for item in prepared_items}
    by_judge = {judge.client.judge_id: judge for judge in prepared_judges}
    checker = {
        item.item.label.item_id: check_playability(
            item.tab,
            item.profile,
            tempo_bpm=item.tempo_bpm,
            beats_per_bar=item.item.meter[0],
        )
        for item in prepared_items
    }
    rows: list[JudgeRow] = []
    for entry in schedule:
        item = by_item[entry.item_id]
        judge = by_judge[entry.judge_id]
        spec = _PROMPT_BY_CONDITION[entry.condition]
        user = _user_prompt(item)
        reply: object | None = None
        _require_model_stamp(judge)
        try:
            try:
                reply = judge.client.llm.complete(
                    system=spec.system,
                    user=user,
                    max_tokens=JUDGE_MAX_TOKENS,
                    temperature=JUDGE_TEMPERATURE,
                )
            except Exception:
                result = JudgeResult.CALL_FAILED
            else:
                result = parse_judge_result(reply)
        finally:
            _require_model_stamp(judge)
        label = item.item.label
        assert label.verdict is not None
        oracle = checker[entry.item_id]
        rows.append(
            JudgeRow(
                entry.call_index,
                label.item_id,
                label.family_id,
                label.adversarial_class,
                label.profile_version,
                label.profile_fingerprint,
                label.proposition_sha256,
                FINGERING_POLICY,
                item.tab_sha256,
                item.tempo_bpm,
                item.item.meter,
                label.verdict,
                label.agreement_status,
                label.provenance,
                oracle.verdict,
                oracle.checker_version,
                entry.condition,
                spec.version,
                spec.sha256,
                _request_sha256(
                    spec.system,
                    user,
                    model_id=judge.model_id,
                    max_tokens=JUDGE_MAX_TOKENS,
                    temperature=JUDGE_TEMPERATURE,
                ),
                JUDGE_MAX_TOKENS,
                JUDGE_TEMPERATURE,
                entry.repetition,
                judge.client.judge_id,
                judge.client.provider_id,
                judge.model_id,
                result,
                _reply_sha256(reply),
                _accounting(judge),
            )
        )
    row_tuple = tuple(rows)
    return SoftwareFixtureResult(
        SOFTWARE_FIXTURE_EVIDENCE_VERSION,
        "SOFTWARE_FIXTURE_ONLY",
        schedule,
        row_tuple,
        _cell_summaries(row_tuple),
        cross_provider,
    )


__all__ = [
    "CROSS_PROVIDER_AUTH_VERSION",
    "FINGERING_POLICY",
    "JUDGE_MAX_TOKENS",
    "JUDGE_REPETITIONS",
    "JUDGE_TEMPERATURE",
    "JUDGE_USER_TEMPLATE",
    "PROMPT_SPECS",
    "RUBRIC_PROMPT_SHA256",
    "RUBRIC_PROMPT_VERSION",
    "RUBRIC_SYSTEM_PROMPT",
    "SOFTWARE_FIXTURE_EVIDENCE_VERSION",
    "TAB_TIME_UNIT",
    "TEMPO_UNIT",
    "UNAVAILABLE_ACCOUNTING",
    "ZERO_SHOT_PROMPT_SHA256",
    "ZERO_SHOT_PROMPT_VERSION",
    "ZERO_SHOT_SYSTEM_PROMPT",
    "AgreementStatus",
    "BinaryVerdict",
    "CallAccounting",
    "CellSummary",
    "CrossProviderAuthorization",
    "CrossProviderAvailability",
    "CrossProviderStatus",
    "JudgeClient",
    "JudgeInputError",
    "JudgeItem",
    "JudgeResult",
    "JudgeRow",
    "LabelProvenance",
    "LabelSource",
    "PromptCondition",
    "PromptSpec",
    "ReferenceLabel",
    "ScheduleEntry",
    "SoftwareFixtureResult",
    "build_judge_schedule",
    "judge_proposition_sha256",
    "parse_judge_result",
    "run_software_fixture",
]
