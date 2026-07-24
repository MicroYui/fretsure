"""Strict public input contract for the playability oracle and solver.

Input validity is deliberately separate from playability.  GREEN/AMBER/RED are
judgments about a *valid* ordinary six-string :class:`~fretsure.tab.Tab` under a
valid profile.  Malformed, hostile, or out-of-domain data raises a typed input
error before any geometric/temporal predicate runs, so invalid rows can never
be misreported as RED or enter a human-playability confusion matrix.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import cast

from fretsure.ir import Note, VoiceRole
from fretsure.oracle.profiles import (
    MAX_SUPPORTED_FRET,
    Profile,
    snapshot_profile,
    validate_profile,
)
from fretsure.tab import (
    MAX_FRACTION_COMPONENT_BITS,
    MAX_TAB_NOTES,
    RightFinger,
    Tab,
    TabNote,
)

ORACLE_INPUT_SCHEMA_VERSION = "tab-input@0.2.0"

# These match the importer envelope.  The JSON adapter exports the same values;
# keeping the semantic validator bounded also protects direct Python callers.
MAX_NOTES_PER_ONSET = 64
MIN_TEMPO_BPM = 1.0
MAX_TEMPO_BPM = 1_000.0
MAX_BEATS_PER_BAR = 32
MAX_SOLVER_BEAM = 1_024
MAX_SOLVER_FRAME_CONFIGS = 48
MAX_SOLVER_FRAME_FINGERINGS = 64
MAX_SOLVER_FINAL_CHECKS = 16
MAX_AGENT_CANDIDATES = 64
MAX_AGENT_REPAIR_ITERS = 64
# Work units include weighted extension predicates, state-selection rescans,
# configuration generation, and three-profile final-checker work.  The threshold
# is calibrated so the 500-frame long-search regression remains admitted while
# the former ~39 s / 105 MiB high-branching case is rejected before generation.
MAX_SOLVER_WORK_UNITS = 12_000_000

# Wall-relevant work coefficients.  One extension advances two independent
# finite oracle states: optimistic admission and pessimistic GREEN-prefix
# viability.  Each performs several bounded active-note scans plus four static
# left-hand predicate calls.  The doubled conservative charge also covers the
# extra state retained per extension: at most six active strings, four fixed RH
# history cells, and one shift summary for each profile.  Parent chains retain at
# most the cumulative selected states already charged as extensions.  The
# selection pass then sorts all extensions and may repeatedly scan variants while
# retaining geometry/LH/RH diversity.  These constants deliberately overcharge
# those bounded operations instead of pretending that one state×config pair is
# one unit.
_SOLVER_EXTENSION_WORK_PER_CONFIG = 128
_SOLVER_SELECTION_RESCAN_PASSES = 4

# ``check_playability`` can evaluate optimistic, pessimistic and median profiles.
# Keep this envelope shared with the gold-statistics path so final solver gates
# cannot silently revert to a linear ``4 * notes`` estimate.
_CHECKER_PROFILE_EVALUATIONS = 3
_CHECKER_LINEAR_PASSES_PER_PROFILE = 12
_CHECKER_SORT_PASSES_PER_PROFILE = 10
_CHECKER_PAIR_PASSES_PER_PROFILE = 2
_CHECKER_ROW_BASE_WORK = 1_024
_CHECKER_MAX_ACTIVE_NOTES_PER_FRAME = 6

_RIGHT_FINGERS = frozenset({"p", "i", "m", "a"})
_VOICE_ROLES = frozenset({"melody", "bass", "harmony"})
_MISSING = object()


class OracleInputCode(StrEnum):
    """Stable machine-readable input-domain failure codes."""

    TAB_TYPE = "TAB_TYPE"
    NOTES_TYPE = "NOTES_TYPE"
    EMPTY_TAB = "EMPTY_TAB"
    TOO_MANY_NOTES = "TOO_MANY_NOTES"
    FRAME_TOO_LARGE = "FRAME_TOO_LARGE"
    TUNING_TYPE = "TUNING_TYPE"
    TUNING_LENGTH = "TUNING_LENGTH"
    TUNING_PITCH = "TUNING_PITCH"
    TUNING_ORDER = "TUNING_ORDER"
    CAPO = "CAPO"
    CAPO_RANGE = "CAPO_RANGE"
    PROFILE_TYPE = "PROFILE_TYPE"
    PROFILE_INVALID = "PROFILE_INVALID"
    TEMPO = "TEMPO"
    BEATS_PER_BAR = "BEATS_PER_BAR"
    NOTE_TYPE = "NOTE_TYPE"
    NOTE_FIELD_MISSING = "NOTE_FIELD_MISSING"
    ONSET_TYPE = "ONSET_TYPE"
    ONSET_RANGE = "ONSET_RANGE"
    DURATION_TYPE = "DURATION_TYPE"
    DURATION_RANGE = "DURATION_RANGE"
    FRACTION_TOO_LARGE = "FRACTION_TOO_LARGE"
    FRACTION_INVALID = "FRACTION_INVALID"
    STRING = "STRING"
    FRET_TYPE = "FRET_TYPE"
    FRET_RANGE = "FRET_RANGE"
    LEFT_FINGER = "LEFT_FINGER"
    RIGHT_FINGER = "RIGHT_FINGER"
    SOUNDING_PITCH_RANGE = "SOUNDING_PITCH_RANGE"
    SOLVER_NOTES_TYPE = "SOLVER_NOTES_TYPE"
    PITCH = "PITCH"
    VOICE = "VOICE"
    DUPLICATE_ONSET_PITCH = "DUPLICATE_ONSET_PITCH"
    BEAM = "BEAM"
    SOLVER_WORK_LIMIT = "SOLVER_WORK_LIMIT"
    CANDIDATE_COUNT = "CANDIDATE_COUNT"
    REPAIR_ITERATIONS = "REPAIR_ITERATIONS"
    BOOLEAN_CONTROL = "BOOLEAN_CONTROL"


@dataclass(frozen=True, slots=True)
class OracleInputDiagnostic:
    """One deterministic, serializable input-contract failure."""

    code: OracleInputCode
    path: str
    message: str


class InputContractError(ValueError):
    """Base class for typed trust-boundary failures."""

    diagnostics: tuple[OracleInputDiagnostic, ...]

    def __init__(self, diagnostics: tuple[OracleInputDiagnostic, ...]) -> None:
        if not diagnostics:
            raise ValueError("an input-contract error requires at least one diagnostic")
        self.diagnostics = diagnostics
        detail = "; ".join(
            f"{diagnostic.code.value} at {diagnostic.path}: {diagnostic.message}"
            for diagnostic in diagnostics
        )
        super().__init__(detail)


class OracleInputError(InputContractError):
    """The oracle cannot judge malformed or unsupported input."""


class SolverInputError(InputContractError):
    """The solver cannot search malformed or unsupported target data."""


def _diagnostic(code: OracleInputCode, path: str, message: str) -> OracleInputDiagnostic:
    return OracleInputDiagnostic(code=code, path=path, message=message)


def _ordered(
    diagnostics: list[OracleInputDiagnostic],
) -> tuple[OracleInputDiagnostic, ...]:
    return tuple(sorted(diagnostics, key=lambda item: (item.path, item.code.value, item.message)))


def _read_field(value: object, name: str) -> object:
    try:
        return object.__getattribute__(value, name)
    except (AttributeError, TypeError):
        return _MISSING


def _snapshot_profile(profile: object) -> object:
    """Detach an exact Profile from its externally reachable ``__dict__``.

    ``frozen=True`` prevents ordinary assignment, but low-level Python callers
    can still mutate a dataclass between validation and use.  Capture each
    field once into an object that only this module can reach; validation and
    all downstream arithmetic then operate on that snapshot.
    """

    return snapshot_profile(profile)


def _snapshot_tuning(tuning: object) -> object:
    if type(tuning) is not tuple:
        return tuning
    # Six strings are the only valid semantic domain.  Copy at most one extra
    # item so an already-invalid giant tuple cannot make validation allocate a
    # second giant tuple merely to report TUNING_LENGTH.
    return tuple(tuning[:7])


def _snapshot_fraction(value: object) -> object:
    """Copy an exact Fraction without retaining mutable private components."""

    if type(value) is not Fraction:
        return value
    try:
        numerator = object.__getattribute__(value, "_numerator")
        denominator = object.__getattribute__(value, "_denominator")
    except (AttributeError, TypeError):
        numerator = _MISSING
        denominator = _MISSING
    # Never retain a hostile int subclass or arbitrary injected component.
    safe_numerator = numerator if type(numerator) is int else _MISSING
    safe_denominator = denominator if type(denominator) is int else _MISSING
    snapshot = object.__new__(Fraction)
    object.__setattr__(snapshot, "_numerator", safe_numerator)
    object.__setattr__(snapshot, "_denominator", safe_denominator)
    return snapshot


def _snapshot_tab_note(note: object) -> object:
    if type(note) is not TabNote:
        return note
    return TabNote(
        cast(Fraction, _snapshot_fraction(_read_field(note, "onset"))),
        cast(Fraction, _snapshot_fraction(_read_field(note, "duration"))),
        cast(int, _read_field(note, "string")),
        cast(int, _read_field(note, "fret")),
        cast(int, _read_field(note, "left_finger")),
        cast(RightFinger, _read_field(note, "right_finger")),
    )


def _snapshot_tab(tab: object) -> object:
    if type(tab) is not Tab:
        return tab
    notes = _read_field(tab, "notes")
    if type(notes) is tuple:
        # MAX+1 preserves the over-limit diagnosis while bounding the copy.
        notes = tuple(_snapshot_tab_note(note) for note in notes[: MAX_TAB_NOTES + 1])
    tuning = _snapshot_tuning(_read_field(tab, "tuning"))
    return Tab(
        cast(tuple[TabNote, ...], notes),
        cast(tuple[int, ...], tuning),
        cast(int, _read_field(tab, "capo")),
    )


def _snapshot_solver_note(note: object) -> object:
    if type(note) is not Note:
        return note
    return Note(
        cast(Fraction, _snapshot_fraction(_read_field(note, "onset"))),
        cast(Fraction, _snapshot_fraction(_read_field(note, "duration"))),
        cast(int, _read_field(note, "pitch")),
        cast(VoiceRole, _read_field(note, "voice")),
    )


def _snapshot_solver_notes(notes: object) -> object:
    if type(notes) not in (list, tuple):
        return notes
    safe_notes = cast(list[object] | tuple[object, ...], notes)
    return tuple(
        _snapshot_solver_note(note)
        for note in safe_notes[: MAX_TAB_NOTES + 1]
    )


def _fraction_diagnostics(
    value: object,
    *,
    path: str,
    type_code: OracleInputCode,
    range_code: OracleInputCode,
    positive: bool,
) -> list[OracleInputDiagnostic]:
    if type(value) is not Fraction:
        return [
            _diagnostic(
                type_code,
                path,
                "must be an exact fractions.Fraction value",
            )
        ]
    fraction = value
    try:
        numerator = object.__getattribute__(fraction, "_numerator")
        denominator = object.__getattribute__(fraction, "_denominator")
    except (AttributeError, TypeError):
        numerator = _MISSING
        denominator = _MISSING
    if type(numerator) is not int or type(denominator) is not int:
        return [
            _diagnostic(
                OracleInputCode.FRACTION_INVALID,
                path,
                "must contain exact integer numerator and denominator components",
            )
        ]
    if (
        numerator.bit_length() > MAX_FRACTION_COMPONENT_BITS
        or denominator.bit_length() > MAX_FRACTION_COMPONENT_BITS
    ):
        return [
            _diagnostic(
                OracleInputCode.FRACTION_TOO_LARGE,
                path,
                (
                    "numerator and denominator must each fit within "
                    f"{MAX_FRACTION_COMPONENT_BITS} bits"
                ),
            )
        ]
    if denominator <= 0 or math.gcd(numerator, denominator) != 1:
        return [
            _diagnostic(
                OracleInputCode.FRACTION_INVALID,
                path,
                "must be reduced and have a positive denominator",
            )
        ]
    if (positive and numerator <= 0) or (not positive and numerator < 0):
        relation = "greater than zero" if positive else "non-negative"
        return [_diagnostic(range_code, path, f"must be {relation}")]
    return []


def _profile_diagnostics(profile: object) -> list[OracleInputDiagnostic]:
    issues = validate_profile(profile)
    diagnostics: list[OracleInputDiagnostic] = []
    for issue in issues:
        code = (
            OracleInputCode.PROFILE_TYPE
            if issue.code == "PROFILE_TYPE"
            else OracleInputCode.PROFILE_INVALID
        )
        path = "profile" if issue.path == "$" else f"profile{issue.path[1:]}"
        diagnostics.append(_diagnostic(code, path, f"{issue.code}: {issue.message}"))
    return diagnostics


def _tuning_diagnostics(tuning: object) -> list[OracleInputDiagnostic]:
    if type(tuning) is not tuple:
        return [
            _diagnostic(
                OracleInputCode.TUNING_TYPE,
                "tuning",
                "must be a tuple of six open-string MIDI integers",
            )
        ]
    if len(tuning) != 6:
        count = "at least 7" if len(tuning) == 7 else str(len(tuning))
        return [
            _diagnostic(
                OracleInputCode.TUNING_LENGTH,
                "tuning",
                f"must contain exactly six pitches, got {count}",
            )
        ]

    diagnostics: list[OracleInputDiagnostic] = []
    valid_pitches = True
    for index, pitch in enumerate(tuning):
        if type(pitch) is not int or not 0 <= pitch <= 127:
            valid_pitches = False
            diagnostics.append(
                _diagnostic(
                    OracleInputCode.TUNING_PITCH,
                    f"tuning[{index}]",
                    "must be an exact integer MIDI pitch in 0..127",
                )
            )
    if valid_pitches and any(
        left >= right for left, right in zip(tuning, tuning[1:], strict=False)
    ):
        diagnostics.append(
            _diagnostic(
                OracleInputCode.TUNING_ORDER,
                "tuning",
                "must be strictly increasing from the lowest to highest string",
            )
        )
    return diagnostics


def _capo_diagnostics(
    capo: object, *, tuning: object, profile: object
) -> list[OracleInputDiagnostic]:
    if type(capo) is not int or capo < 0:
        return [
            _diagnostic(
                OracleInputCode.CAPO,
                "capo",
                "must be an exact non-negative integer",
            )
        ]

    diagnostics: list[OracleInputDiagnostic] = []
    if type(profile) is Profile and not validate_profile(profile) and capo > profile.max_fret:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.CAPO_RANGE,
                "capo",
                f"must not exceed profile.max_fret ({profile.max_fret})",
            )
        )
    if (
        type(tuning) is tuple
        and len(tuning) == 6
        and all(type(pitch) is int and 0 <= pitch <= 127 for pitch in tuning)
        and any(pitch + capo > 127 for pitch in tuning)
    ):
        diagnostics.append(
            _diagnostic(
                OracleInputCode.CAPO_RANGE,
                "capo",
                "tuning plus capo must remain in the MIDI range 0..127",
            )
        )
    return diagnostics


def _tempo_diagnostics(tempo_bpm: object) -> list[OracleInputDiagnostic]:
    if type(tempo_bpm) not in (int, float):
        return [
            _diagnostic(
                OracleInputCode.TEMPO,
                "tempo_bpm",
                "must be an exact built-in int or float; bool and subclasses are not accepted",
            )
        ]
    try:
        tempo = float(cast(int | float, tempo_bpm))
    except OverflowError:
        tempo = math.inf
    if not math.isfinite(tempo) or not MIN_TEMPO_BPM <= tempo <= MAX_TEMPO_BPM:
        return [
            _diagnostic(
                OracleInputCode.TEMPO,
                "tempo_bpm",
                f"must be finite and within {MIN_TEMPO_BPM:g}..{MAX_TEMPO_BPM:g} BPM",
            )
        ]
    return []


def _beats_per_bar_diagnostics(beats_per_bar: object) -> list[OracleInputDiagnostic]:
    if type(beats_per_bar) is not int or not 1 <= beats_per_bar <= MAX_BEATS_PER_BAR:
        return [
            _diagnostic(
                OracleInputCode.BEATS_PER_BAR,
                "beats_per_bar",
                f"must be an exact integer in 1..{MAX_BEATS_PER_BAR}",
            )
        ]
    return []


def ensure_candidate_count(value: object, *, path: str = "n") -> int:
    """Return an exact bounded best-of count or raise a typed failure."""

    if type(value) is not int or not 0 <= value <= MAX_AGENT_CANDIDATES:
        raise SolverInputError(
            (
                _diagnostic(
                    OracleInputCode.CANDIDATE_COUNT,
                    path,
                    (
                        "must be an exact integer in "
                        f"0..{MAX_AGENT_CANDIDATES}"
                    ),
                ),
            )
        )
    return value


def ensure_repair_iterations(value: object) -> int:
    """Return an exact bounded verifier-guided edit budget."""

    if type(value) is not int or not 0 <= value <= MAX_AGENT_REPAIR_ITERS:
        raise SolverInputError(
            (
                _diagnostic(
                    OracleInputCode.REPAIR_ITERATIONS,
                    "max_iters",
                    (
                        "must be an exact integer in "
                        f"0..{MAX_AGENT_REPAIR_ITERS}"
                    ),
                ),
            )
        )
    return value


def ensure_boolean_control(value: object, *, path: str) -> bool:
    """Return an exact bool without truthiness coercion or user hooks."""

    if type(value) is not bool:
        raise SolverInputError(
            (
                _diagnostic(
                    OracleInputCode.BOOLEAN_CONTROL,
                    path,
                    "must be an exact bool",
                ),
            )
        )
    return value


def _validate_instrument_config_snapshot(
    tuning: object,
    capo: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
) -> tuple[OracleInputDiagnostic, ...]:
    diagnostics = _profile_diagnostics(profile)
    diagnostics.extend(_tuning_diagnostics(tuning))
    diagnostics.extend(_capo_diagnostics(capo, tuning=tuning, profile=profile))
    diagnostics.extend(_tempo_diagnostics(tempo_bpm))
    return _ordered(diagnostics)


def validate_instrument_config(
    tuning: object,
    capo: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
) -> tuple[OracleInputDiagnostic, ...]:
    """Validate one detached shared tuning/capo/profile/tempo snapshot."""

    return _validate_instrument_config_snapshot(
        _snapshot_tuning(tuning),
        capo,
        _snapshot_profile(profile),
        tempo_bpm=tempo_bpm,
    )


def ensure_profile(profile: object) -> Profile:
    """Validate and return one detached canonical profile snapshot."""

    snapshot = _snapshot_profile(profile)
    diagnostics = _ordered(_profile_diagnostics(snapshot))
    if diagnostics:
        raise OracleInputError(diagnostics)
    return cast(Profile, snapshot)


def ensure_instrument_config(
    tuning: object,
    capo: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
) -> tuple[tuple[int, ...], int, Profile, float]:
    """Validate and detach the shared instrument/profile/tempo configuration."""

    tuning_snapshot = _snapshot_tuning(tuning)
    profile_snapshot = _snapshot_profile(profile)
    diagnostics = _validate_instrument_config_snapshot(
        tuning_snapshot,
        capo,
        profile_snapshot,
        tempo_bpm=tempo_bpm,
    )
    if diagnostics:
        raise SolverInputError(diagnostics)
    return (
        cast(tuple[int, ...], tuning_snapshot),
        cast(int, capo),
        cast(Profile, profile_snapshot),
        float(cast(int | float, tempo_bpm)),
    )


def _tab_note_diagnostics(
    note: object,
    *,
    index: int,
    tuning: object,
    capo: object,
) -> tuple[list[OracleInputDiagnostic], Fraction | None]:
    base = f"tab.notes[{index}]"
    if type(note) is not TabNote:
        return (
            [
                _diagnostic(
                    OracleInputCode.NOTE_TYPE,
                    base,
                    "must be an exact TabNote instance",
                )
            ],
            None,
        )

    diagnostics: list[OracleInputDiagnostic] = []
    values = {
        name: _read_field(note, name)
        for name in (
            "onset",
            "duration",
            "string",
            "fret",
            "left_finger",
            "right_finger",
        )
    }
    for name, value in values.items():
        if value is _MISSING:
            diagnostics.append(
                _diagnostic(
                    OracleInputCode.NOTE_FIELD_MISSING,
                    f"{base}.{name}",
                    f"{name} is missing",
                )
            )

    onset = values["onset"]
    if onset is not _MISSING:
        diagnostics.extend(
            _fraction_diagnostics(
                onset,
                path=f"{base}.onset",
                type_code=OracleInputCode.ONSET_TYPE,
                range_code=OracleInputCode.ONSET_RANGE,
                positive=False,
            )
        )
    duration = values["duration"]
    if duration is not _MISSING:
        diagnostics.extend(
            _fraction_diagnostics(
                duration,
                path=f"{base}.duration",
                type_code=OracleInputCode.DURATION_TYPE,
                range_code=OracleInputCode.DURATION_RANGE,
                positive=True,
            )
        )

    string = values["string"]
    if string is not _MISSING and (type(string) is not int or not 0 <= string <= 5):
        diagnostics.append(
            _diagnostic(
                OracleInputCode.STRING,
                f"{base}.string",
                "must be an exact integer in 0..5",
            )
        )

    fret = values["fret"]
    if fret is not _MISSING:
        if type(fret) is not int:
            diagnostics.append(
                _diagnostic(
                    OracleInputCode.FRET_TYPE,
                    f"{base}.fret",
                    "must be an exact integer; bool is not accepted",
                )
            )
        elif not 0 <= fret <= MAX_SUPPORTED_FRET:
            diagnostics.append(
                _diagnostic(
                    OracleInputCode.FRET_RANGE,
                    f"{base}.fret",
                    f"must be within 0..{MAX_SUPPORTED_FRET}",
                )
            )

    left_finger = values["left_finger"]
    if left_finger is not _MISSING and (type(left_finger) is not int or not 0 <= left_finger <= 4):
        diagnostics.append(
            _diagnostic(
                OracleInputCode.LEFT_FINGER,
                f"{base}.left_finger",
                "must be an exact integer in 0..4",
            )
        )

    right_finger = values["right_finger"]
    if type(right_finger) is not str or right_finger not in _RIGHT_FINGERS:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.RIGHT_FINGER,
                f"{base}.right_finger",
                "must be one of p, i, m, a",
            )
        )

    if (
        type(tuning) is tuple
        and len(tuning) == 6
        and all(type(pitch) is int and 0 <= pitch <= 127 for pitch in tuning)
        and type(capo) is int
        and capo >= 0
        and type(string) is int
        and 0 <= string <= 5
        and type(fret) is int
        and 0 <= fret <= MAX_SUPPORTED_FRET
        and tuning[string] + capo + fret > 127
    ):
        diagnostics.append(
            _diagnostic(
                OracleInputCode.SOUNDING_PITCH_RANGE,
                f"{base}.fret",
                "sounding pitch must remain in the MIDI range 0..127",
            )
        )

    valid_onset = (
        onset
        if type(onset) is Fraction and not any(item.path == f"{base}.onset" for item in diagnostics)
        else None
    )
    return diagnostics, valid_onset


def _validate_oracle_input_snapshot(
    tab: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
    beats_per_bar: object = 4,
) -> tuple[OracleInputDiagnostic, ...]:
    """Return every safe-to-compute input issue in deterministic path order."""

    diagnostics = list(
        _validate_instrument_config_snapshot(
            _read_field(tab, "tuning") if type(tab) is Tab else _MISSING,
            _read_field(tab, "capo") if type(tab) is Tab else _MISSING,
            profile,
            tempo_bpm=tempo_bpm,
        )
    )
    diagnostics.extend(_beats_per_bar_diagnostics(beats_per_bar))
    if type(tab) is not Tab:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.TAB_TYPE,
                "tab",
                "must be an exact Tab instance",
            )
        )
        # The placeholder tuning/capo diagnostics are not useful when no Tab
        # object exists; keep the failure focused and deterministic.
        diagnostics = [
            item
            for item in diagnostics
            if item.path not in {"tuning", "capo"} and not item.path.startswith("tuning[")
        ]
        return _ordered(diagnostics)

    notes = _read_field(tab, "notes")
    tuning = _read_field(tab, "tuning")
    capo = _read_field(tab, "capo")
    if type(notes) is not tuple:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.NOTES_TYPE,
                "tab.notes",
                "must be a tuple of TabNote objects",
            )
        )
        return _ordered(diagnostics)
    if not notes:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.EMPTY_TAB,
                "tab.notes",
                "an empty tab cannot receive a playability certification",
            )
        )
    if len(notes) > MAX_TAB_NOTES:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.TOO_MANY_NOTES,
                "tab.notes",
                (
                    f"contains at least {MAX_TAB_NOTES + 1} notes; "
                    f"limit is {MAX_TAB_NOTES}"
                ),
            )
        )

    valid_onsets: list[Fraction] = []
    for index, note in enumerate(notes[: MAX_TAB_NOTES + 1]):
        note_diagnostics, onset = _tab_note_diagnostics(note, index=index, tuning=tuning, capo=capo)
        diagnostics.extend(note_diagnostics)
        if onset is not None:
            valid_onsets.append(onset)
    if valid_onsets:
        largest_frame = max(Counter(valid_onsets).values())
        if largest_frame > MAX_NOTES_PER_ONSET:
            diagnostics.append(
                _diagnostic(
                    OracleInputCode.FRAME_TOO_LARGE,
                    "tab.notes",
                    (
                        f"one onset contains {largest_frame} attacks; resource limit is "
                        f"{MAX_NOTES_PER_ONSET}"
                    ),
                )
            )
    return _ordered(diagnostics)


def validate_oracle_input(
    tab: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
    beats_per_bar: object = 4,
) -> tuple[OracleInputDiagnostic, ...]:
    """Return issues for one detached snapshot in deterministic path order."""

    return _validate_oracle_input_snapshot(
        _snapshot_tab(tab),
        _snapshot_profile(profile),
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
    )


def ensure_oracle_input(
    tab: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
    beats_per_bar: object = 4,
) -> tuple[Tab, Profile, float, int]:
    """Validate and return a detached canonical oracle-input snapshot."""

    tab_snapshot = _snapshot_tab(tab)
    profile_snapshot = _snapshot_profile(profile)
    diagnostics = _validate_oracle_input_snapshot(
        tab_snapshot,
        profile_snapshot,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
    )
    if diagnostics:
        raise OracleInputError(diagnostics)
    return (
        cast(Tab, tab_snapshot),
        cast(Profile, profile_snapshot),
        float(cast(int | float, tempo_bpm)),
        cast(int, beats_per_bar),
    )


def _solver_note_diagnostics(
    note: object, *, index: int
) -> tuple[list[OracleInputDiagnostic], tuple[Fraction, int] | None]:
    base = f"notes[{index}]"
    if type(note) is not Note:
        return (
            [
                _diagnostic(
                    OracleInputCode.NOTE_TYPE,
                    base,
                    "must be an exact Note instance",
                )
            ],
            None,
        )

    diagnostics: list[OracleInputDiagnostic] = []
    onset = _read_field(note, "onset")
    duration = _read_field(note, "duration")
    pitch = _read_field(note, "pitch")
    voice = _read_field(note, "voice")
    for name, value in (
        ("onset", onset),
        ("duration", duration),
        ("pitch", pitch),
        ("voice", voice),
    ):
        if value is _MISSING:
            diagnostics.append(
                _diagnostic(
                    OracleInputCode.NOTE_FIELD_MISSING,
                    f"{base}.{name}",
                    f"{name} is missing",
                )
            )
    if onset is not _MISSING:
        diagnostics.extend(
            _fraction_diagnostics(
                onset,
                path=f"{base}.onset",
                type_code=OracleInputCode.ONSET_TYPE,
                range_code=OracleInputCode.ONSET_RANGE,
                positive=False,
            )
        )
    if duration is not _MISSING:
        diagnostics.extend(
            _fraction_diagnostics(
                duration,
                path=f"{base}.duration",
                type_code=OracleInputCode.DURATION_TYPE,
                range_code=OracleInputCode.DURATION_RANGE,
                positive=True,
            )
        )
    if type(pitch) is not int or not 0 <= pitch <= 127:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.PITCH,
                f"{base}.pitch",
                "must be an exact integer MIDI pitch in 0..127",
            )
        )
    if type(voice) is not str or voice not in _VOICE_ROLES:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.VOICE,
                f"{base}.voice",
                "must be one of melody, bass, harmony",
            )
        )
    identity = (
        (onset, pitch)
        if type(onset) is Fraction
        and type(pitch) is int
        and not any(item.path in {f"{base}.onset", f"{base}.pitch"} for item in diagnostics)
        else None
    )
    return diagnostics, identity


def _solver_frame_placement_counts(
    pitches: tuple[int, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
) -> dict[int, int]:
    """Count distinct-string placements by their number of fretted notes."""

    width = len(pitches)
    if width == 0 or width > 4:
        return {}

    candidate_lists: list[tuple[tuple[int, int], ...]] = []
    for pitch in pitches:
        placements = tuple(
            (string, pitch - (open_pitch + capo))
            for string, open_pitch in enumerate(tuning)
            if 0 <= pitch - (open_pitch + capo) <= profile.max_fret
        )
        if not placements:
            return {}
        candidate_lists.append(placements)

    states: dict[tuple[int, int], int] = {(0, 0): 1}
    for placements in candidate_lists:
        next_states: dict[tuple[int, int], int] = {}
        for (mask, fretted_count), count in states.items():
            for string, fret in placements:
                bit = 1 << string
                if mask & bit:
                    continue
                key = (mask | bit, fretted_count + (1 if fret > 0 else 0))
                next_states[key] = next_states.get(key, 0) + count
        states = next_states
        if not states:
            return {}

    by_fretted: dict[int, int] = {}
    for (_mask, fretted_count), count in states.items():
        by_fretted[fretted_count] = by_fretted.get(fretted_count, 0) + count
    return by_fretted


def solver_frame_config_count_upper_bound(
    pitches: tuple[int, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
) -> int:
    """Upper-bound retained configs for one pitch signature before generation."""

    if not pitches:
        return 1
    width = len(pitches)
    placement_counts = _solver_frame_placement_counts(pitches, tuning, capo, profile)
    if not placement_counts:
        return 0
    right_variants = math.comb(4, width)
    candidate_count = sum(
        placement_count
        * min(MAX_SOLVER_FRAME_FINGERINGS, 1 if fretted == 0 else 4**fretted)
        * right_variants
        for fretted, placement_count in placement_counts.items()
    )
    return min(MAX_SOLVER_FRAME_CONFIGS, candidate_count)


def solver_frame_generation_work_upper_bound(
    pitches: tuple[int, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
) -> int:
    """Conservative work bound for one distinct pitch-frame configuration build.

    Callers must first validate the instrument/profile and note domains.  A
    raw Cartesian-product term covers combos rejected for reusing a string.  A
    small bitmask DP then counts candidate placements on distinct strings and
    separates them by the number of fretted notes.  For every such placement we
    charge all ``4**fretted`` fingering trials, one static feasibility check per
    potentially valid fingering (capped like the generator), every legal RH
    selection, both bounded-retention map operations, retained-map sorting, and
    the bounded diversity-selection scans.  This is cheap to compute (at most 64
    masks x five fret counts) and covers work performed before the returned list
    is truncated.
    """

    width = len(pitches)
    if width == 0 or width > 4:
        return 0
    # ``frame_configs`` visits the complete Cartesian product before discarding
    # assignments that reuse a string.  Count those loop iterations explicitly;
    # a frame whose pitches are each reachable but only on the same string still
    # performs this work before returning ``NO_FRAME_CONFIG``.
    raw_cartesian_work = 1
    for pitch in pitches:
        candidate_count = sum(
            1
            for open_pitch in tuning
            if 0 <= pitch - (open_pitch + capo) <= profile.max_fret
        )
        if candidate_count == 0:
            # ``solve_fingering`` returns ``UNREACHABLE_PITCH`` before calling
            # ``frame_configs`` when any individual pitch has no placement.
            return 0
        raw_cartesian_work *= candidate_count

    placement_counts = _solver_frame_placement_counts(pitches, tuning, capo, profile)
    if not placement_counts:
        return raw_cartesian_work

    right_variants = math.comb(4, width)
    work = raw_cartesian_work
    retained_values = 0
    geometry_count = sum(placement_counts.values())
    for fretted_count, placement_count in placement_counts.items():
        fingering_trials = 1 if fretted_count == 0 else 4**fretted_count
        potentially_valid = min(MAX_SOLVER_FRAME_FINGERINGS, fingering_trials)
        emitted = potentially_valid * right_variants
        # Each emitted candidate is constructed and offered to two independently
        # bounded retention maps.  A bounded map scan is one work unit here; its
        # concrete width is separately fixed by MAX_SOLVER_FRAME_CONFIGS.
        per_placement = fingering_trials + potentially_valid + emitted * 3
        work += placement_count * per_placement
        retained_values += placement_count * min(MAX_SOLVER_FRAME_CONFIGS, emitted)

    # Two retained maps are sorted per geometry; their width is capped at 48.
    retained_sort_work = 2 * retained_values * max(
        1, MAX_SOLVER_FRAME_CONFIGS.bit_length()
    )
    geometry_sort_work = geometry_count * max(1, geometry_count.bit_length())
    diversity_scan_work = MAX_SOLVER_FRAME_CONFIGS * retained_values
    work += retained_sort_work + geometry_sort_work + diversity_scan_work
    return work


def oracle_checker_work_upper_bound(
    note_count: int,
    attacks_per_onset: tuple[int, ...],
) -> int:
    """Worst-case three-profile checker work for already validated counts."""

    sort_work = note_count * max(1, note_count.bit_length())
    attack_pair_work = sum(count * count for count in attacks_per_onset)
    # Left-hand predicates operate on sounding frames, not just simultaneous
    # attacks.  The validated ordinary-guitar domain has six physical strings,
    # and ``_indexed_sounding_frames`` retains at most one active representative
    # per string, so 6**2 is a safe per-frame ordered-pair envelope even with
    # long sustains.
    active_pair_work = (
        len(attacks_per_onset) * _CHECKER_MAX_ACTIVE_NOTES_PER_FRAME**2
    )
    per_profile = (
        _CHECKER_LINEAR_PASSES_PER_PROFILE * note_count
        + _CHECKER_SORT_PASSES_PER_PROFILE * sort_work
        + _CHECKER_PAIR_PASSES_PER_PROFILE
        * (attack_pair_work + active_pair_work)
    )
    return _CHECKER_ROW_BASE_WORK + _CHECKER_PROFILE_EVALUATIONS * per_profile


def _validate_solver_contract_snapshot(
    notes: object,
    tuning: object,
    capo: object,
    profile: object,
    *,
    tempo_bpm: object,
    beam: object,
    include_search_work: bool,
) -> tuple[OracleInputDiagnostic, ...]:
    """Shared structural validation with an optional concrete-search envelope."""

    instrument_diagnostics = _validate_instrument_config_snapshot(
        tuning, capo, profile, tempo_bpm=tempo_bpm
    )
    diagnostics = list(instrument_diagnostics)
    if type(beam) is not int or not 1 <= beam <= MAX_SOLVER_BEAM:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.BEAM,
                "beam",
                f"must be an exact integer in 1..{MAX_SOLVER_BEAM}",
            )
        )
    # Only the two inert built-in sequence containers cross this trust
    # boundary.  An arbitrary ``collections.abc.Sequence`` may execute user
    # code from ``__len__``/``__getitem__`` while validation is still trying to
    # fail closed, or claim a finite length and then yield forever.  Public
    # adapters already materialize JSON arrays as a list/tuple, so accepting
    # custom sequence implementations buys no useful interoperability here.
    if type(notes) not in (list, tuple):
        diagnostics.append(
            _diagnostic(
                OracleInputCode.SOLVER_NOTES_TYPE,
                "notes",
                "must be a built-in list or tuple of Note objects",
            )
        )
        return _ordered(diagnostics)
    safe_notes = cast(list[object] | tuple[object, ...], notes)
    if len(safe_notes) > MAX_TAB_NOTES:
        diagnostics.append(
            _diagnostic(
                OracleInputCode.TOO_MANY_NOTES,
                "notes",
                (
                    f"contains at least {MAX_TAB_NOTES + 1} notes; "
                    f"limit is {MAX_TAB_NOTES}"
                ),
            )
        )

    identities: dict[tuple[Fraction, int], int] = {}
    valid_onsets: list[Fraction] = []
    pitches_by_onset: dict[Fraction, list[int]] = {}
    for index, note in enumerate(safe_notes[: MAX_TAB_NOTES + 1]):
        note_diagnostics, identity = _solver_note_diagnostics(note, index=index)
        diagnostics.extend(note_diagnostics)
        if identity is None:
            continue
        onset, pitch = identity
        valid_onsets.append(onset)
        pitches_by_onset.setdefault(onset, []).append(pitch)
        prior = identities.get(identity)
        if prior is not None:
            diagnostics.append(
                _diagnostic(
                    OracleInputCode.DUPLICATE_ONSET_PITCH,
                    f"notes[{index}]",
                    (
                        f"duplicates pitch {pitch} at onset {onset} from notes[{prior}]; "
                        "duration/voice would be ambiguous"
                    ),
                )
            )
        else:
            identities[identity] = index
    if valid_onsets:
        largest_frame = max(Counter(valid_onsets).values())
        if largest_frame > MAX_NOTES_PER_ONSET:
            diagnostics.append(
                _diagnostic(
                    OracleInputCode.FRAME_TOO_LARGE,
                    "notes",
                    (
                        f"one onset contains {largest_frame} targets; resource limit is "
                        f"{MAX_NOTES_PER_ONSET}"
                    ),
                )
            )
        if (
            include_search_work
            and type(beam) is int
            and 1 <= beam <= MAX_SOLVER_BEAM
        ):
            frame_pitches_in_order = tuple(
                tuple(sorted(pitches_by_onset[onset]))
                for onset in sorted(pitches_by_onset)
            )
            config_caps: dict[tuple[int, ...], int] = {}
            generation_work = 0
            if not instrument_diagnostics:
                safe_tuning = cast(tuple[int, ...], tuning)
                safe_capo = cast(int, capo)
                safe_profile = cast(Profile, profile)
                distinct_frames = set(frame_pitches_in_order)
                generation_work = sum(
                    solver_frame_generation_work_upper_bound(
                        pitches,
                        safe_tuning,
                        safe_capo,
                        safe_profile,
                    )
                    for pitches in distinct_frames
                )
                config_caps = {
                    pitches: solver_frame_config_count_upper_bound(
                        pitches,
                        safe_tuning,
                        safe_capo,
                        safe_profile,
                    )
                    for pitches in distinct_frames
                }

            # Follow the same bounded state growth as the solver instead of
            # charging every frame as if a full beam and 48 configs already
            # existed.  This preserves long, narrow searches while rejecting a
            # high-branching beam before it allocates or sorts the extensions.
            state_count = 1
            extension_work = 0
            selection_work = 0
            if config_caps:
                for pitches in frame_pitches_in_order:
                    config_count = config_caps[pitches]
                    extension_count = state_count * config_count
                    extension_work += (
                        extension_count * _SOLVER_EXTENSION_WORK_PER_CONFIG
                    )
                    if extension_count == 0:
                        state_count = 0
                        break
                    selected_count = min(beam, extension_count)
                    selection_work += extension_count * max(
                        1, extension_count.bit_length()
                    )
                    selection_work += (
                        _SOLVER_SELECTION_RESCAN_PASSES
                        * selected_count
                        * extension_count
                    )
                    state_count = selected_count

            final_checks = min(state_count, MAX_SOLVER_FINAL_CHECKS)
            attacks_per_onset = tuple(
                len(pitches_by_onset[onset]) for onset in sorted(pitches_by_onset)
            )
            final_work = final_checks * (
                len(valid_onsets)
                + oracle_checker_work_upper_bound(
                    len(valid_onsets),
                    attacks_per_onset,
                )
            )
            estimated_work = (
                extension_work
                + selection_work
                + generation_work
                + final_work
            )
            if estimated_work > MAX_SOLVER_WORK_UNITS:
                diagnostics.append(
                    _diagnostic(
                        OracleInputCode.SOLVER_WORK_LIMIT,
                        "notes",
                        (
                            f"estimated bounded search work is {estimated_work} units "
                            f"(extensions={extension_work}, state_selection="
                            f"{selection_work}, config_generation={generation_work}, "
                            f"final_checks={final_work}); limit is "
                            f"{MAX_SOLVER_WORK_UNITS}"
                        ),
                    )
                )
    return _ordered(diagnostics)


def validate_solver_domain(
    notes: object,
    tuning: object,
    capo: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
) -> tuple[OracleInputDiagnostic, ...]:
    """Validate a solver target's inert domain without pricing a search.

    Proposal-only call sites use this boundary before prompt construction or
    deterministic note selection.  It validates the same notes, instrument,
    profile, and tempo as the solver, but deliberately does not pretend that a
    particular beam search will run.  Any path that actually calls
    :func:`solve_fingering` is revalidated by :func:`ensure_solver_input` with
    its concrete beam and full work envelope.
    """

    return _validate_solver_contract_snapshot(
        _snapshot_solver_notes(notes),
        _snapshot_tuning(tuning),
        capo,
        _snapshot_profile(profile),
        tempo_bpm=tempo_bpm,
        beam=1,
        include_search_work=False,
    )


def validate_solver_input(
    notes: object,
    tuning: object,
    capo: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
    beam: object = 16,
) -> tuple[OracleInputDiagnostic, ...]:
    """Validate all solver inputs before candidate generation or beam search."""

    return _validate_solver_contract_snapshot(
        _snapshot_solver_notes(notes),
        _snapshot_tuning(tuning),
        capo,
        _snapshot_profile(profile),
        tempo_bpm=tempo_bpm,
        beam=beam,
        include_search_work=True,
    )


def ensure_solver_domain(
    notes: object,
    tuning: object,
    capo: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
) -> tuple[tuple[Note, ...], tuple[int, ...], int, Profile, float]:
    """Validate and return a detached proposal-domain snapshot."""

    notes_snapshot = _snapshot_solver_notes(notes)
    tuning_snapshot = _snapshot_tuning(tuning)
    profile_snapshot = _snapshot_profile(profile)
    diagnostics = _validate_solver_contract_snapshot(
        notes_snapshot,
        tuning_snapshot,
        capo,
        profile_snapshot,
        tempo_bpm=tempo_bpm,
        beam=1,
        include_search_work=False,
    )
    if diagnostics:
        raise SolverInputError(diagnostics)
    return (
        cast(tuple[Note, ...], notes_snapshot),
        cast(tuple[int, ...], tuning_snapshot),
        cast(int, capo),
        cast(Profile, profile_snapshot),
        float(cast(int | float, tempo_bpm)),
    )


def ensure_solver_input(
    notes: object,
    tuning: object,
    capo: object,
    profile: object,
    *,
    tempo_bpm: object = 90.0,
    beam: object = 16,
) -> tuple[tuple[Note, ...], tuple[int, ...], int, Profile, float, int]:
    """Validate and return a detached bounded-search snapshot."""

    notes_snapshot = _snapshot_solver_notes(notes)
    tuning_snapshot = _snapshot_tuning(tuning)
    profile_snapshot = _snapshot_profile(profile)
    diagnostics = _validate_solver_contract_snapshot(
        notes_snapshot,
        tuning_snapshot,
        capo,
        profile_snapshot,
        tempo_bpm=tempo_bpm,
        beam=beam,
        include_search_work=True,
    )
    if diagnostics:
        raise SolverInputError(diagnostics)
    return (
        cast(tuple[Note, ...], notes_snapshot),
        cast(tuple[int, ...], tuning_snapshot),
        cast(int, capo),
        cast(Profile, profile_snapshot),
        float(cast(int | float, tempo_bpm)),
        cast(int, beam),
    )


__all__ = [
    "MAX_AGENT_CANDIDATES",
    "MAX_AGENT_REPAIR_ITERS",
    "MAX_BEATS_PER_BAR",
    "MAX_FRACTION_COMPONENT_BITS",
    "MAX_NOTES_PER_ONSET",
    "MAX_SOLVER_BEAM",
    "MAX_SOLVER_FRAME_CONFIGS",
    "MAX_SOLVER_FRAME_FINGERINGS",
    "MAX_SOLVER_FINAL_CHECKS",
    "MAX_SOLVER_WORK_UNITS",
    "MAX_TAB_NOTES",
    "MAX_TEMPO_BPM",
    "MIN_TEMPO_BPM",
    "ORACLE_INPUT_SCHEMA_VERSION",
    "InputContractError",
    "OracleInputCode",
    "OracleInputDiagnostic",
    "OracleInputError",
    "SolverInputError",
    "ensure_boolean_control",
    "ensure_candidate_count",
    "ensure_instrument_config",
    "ensure_oracle_input",
    "ensure_profile",
    "ensure_repair_iterations",
    "ensure_solver_domain",
    "ensure_solver_input",
    "oracle_checker_work_upper_bound",
    "validate_instrument_config",
    "validate_oracle_input",
    "validate_solver_domain",
    "validate_solver_input",
    "solver_frame_config_count_upper_bound",
    "solver_frame_generation_work_upper_bound",
]
