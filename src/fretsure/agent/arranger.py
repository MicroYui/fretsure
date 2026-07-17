"""LLM arrangement proposer.

The LLM decides musical intent only — which notes carry the melody/bass/harmony,
in which octaves — and emits a target note set as JSON. It never decides fingering
(the deterministic solver does). On any malformed reply we fall back honestly to
the Plan 2 rule stub, so the pipeline always has a target.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from typing import cast

from fretsure.agent.model_calls import ModelCallScopeFactory, model_call_scope
from fretsure.arrange.propose import propose_fingerstyle
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import MusicIR, Note, VoiceRole, snapshot_music_ir
from fretsure.llm.client import ConstantLLM, LLMClient, LLMIntegrityError, extract_json
from fretsure.oracle.input import ensure_solver_domain
from fretsure.oracle.profiles import MEDIAN_HAND, Profile

PROPOSAL_OBJECT_PROTOCOL_VERSION = "arrangement-proposal-object@0.1.0"
PROPOSAL_COMPACT_PROTOCOL_VERSION = "arrangement-proposal-compact@0.1.0"
ARRANGEMENT_UNISON_COALESCER_VERSION = "arrangement-unison-coalescer@0.1.0"

_ARRANGE_OBJECT_SYSTEM = (
    "You are a fingerstyle guitar arranger. Given a lead sheet, decide the musical "
    "intent for a solo fingerstyle arrangement: which notes carry the melody (always "
    "kept, as the top voice), which carry the bass, and optional inner 'harmony' notes. "
    "You decide notes and octaves, NOT fingering. Reply with ONLY a JSON object: "
    '{"notes": [{"onset": "<fraction>", "duration": "<fraction>", "pitch": <midi>, '
    '"voice": "melody|bass|harmony"}, ...]}. The melody must be present.'
)

_ARRANGE_COMPACT_SYSTEM = (
    "You are a fingerstyle guitar arranger. Given a long score, decide the musical "
    "intent for a solo fingerstyle arrangement: which notes carry the melody (always "
    "kept, as the top voice), which carry the bass, and optional inner harmony notes. "
    "You decide notes and octaves, NOT fingering. Reply with ONLY one JSON object in "
    "this exact lossless compact schema: "
    '{"schema": "arrangement-proposal-compact@0.1.0", "notes": [['
    '"<reduced onset fraction>", "<reduced duration fraction>", '
    '<midi integer>, "melody|bass|harmony"], ...]}. Fractions must use numerator/'
    "positive-denominator form such as 0/1 or 3/2. The melody must be present. "
    "Do not add fields, prose, or Markdown."
)

MIN_OUTPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS = 16_384
OBJECT_OUTPUT_TOKENS_PER_EVENT = 96
COMPACT_OUTPUT_TOKENS_PER_EVENT = 32
OUTPUT_TOKEN_FIXED_ALLOWANCE = 128
MAX_OBJECT_SOURCE_EVENTS = (
    MAX_OUTPUT_TOKENS - OUTPUT_TOKEN_FIXED_ALLOWANCE
) // OBJECT_OUTPUT_TOKENS_PER_EVENT
MAX_COMPACT_SOURCE_EVENTS = (
    MAX_OUTPUT_TOKENS - OUTPUT_TOKEN_FIXED_ALLOWANCE
) // COMPACT_OUTPUT_TOKENS_PER_EVENT
_SOURCE_CONTEXT_DIGEST_DOMAIN = b"fretsure:arrangement-source-context@0.1.0\0"
_PROMPT_DIGEST_DOMAIN = b"fretsure:arrangement-prompt@0.1.0\0"
_CANONICAL_FRACTION = re.compile(
    r"(?P<numerator>0|-?[1-9][0-9]*)/(?P<denominator>[1-9][0-9]*)\Z"
)


class ArrangementOutputProtocol(StrEnum):
    """Versioned one-call response encoding selected before model execution."""

    OBJECT = PROPOSAL_OBJECT_PROTOCOL_VERSION
    COMPACT = PROPOSAL_COMPACT_PROTOCOL_VERSION


def _prompt_sha256(text: str) -> str:
    return hashlib.sha256(_PROMPT_DIGEST_DOMAIN + text.encode("utf-8")).hexdigest()


PROPOSAL_OBJECT_SYSTEM_SHA256 = _prompt_sha256(_ARRANGE_OBJECT_SYSTEM)
PROPOSAL_COMPACT_SYSTEM_SHA256 = _prompt_sha256(_ARRANGE_COMPACT_SYSTEM)


class ArrangementCapacityError(ValueError):
    """A real-LLM proposal cannot faithfully encode every legal source event."""


class ProposalStatus(StrEnum):
    """Stable, outcome-only provenance for one bounded proposal attempt."""

    LLM_SUCCESS = "LLM_SUCCESS"
    PARSE_VALIDATION_FALLBACK = "PARSE_VALIDATION_FALLBACK"
    CALL_FAILURE_FALLBACK = "CALL_FAILURE_FALLBACK"
    CONSTANT_LLM_BYPASS = "CONSTANT_LLM_BYPASS"


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    """Immutable proposal target plus its non-sensitive execution outcome."""

    target: tuple[Note, ...]
    status: ProposalStatus
    llm_calls: int

    def __post_init__(self) -> None:
        if type(self.target) is not tuple:
            raise ValueError("target must be an exact tuple")
        if type(self.status) is not ProposalStatus:
            raise ValueError("status must be a ProposalStatus")
        if type(self.llm_calls) is not int or self.llm_calls not in (0, 1):
            raise ValueError("llm_calls must be an exact integer in 0..1")
        if (self.status is ProposalStatus.CONSTANT_LLM_BYPASS) != (self.llm_calls == 0):
            raise ValueError("only the ConstantLLM bypass may use zero LLM calls")

    @property
    def fallback_assisted(self) -> bool:
        """Whether malformed output or a failed call selected the rule fallback."""

        return self.status in {
            ProposalStatus.PARSE_VALIDATION_FALLBACK,
            ProposalStatus.CALL_FAILURE_FALLBACK,
        }


@dataclass(frozen=True)
class ArrangeGoal:
    style: str = "fingerstyle"
    tier: str = "intermediate"
    tuning: tuple[int, ...] = STANDARD_TUNING
    capo: int = 0
    tempo_bpm: float = 90.0
    extras: dict[str, str] = field(default_factory=dict)


def _render_arrangement_source_context(ir: MusicIR) -> str:
    mel = [n for n in ir.notes if n.voice == "melody"]
    bass = [n for n in ir.notes if n.voice == "bass"]
    harmony = [n for n in ir.notes if n.voice == "harmony"]

    def events(notes: list[Note]) -> str:
        return "; ".join(f"onset={n.onset} duration={n.duration} pitch={n.pitch}" for n in notes)

    chords = "; ".join(
        f"onset={c.onset} {c.symbol} root_pc={c.root_pc} "
        f"pitch_classes={','.join(str(pc) for pc in sorted(c.pitch_classes))}"
        for c in ir.chords
    )
    return (
        f"Key {ir.meta.key}, {ir.meta.time_sig[0]}/{ir.meta.time_sig[1]}, "
        f"source tempo {ir.meta.tempo_bpm} BPM.\n"
        f"Melody events: {events(mel)}\n"
        f"Source bass events: {events(bass)}\n"
        f"Source harmony-note events: {events(harmony)}\n"
        f"Chord annotations: {chords}"
    )


def arrangement_source_context(ir: MusicIR) -> str:
    """Return the stable source-only context shared by proposal baselines.

    Goal, tuning, capo, and effective-tempo instructions deliberately remain outside
    this renderer so callers can prove that different policies saw the same source
    facts without claiming that their tasks were identical.
    """

    return _render_arrangement_source_context(snapshot_music_ir(ir))


def arrangement_source_context_sha256(ir: MusicIR) -> str:
    """Digest the exact public source-context rendering with a stable domain tag."""

    encoded = arrangement_source_context(ir).encode("utf-8")
    return hashlib.sha256(_SOURCE_CONTEXT_DIGEST_DOMAIN + encoded).hexdigest()


def arrangement_solver_ir(ir: MusicIR) -> MusicIR:
    """Coalesce physically identical source unisons for the target/solver domain.

    The canonical source remains untouched for prompts, hashes, and fidelity.  A
    guitar cannot realize two distinct copies of one pitch at one onset, so the
    solver view retains the longest sounding duration and the strongest available
    melody/bass/harmony role without dropping any distinct audible pitch event.
    """

    source = snapshot_music_ir(ir)
    grouped: dict[tuple[Fraction, int], list[Note]] = {}
    for note in source.notes:
        grouped.setdefault((note.onset, note.pitch), []).append(note)
    voice_rank = {"melody": 0, "bass": 1, "harmony": 2}
    notes = tuple(
        Note(
            onset,
            max(note.duration for note in group),
            pitch,
            min(group, key=lambda note: voice_rank[note.voice]).voice,
        )
        for (onset, pitch), group in sorted(grouped.items())
    )
    return MusicIR(notes, tuple(source.chords), source.meta)


def _canonical_fraction(value: object) -> Fraction:
    if type(value) is not str or len(value) > 128:
        raise ValueError("compact fractions must be bounded canonical strings")
    match = _CANONICAL_FRACTION.fullmatch(value)
    if match is None:
        raise ValueError("compact fractions must use reduced numerator/denominator form")
    numerator = int(match.group("numerator"))
    denominator = int(match.group("denominator"))
    fraction = Fraction(numerator, denominator)
    if f"{fraction.numerator}/{fraction.denominator}" != value:
        raise ValueError("compact fractions must be reduced")
    return fraction


def _reject_duplicate_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("compact proposal objects cannot repeat keys")
        result[key] = value
    return result


def _balanced_json_object_text(reply: object) -> str:
    if type(reply) is not str:
        raise ValueError("compact proposal reply must be an exact string")
    start = reply.find("{")
    if start < 0:
        raise ValueError("compact proposal reply has no JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(reply)):
        character = reply[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return reply[start : index + 1]
    raise ValueError("compact proposal reply has no balanced JSON object")


def _extract_compact_json(reply: object) -> dict[str, object]:
    decoded = json.loads(
        _balanced_json_object_text(reply),
        object_pairs_hook=_reject_duplicate_object_pairs,
        parse_constant=lambda _token: (_ for _ in ()).throw(ValueError("non-finite number")),
    )
    if type(decoded) is not dict:
        raise ValueError("compact proposal reply must contain one object")
    return cast(dict[str, object], decoded)


def _parse_notes(
    obj: dict[str, object],
    protocol: ArrangementOutputProtocol,
) -> tuple[Note, ...]:
    if type(protocol) is not ArrangementOutputProtocol:
        raise ValueError("proposal protocol must be frozen before parsing")
    if protocol is ArrangementOutputProtocol.COMPACT:
        if set(obj) != {"schema", "notes"}:
            raise ValueError("compact proposal must contain the exact top-level fields")
        if obj["schema"] != PROPOSAL_COMPACT_PROTOCOL_VERSION:
            raise ValueError("compact proposal has the wrong schema version")
    raw = obj["notes"]
    if not isinstance(raw, list):
        raise ValueError("notes must be a list")
    notes: list[Note] = []
    seen: set[tuple[Fraction, int]] = set()
    for item in raw:
        if protocol is ArrangementOutputProtocol.COMPACT:
            if type(item) is not list or len(item) != 4:
                raise ValueError("each compact note must be a four-value array")
            onset = _canonical_fraction(item[0])
            duration = _canonical_fraction(item[1])
            raw_pitch = item[2]
            voice = item[3]
        else:
            if type(item) is not dict:
                raise ValueError("each note must be an object")
            if set(item) != {"onset", "duration", "pitch", "voice"}:
                raise ValueError("each note must contain the exact object fields")
            onset = Fraction(str(item["onset"]))
            duration = Fraction(str(item["duration"]))
            raw_pitch = item["pitch"]
            voice = item["voice"]
        if voice not in ("melody", "bass", "harmony"):
            raise ValueError(f"bad voice {voice!r}")
        if not isinstance(raw_pitch, int) or isinstance(raw_pitch, bool):
            raise ValueError(f"pitch must be an integer MIDI value, got {raw_pitch!r}")
        if onset < 0:
            raise ValueError(f"onset must be non-negative, got {onset}")
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        if not 0 <= raw_pitch <= 127:
            raise ValueError(f"pitch must be in MIDI range 0..127, got {raw_pitch}")
        identity = (onset, raw_pitch)
        if identity in seen:
            raise ValueError(f"duplicate pitch {raw_pitch} at onset {onset} is ambiguous")
        seen.add(identity)
        notes.append(Note(onset, duration, raw_pitch, cast(VoiceRole, voice)))
    return tuple(sorted(notes, key=lambda n: (n.onset, n.pitch)))


def arrangement_output_protocol(ir: MusicIR) -> ArrangementOutputProtocol:
    """Choose the frozen lossless response encoding before model execution."""

    ir = snapshot_music_ir(ir)
    events = len(ir.notes) + len(ir.chords)
    if events <= MAX_OBJECT_SOURCE_EVENTS:
        return ArrangementOutputProtocol.OBJECT
    if events <= MAX_COMPACT_SOURCE_EVENTS:
        return ArrangementOutputProtocol.COMPACT
    raise ArrangementCapacityError(
        "real-LLM arrangement supports at most "
        f"{MAX_COMPACT_SOURCE_EVENTS} source note/chord events in the lossless compact "
        f"single-call protocol, got {events}; use the deterministic path. The input "
        "was not truncated"
    )


def proposal_output_token_budget(ir: MusicIR) -> int:
    """Budget a full structured reply for every legal input event.

    The old fixed 2k budget could truncate an otherwise-valid long arrangement.
    Imported resource limits bound the request; this scales generously with all
    source notes and chord annotations while retaining a provider-safe ceiling.
    """
    ir = snapshot_music_ir(ir)
    events = len(ir.notes) + len(ir.chords)
    protocol = arrangement_output_protocol(ir)
    per_event = (
        OBJECT_OUTPUT_TOKENS_PER_EVENT
        if protocol is ArrangementOutputProtocol.OBJECT
        else COMPACT_OUTPUT_TOKENS_PER_EVENT
    )
    return max(MIN_OUTPUT_TOKENS, OUTPUT_TOKEN_FIXED_ALLOWANCE + per_event * events)


def ensure_llm_capacity(ir: MusicIR) -> None:
    """Raise a typed error rather than silently truncating a real-LLM request."""

    proposal_output_token_budget(ir)


def propose_arrangement_outcome(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    temperature: float = 0.0,
    profile: Profile = MEDIAN_HAND,
    call_scope_factory: ModelCallScopeFactory | None = None,
    candidate_index: int | None = None,
) -> ProposalOutcome:
    """Return one target and explicit provenance without exposing model content."""

    source_ir = snapshot_music_ir(ir)
    ir = arrangement_solver_ir(source_ir)
    notes, tuning, capo, profile, tempo_bpm = ensure_solver_domain(
        ir.notes,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
    )
    ir = MusicIR(notes, tuple(ir.chords), ir.meta)
    goal = ArrangeGoal(
        style=goal.style,
        tier=goal.tier,
        tuning=tuning,
        capo=capo,
        tempo_bpm=tempo_bpm,
        extras=goal.extras,
    )
    # ``ConstantLLM`` is the documented offline switch that previously reached
    # the same rule path via malformed JSON.  Dispatch directly so the non-LLM
    # vertical slice continues to accept the importer's much larger resource
    # envelope without constructing an enormous prompt.
    if isinstance(llm, ConstantLLM):
        return ProposalOutcome(
            propose_fingerstyle(
                ir,
                goal.tuning,
                goal.capo,
                profile=profile,
                tempo_bpm=goal.tempo_bpm,
            ),
            ProposalStatus.CONSTANT_LLM_BYPASS,
            0,
        )
    protocol = arrangement_output_protocol(source_ir)
    max_tokens = proposal_output_token_budget(source_ir)
    system = (
        _ARRANGE_OBJECT_SYSTEM
        if protocol is ArrangementOutputProtocol.OBJECT
        else _ARRANGE_COMPACT_SYSTEM
    )
    low = min(goal.tuning) + goal.capo
    high = max(goal.tuning) + goal.capo + 22
    user = (
        f"{arrangement_source_context(source_ir)}\n"
        f"Effective arrangement tempo: {goal.tempo_bpm} BPM.\n\n"
        f"Playable range on this tuning: MIDI {low}-{high} "
        f"(the lowest playable note is {low}; never write a note below {low}). "
        f"Keep at most 4 notes sounding at the same onset. "
        f"Goal: {goal.style}, {goal.tier} difficulty. Produce the target note set now."
    )
    try:
        with model_call_scope(
            call_scope_factory,
            stage="proposal",
            stage_ordinal=0,
            candidate_index=candidate_index,
        ):
            reply = llm.complete(
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
            )
    except LLMIntegrityError:
        raise
    except (ValueError, KeyError, TypeError, RuntimeError, ZeroDivisionError):
        return ProposalOutcome(
            propose_fingerstyle(
                ir,
                goal.tuning,
                goal.capo,
                profile=profile,
                tempo_bpm=goal.tempo_bpm,
            ),
            ProposalStatus.CALL_FAILURE_FALLBACK,
            1,
        )

    try:
        parsed = (
            _extract_compact_json(reply)
            if protocol is ArrangementOutputProtocol.COMPACT
            else extract_json(reply)
        )
        notes = _parse_notes(parsed, protocol)
        if not any(n.voice == "melody" for n in notes):
            raise ValueError("proposal has no melody")
        notes, _tuning, _capo, _profile, _tempo = ensure_solver_domain(
            notes,
            goal.tuning,
            goal.capo,
            profile,
            tempo_bpm=goal.tempo_bpm,
        )
        return ProposalOutcome(notes, ProposalStatus.LLM_SUCCESS, 1)
    except (ValueError, KeyError, TypeError, RuntimeError, ArithmeticError):
        return ProposalOutcome(
            propose_fingerstyle(
                ir,
                goal.tuning,
                goal.capo,
                profile=profile,
                tempo_bpm=goal.tempo_bpm,
            ),
            ProposalStatus.PARSE_VALIDATION_FALLBACK,
            1,
        )  # honest deterministic fallback


def propose_arrangement(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    temperature: float = 0.0,
    profile: Profile = MEDIAN_HAND,
    call_scope_factory: ModelCallScopeFactory | None = None,
    candidate_index: int | None = None,
) -> tuple[Note, ...]:
    """Compatibility wrapper returning exactly the historical target tuple."""

    return propose_arrangement_outcome(
        ir,
        goal,
        llm,
        temperature=temperature,
        profile=profile,
        call_scope_factory=call_scope_factory,
        candidate_index=candidate_index,
    ).target
