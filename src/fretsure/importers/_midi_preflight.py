"""First-party, resource-bounded Standard MIDI File preflight.

Raw bytes are completely framed and semantically narrowed here before a third-
party MIDI parser can observe them.  The resolved tick timeline is authoritative;
``canonical_midi`` is only a minimal cross-validation projection.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction

from fretsure.importers.contracts import (
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportLimits,
    SourceLocation,
)

_MAX_DIAGNOSTICS = 256
_TEXT_META_TYPES = frozenset(range(0x01, 0x08))
_CONTROLLER_ALLOWLIST = frozenset({0, 1, 2, 6, 7, 10, 11, 32, 91, 93, 100, 101, 121})
_RPN_CONTROLLERS = frozenset({6, 100, 101})
_MAJOR_KEYS = (
    "Cb",
    "Gb",
    "Db",
    "Ab",
    "Eb",
    "Bb",
    "F",
    "C",
    "G",
    "D",
    "A",
    "E",
    "B",
    "F#",
    "C#",
)
_MINOR_KEYS = (
    "Abm",
    "Ebm",
    "Bbm",
    "Fm",
    "Cm",
    "Gm",
    "Dm",
    "Am",
    "Em",
    "Bm",
    "F#m",
    "C#m",
    "G#m",
    "D#m",
    "A#m",
)


@dataclass(frozen=True, slots=True)
class MIDINoteEvent:
    """One exact, paired MIDI note from the authoritative raw tick timeline."""

    onset: Fraction
    duration: Fraction
    pitch: int
    onset_ticks: int
    duration_ticks: int
    track: int
    channel: int


@dataclass(frozen=True, slots=True)
class MIDIPreflightMetadata:
    """Validated metadata and note events consumed by the public importer."""

    format_type: int
    ticks_per_quarter: int
    tempo_microseconds_per_quarter: int
    tempo_bpm: float
    time_sig: tuple[int, int]
    key: str
    key_fifths: int | None
    key_mode: int | None
    title: str
    rights: str
    duration_ticks: int
    duration_beats: Fraction
    note_track: int
    note_channel: int
    note_events: tuple[MIDINoteEvent, ...]


@dataclass(frozen=True, slots=True)
class MIDIPreflightResult:
    diagnostics: tuple[ImportDiagnostic, ...]
    metadata: MIDIPreflightMetadata | None
    canonical_midi: bytes | None


@dataclass(frozen=True, slots=True)
class _LocatedScalar:
    tick: int
    value: bytes
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class _TextEvent:
    meta_type: int
    tick: int
    payload: bytes
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class _NoteMessage:
    track: int
    channel: int
    tick: int
    event_index: int
    pitch: int
    attack: bool

    @property
    def location(self) -> SourceLocation:
        return SourceLocation(
            element="note-on" if self.attack else "note-off",
            track_index=self.track,
            channel=self.channel + 1,
            tick=self.tick,
            event_index=self.event_index,
        )


@dataclass(frozen=True, slots=True)
class _ControllerMessage:
    track: int
    channel: int
    tick: int
    event_index: int
    controller: int
    value: int

    @property
    def location(self) -> SourceLocation:
        return _track_location(
            self.track,
            channel=self.channel,
            tick=self.tick,
            event_index=self.event_index,
            element=f"controller-{self.controller}",
        )


@dataclass(frozen=True, slots=True)
class _PitchBendMessage:
    track: int
    channel: int
    tick: int
    event_index: int
    lsb: int
    msb: int

    @property
    def location(self) -> SourceLocation:
        return _track_location(
            self.track,
            channel=self.channel,
            tick=self.tick,
            event_index=self.event_index,
            element="pitch-bend",
        )


@dataclass(slots=True)
class _ParsedMIDI:
    format_type: int
    ticks_per_quarter: int
    track_end_ticks: dict[int, int]
    tempos: list[_LocatedScalar]
    time_signatures: list[_LocatedScalar]
    key_signatures: list[_LocatedScalar]
    text_events: list[_TextEvent]
    note_messages: list[_NoteMessage]
    controller_messages: list[_ControllerMessage]
    pitch_bends: list[_PitchBendMessage]
    note_attack_count: int
    performance_data_ignored: bool
    diagnostics: _BoundedDiagnostics
    key_signature_seen: bool = False


class _StructuralFailure(Exception):
    def __init__(self, diagnostic: ImportDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


def _error(
    code: ImportCode,
    message: str,
    location: SourceLocation | None = None,
) -> ImportDiagnostic:
    return ImportDiagnostic(code, DiagnosticSeverity.ERROR, message, location)


def _warning(
    code: ImportCode,
    message: str,
    location: SourceLocation | None = None,
) -> ImportDiagnostic:
    return ImportDiagnostic(code, DiagnosticSeverity.WARNING, message, location)


class _BoundedDiagnostics(list[ImportDiagnostic]):
    """Retain at most 256 source diagnostics plus one overflow sentinel."""

    __slots__ = ("_overflowed",)

    def __init__(self) -> None:
        super().__init__()
        self._overflowed = False

    def append(self, diagnostic: ImportDiagnostic) -> None:
        if self._overflowed:
            return
        if len(self) >= _MAX_DIAGNOSTICS:
            super().append(
                _error(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    "MIDI produced more than 256 source diagnostics; remaining "
                    "diagnostics were omitted",
                    SourceLocation(element="diagnostics"),
                )
            )
            self._overflowed = True
            return
        super().append(diagnostic)

    def extend(self, diagnostics: Iterable[ImportDiagnostic]) -> None:
        for diagnostic in diagnostics:
            self.append(diagnostic)


def _structural(
    code: ImportCode,
    message: str,
    location: SourceLocation | None = None,
) -> _StructuralFailure:
    return _StructuralFailure(_error(code, message, location))


def _track_location(
    track: int,
    *,
    tick: int | None = None,
    event_index: int | None = None,
    channel: int | None = None,
    element: str | None = None,
) -> SourceLocation:
    return SourceLocation(
        element=element,
        track_index=track,
        channel=None if channel is None else channel + 1,
        tick=tick,
        event_index=event_index,
    )


def _read_u16(data: memoryview, offset: int) -> int:
    return (int(data[offset]) << 8) | int(data[offset + 1])


def _read_u32(data: memoryview, offset: int) -> int:
    return (
        (int(data[offset]) << 24)
        | (int(data[offset + 1]) << 16)
        | (int(data[offset + 2]) << 8)
        | int(data[offset + 3])
    )


def _read_vlq(
    data: memoryview,
    position: int,
    end: int,
    *,
    location: SourceLocation,
    label: str,
) -> tuple[int, int]:
    start = position
    value = 0
    for index in range(4):
        if position >= end:
            raise _structural(
                ImportCode.MALFORMED_MIDI,
                f"{label} VLQ is truncated",
                location,
            )
        byte = int(data[position])
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            width = position - start
            if width > 1 and value < 1 << (7 * (width - 1)):
                raise _structural(
                    ImportCode.MALFORMED_MIDI,
                    f"{label} VLQ is not minimally encoded",
                    location,
                )
            return value, position
        if index == 3:
            raise _structural(
                ImportCode.MALFORMED_MIDI,
                f"{label} VLQ exceeds four bytes",
                location,
            )
    raise AssertionError("unreachable VLQ state")


def _data_bytes(
    data: memoryview,
    position: int,
    end: int,
    count: int,
    *,
    first: int | None,
    location: SourceLocation,
) -> tuple[tuple[int, ...], int]:
    values: list[int] = []
    if first is not None:
        values.append(first)
    while len(values) < count:
        if position >= end:
            raise _structural(
                ImportCode.MALFORMED_MIDI,
                "channel message is truncated",
                location,
            )
        value = int(data[position])
        position += 1
        if value >= 0x80:
            raise _structural(
                ImportCode.MALFORMED_MIDI,
                "channel-message data bytes must be in 0..127",
                location,
            )
        values.append(value)
    return tuple(values), position


def _handle_meta(
    parsed: _ParsedMIDI,
    *,
    track: int,
    tick: int,
    event_index: int,
    meta_type: int,
    payload: bytes,
) -> None:
    location = _track_location(
        track,
        tick=tick,
        event_index=event_index,
        element=f"meta-0x{meta_type:02x}",
    )
    scalar = _LocatedScalar(tick, payload, location)
    if meta_type in _TEXT_META_TYPES:
        parsed.text_events.append(_TextEvent(meta_type, tick, payload, location))
    elif meta_type == 0x20:
        if len(payload) != 1 or payload[0] > 0x0F:
            parsed.diagnostics.append(
                _error(
                    ImportCode.MALFORMED_MIDI,
                    "MIDI channel-prefix meta event must name channel 0..15",
                    location,
                )
            )
        else:
            parsed.performance_data_ignored = True
    elif meta_type == 0x21:
        if len(payload) != 1 or payload[0] >= 0x80:
            parsed.diagnostics.append(
                _error(
                    ImportCode.MALFORMED_MIDI,
                    "MIDI port meta event must contain one data byte",
                    location,
                )
            )
        else:
            parsed.performance_data_ignored = True
    elif meta_type == 0x2F:
        # EOT shape/finality is enforced by the track parser.
        pass
    elif meta_type == 0x51:
        if len(payload) != 3:
            parsed.diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_TEMPO,
                    "Set Tempo must contain exactly three bytes",
                    location,
                )
            )
        else:
            parsed.tempos.append(scalar)
    elif meta_type == 0x54:
        parsed.diagnostics.append(
            _error(
                ImportCode.MIDI_META_EVENT_UNSUPPORTED,
                "SMPTE offset meta events are unsupported",
                location,
            )
        )
    elif meta_type == 0x58:
        if len(payload) != 4:
            parsed.diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_TIME_SIGNATURE,
                    "time signature must contain exactly four bytes",
                    location,
                )
            )
        else:
            parsed.time_signatures.append(scalar)
    elif meta_type == 0x59:
        parsed.key_signature_seen = True
        if len(payload) != 2:
            parsed.diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_KEY,
                    "key signature must contain exactly two bytes",
                    location,
                )
            )
        else:
            parsed.key_signatures.append(scalar)
    elif meta_type == 0x7F:
        parsed.diagnostics.append(
            _error(
                ImportCode.MIDI_META_EVENT_UNSUPPORTED,
                "sequencer-specific meta events are unsupported",
                location,
            )
        )
    else:
        parsed.diagnostics.append(
            _error(
                ImportCode.MIDI_META_EVENT_UNSUPPORTED,
                f"meta event 0x{meta_type:02x} is outside the frozen allowlist",
                location,
            )
        )


def _handle_channel_message(
    parsed: _ParsedMIDI,
    *,
    status: int,
    values: tuple[int, ...],
    track: int,
    tick: int,
    event_index: int,
    limits: ImportLimits,
) -> None:
    kind = status >> 4
    channel = status & 0x0F
    location = _track_location(
        track,
        tick=tick,
        event_index=event_index,
        channel=channel,
        element=f"channel-0x{kind:x}",
    )
    if kind in {0x8, 0x9}:
        pitch, velocity = values
        attack = kind == 0x9 and velocity != 0
        if attack and channel == 9:
            parsed.diagnostics.append(
                _error(
                    ImportCode.MIDI_PERCUSSION_UNSUPPORTED,
                    "channel 10 percussion note attacks are unsupported",
                    location,
                )
            )
        if attack:
            parsed.note_attack_count += 1
            if parsed.note_attack_count > limits.max_midi_notes:
                raise _structural(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    f"MIDI note attacks exceed {limits.max_midi_notes}",
                    location,
                )
        parsed.note_messages.append(
            _NoteMessage(track, channel, tick, event_index, pitch, attack)
        )
        parsed.performance_data_ignored = True
    elif kind == 0xB:
        controller, value = values
        if controller in _CONTROLLER_ALLOWLIST:
            parsed.controller_messages.append(
                _ControllerMessage(
                    track,
                    channel,
                    tick,
                    event_index,
                    controller,
                    value,
                )
            )
            parsed.performance_data_ignored = True
        else:
            parsed.diagnostics.append(
                _error(
                    ImportCode.MIDI_CONTROL_CHANGE_UNSUPPORTED,
                    f"controller {controller} is outside the frozen non-sounding allowlist",
                    location,
                )
            )
    elif kind == 0xC:
        parsed.performance_data_ignored = True
    elif kind == 0xE:
        parsed.pitch_bends.append(
            _PitchBendMessage(
                track,
                channel,
                tick,
                event_index,
                values[0],
                values[1],
            )
        )
        parsed.performance_data_ignored = True
    else:
        parsed.diagnostics.append(
            _error(
                ImportCode.MIDI_EVENT_UNSUPPORTED,
                f"channel message 0x{kind:x} is unsupported",
                location,
            )
        )


def _parse_track(
    data: memoryview,
    start: int,
    end: int,
    *,
    track: int,
    parsed: _ParsedMIDI,
    limits: ImportLimits,
    total_text_bytes: list[int],
    total_events: list[int],
) -> None:
    position = start
    tick = 0
    event_index = 0
    running_status: int | None = None
    while position < end:
        location = _track_location(
            track,
            tick=tick,
            event_index=event_index,
            element="event",
        )
        delta, position = _read_vlq(
            data,
            position,
            end,
            location=location,
            label="delta-time",
        )
        if delta > limits.max_midi_tick - tick:
            raise _structural(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                f"absolute MIDI tick exceeds {limits.max_midi_tick}",
                location,
            )
        tick += delta
        total_events[0] += 1
        if total_events[0] > limits.max_midi_events:
            raise _structural(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                f"MIDI event count exceeds {limits.max_midi_events}",
                location,
            )
        if position >= end:
            raise _structural(
                ImportCode.MALFORMED_MIDI,
                "track ends before event status/data",
                location,
            )
        first = int(data[position])
        first_data: int | None = None
        if first < 0x80:
            if running_status is None:
                raise _structural(
                    ImportCode.MALFORMED_MIDI,
                    "data byte appears without active running status",
                    location,
                )
            status = running_status
            first_data = first
            position += 1
        else:
            status = first
            position += 1
            running_status = status if 0x80 <= status <= 0xEF else None

        event_location = _track_location(
            track,
            tick=tick,
            event_index=event_index,
            element="event",
        )
        if 0x80 <= status <= 0xEF:
            count = 1 if status >> 4 in {0xC, 0xD} else 2
            values, position = _data_bytes(
                data,
                position,
                end,
                count,
                first=first_data,
                location=event_location,
            )
            _handle_channel_message(
                parsed,
                status=status,
                values=values,
                track=track,
                tick=tick,
                event_index=event_index,
                limits=limits,
            )
        elif status in {0xF0, 0xF7}:
            length, position = _read_vlq(
                data,
                position,
                end,
                location=event_location,
                label="SysEx length",
            )
            if length > end - position:
                raise _structural(
                    ImportCode.MALFORMED_MIDI,
                    "SysEx payload exceeds its track chunk",
                    event_location,
                )
            position += length
            parsed.diagnostics.append(
                _error(
                    ImportCode.MIDI_SYSEX_UNSUPPORTED,
                    "SysEx events are unsupported",
                    event_location,
                )
            )
        elif status == 0xFF:
            if position >= end:
                raise _structural(
                    ImportCode.MALFORMED_MIDI,
                    "meta event is missing its type",
                    event_location,
                )
            meta_type = int(data[position])
            position += 1
            length, position = _read_vlq(
                data,
                position,
                end,
                location=event_location,
                label="meta length",
            )
            if length > limits.max_midi_text_bytes:
                raise _structural(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    f"MIDI meta payload exceeds {limits.max_midi_text_bytes} bytes",
                    event_location,
                )
            if length > end - position:
                raise _structural(
                    ImportCode.MALFORMED_MIDI,
                    "meta payload exceeds its track chunk",
                    event_location,
                )
            total_text_bytes[0] += length
            if total_text_bytes[0] > limits.max_midi_total_text_bytes:
                raise _structural(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    "cumulative MIDI meta payload exceeds "
                    f"{limits.max_midi_total_text_bytes} bytes",
                    event_location,
                )
            payload = bytes(data[position : position + length])
            position += length
            if meta_type == 0x2F:
                if payload:
                    raise _structural(
                        ImportCode.MALFORMED_MIDI,
                        "End-of-Track must have zero payload length",
                        event_location,
                    )
                if position != end:
                    raise _structural(
                        ImportCode.MALFORMED_MIDI,
                        "End-of-Track must be the final event in its chunk",
                        event_location,
                    )
                parsed.track_end_ticks[track] = tick
            _handle_meta(
                parsed,
                track=track,
                tick=tick,
                event_index=event_index,
                meta_type=meta_type,
                payload=payload,
            )
            if meta_type == 0x2F:
                return
        else:
            raise _structural(
                ImportCode.MIDI_EVENT_UNSUPPORTED,
                f"system status 0x{status:02x} is not valid in the frozen SMF subset",
                event_location,
            )
        event_index += 1

    raise _structural(
        ImportCode.MALFORMED_MIDI,
        "track is missing a final End-of-Track event",
        _track_location(track, tick=tick, event_index=event_index, element="track"),
    )


def _parse_smf(data: bytes, limits: ImportLimits) -> _ParsedMIDI:
    if len(data) > limits.max_midi_bytes:
        raise _structural(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"MIDI input is {len(data)} bytes; limit is {limits.max_midi_bytes}",
            SourceLocation(element="file"),
        )
    view = memoryview(data)
    if len(view) < 14 or bytes(view[:4]) != b"MThd":
        raise _structural(
            ImportCode.MALFORMED_MIDI,
            "expected an MThd header chunk",
            SourceLocation(element="header"),
        )
    header_length = _read_u32(view, 4)
    if header_length != 6:
        raise _structural(
            ImportCode.MALFORMED_MIDI,
            "MThd payload length must be exactly 6",
            SourceLocation(element="header"),
        )
    format_type = _read_u16(view, 8)
    declared_tracks = _read_u16(view, 10)
    division = _read_u16(view, 12)
    if format_type not in {0, 1}:
        raise _structural(
            ImportCode.UNSUPPORTED_MIDI_FORMAT,
            "only SMF format 0 and 1 are supported",
            SourceLocation(element="header"),
        )
    if (format_type == 0 and declared_tracks != 1) or (
        format_type == 1 and declared_tracks < 1
    ):
        raise _structural(
            ImportCode.MALFORMED_MIDI,
            "declared track count is inconsistent with the SMF format",
            SourceLocation(element="header"),
        )
    if declared_tracks > limits.max_midi_tracks:
        raise _structural(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"MIDI track count exceeds {limits.max_midi_tracks}",
            SourceLocation(element="header"),
        )
    if division & 0x8000:
        raise _structural(
            ImportCode.MIDI_TIMING_UNSUPPORTED,
            "SMPTE time division is unsupported",
            SourceLocation(element="division"),
        )
    if division == 0:
        raise _structural(
            ImportCode.MALFORMED_MIDI,
            "PPQN division must be in 1..32767",
            SourceLocation(element="division"),
        )

    parsed = _ParsedMIDI(
        format_type,
        division,
        {},
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        0,
        False,
        _BoundedDiagnostics(),
    )
    position = 14
    total_text_bytes = [0]
    total_events = [0]
    for track in range(declared_tracks):
        if position + 8 > len(view):
            raise _structural(
                ImportCode.MALFORMED_MIDI,
                "declared track chunk header is truncated",
                _track_location(track, element="track"),
            )
        if bytes(view[position : position + 4]) != b"MTrk":
            raise _structural(
                ImportCode.MALFORMED_MIDI,
                "expected an MTrk chunk; unknown chunks are unsupported",
                _track_location(track, element="track"),
            )
        track_length = _read_u32(view, position + 4)
        start = position + 8
        end = start + track_length
        if end > len(view):
            raise _structural(
                ImportCode.MALFORMED_MIDI,
                "declared MTrk length exceeds the input",
                _track_location(track, element="track"),
            )
        _parse_track(
            view,
            start,
            end,
            track=track,
            parsed=parsed,
            limits=limits,
            total_text_bytes=total_text_bytes,
            total_events=total_events,
        )
        position = end
    if position != len(view):
        raise _structural(
            ImportCode.MALFORMED_MIDI,
            "trailing bytes or undeclared chunks follow the declared tracks",
            SourceLocation(element="file"),
        )
    return parsed


def _validate_performance_setup(
    parsed: _ParsedMIDI,
    *,
    note_track: int,
    note_channel: int,
    attacks: list[_NoteMessage],
) -> None:
    """Validate the producer-driven RPN and no-op bend setup window."""

    first_attack = min(attacks, key=lambda item: (item.tick, item.event_index))

    def in_setup_window(track: int, channel: int, tick: int, event_index: int) -> bool:
        return (
            track == note_track
            and channel == note_channel
            and tick == 0
            and event_index < first_attack.event_index
        )

    rpn = [
        message
        for message in parsed.controller_messages
        if message.controller in _RPN_CONTROLLERS
    ]
    if rpn:
        rpn.sort(key=lambda item: (item.track, item.event_index))
        shape_is_valid = (
            len(rpn) == 5
            and all(
                in_setup_window(
                    message.track,
                    message.channel,
                    message.tick,
                    message.event_index,
                )
                for message in rpn
            )
            and {rpn[0].controller, rpn[1].controller} == {100, 101}
            and rpn[0].value == 0
            and rpn[1].value == 0
            and rpn[2].controller == 6
            and {rpn[3].controller, rpn[4].controller} == {100, 101}
            and rpn[3].value == 127
            and rpn[4].value == 127
        )
        if not shape_is_valid:
            offender = next(
                (
                    message
                    for message in rpn
                    if not in_setup_window(
                        message.track,
                        message.channel,
                        message.tick,
                        message.event_index,
                    )
                ),
                rpn[0],
            )
            parsed.diagnostics.append(
                _error(
                    ImportCode.MIDI_CONTROL_CHANGE_UNSUPPORTED,
                    "CC6/100/101 must form one tick-0 pre-note RPN 0/0 data-entry "
                    "then null 127/127 sequence on the note-bearing channel",
                    offender.location,
                )
            )

    for index, bend in enumerate(parsed.pitch_bends):
        if (
            len(parsed.pitch_bends) != 1
            or index != 0
            or (bend.lsb, bend.msb) != (0, 64)
            or not in_setup_window(
                bend.track,
                bend.channel,
                bend.tick,
                bend.event_index,
            )
        ):
            parsed.diagnostics.append(
                _error(
                    ImportCode.MIDI_PITCH_BEND_UNSUPPORTED,
                    "only one tick-0 center pitch bend before the first note on the "
                    "note-bearing channel is supported",
                    bend.location,
                )
            )


def _resolve_notes(
    parsed: _ParsedMIDI,
    limits: ImportLimits,
) -> tuple[tuple[MIDINoteEvent, ...], int | None, int | None, int | None]:
    attacks = [message for message in parsed.note_messages if message.attack]
    if not attacks:
        parsed.diagnostics.append(
            _error(
                ImportCode.NO_NOTE_BEARING_STREAM,
                "MIDI contains no note-bearing stream",
                SourceLocation(element="notes"),
            )
        )
        return (), None, None, None
    streams = {(message.track, message.channel) for message in attacks}
    if len(streams) != 1:
        second = next(message for message in attacks if (message.track, message.channel) != (
            attacks[0].track,
            attacks[0].channel,
        ))
        parsed.diagnostics.append(
            _error(
                ImportCode.MULTIPLE_NOTE_BEARING_STREAMS,
                "MIDI contains more than one note-bearing (track, channel) stream",
                second.location,
            )
        )
        return (), None, None, None
    note_track, note_channel = next(iter(streams))
    note_track_end = parsed.track_end_ticks[note_track]
    if note_track_end > limits.max_midi_quarter_span * parsed.ticks_per_quarter:
        parsed.diagnostics.append(
            _error(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                "note-bearing MIDI track exceeds "
                f"{limits.max_midi_quarter_span} quarter notes",
                _track_location(
                    note_track,
                    tick=note_track_end,
                    element="track-duration",
                ),
            )
        )
        return (), None, None, None
    _validate_performance_setup(
        parsed,
        note_track=note_track,
        note_channel=note_channel,
        attacks=attacks,
    )
    stream_messages: list[_NoteMessage] = []
    for message in parsed.note_messages:
        if (message.track, message.channel) != (note_track, note_channel):
            parsed.diagnostics.append(
                _error(
                    ImportCode.MIDI_NOTE_PAIRING_ERROR,
                    "note release occurs outside the unique note-bearing stream",
                    message.location,
                )
            )
        else:
            stream_messages.append(message)

    stream_messages.sort(key=lambda item: (item.tick, item.event_index))
    active: dict[int, _NoteMessage] = {}
    resolved: list[MIDINoteEvent] = []
    cursor = 0
    while cursor < len(stream_messages):
        tick = stream_messages[cursor].tick
        group_end = cursor
        while group_end < len(stream_messages) and stream_messages[group_end].tick == tick:
            group_end += 1
        grouped: dict[int, tuple[list[_NoteMessage], list[_NoteMessage]]] = {}
        for message in stream_messages[cursor:group_end]:
            attacks_at_tick, releases_at_tick = grouped.setdefault(
                message.pitch,
                ([], []),
            )
            (attacks_at_tick if message.attack else releases_at_tick).append(message)
        for pitch in sorted(grouped):
            attacks_at_tick, releases_at_tick = grouped[pitch]
            prior_attack = active.get(pitch)
            if prior_attack is not None and releases_at_tick:
                release = releases_at_tick.pop(0)
                active.pop(pitch)
                duration_ticks = tick - prior_attack.tick
                if duration_ticks <= 0:
                    parsed.diagnostics.append(
                        _error(
                            ImportCode.MIDI_NOTE_PAIRING_ERROR,
                            "resolved MIDI note duration must be positive",
                            release.location,
                        )
                    )
                else:
                    resolved.append(
                        MIDINoteEvent(
                            Fraction(prior_attack.tick, parsed.ticks_per_quarter),
                            Fraction(duration_ticks, parsed.ticks_per_quarter),
                            prior_attack.pitch,
                            prior_attack.tick,
                            duration_ticks,
                            note_track,
                            note_channel,
                        )
                    )

            if prior_attack is None and attacks_at_tick and releases_at_tick:
                first_attack_at_tick = attacks_at_tick[0]
                first_release_at_tick = releases_at_tick[0]
                if first_attack_at_tick.event_index < first_release_at_tick.event_index:
                    attacks_at_tick.pop(0)
                    releases_at_tick.pop(0)
                    parsed.diagnostics.append(
                        _error(
                            ImportCode.MIDI_NOTE_PAIRING_ERROR,
                            "resolved MIDI note duration must be positive",
                            first_release_at_tick.location,
                        )
                    )

            for release in releases_at_tick:
                parsed.diagnostics.append(
                    _error(
                        ImportCode.MIDI_NOTE_PAIRING_ERROR,
                        "note release has no matching active attack",
                        release.location,
                    )
                )

            if attacks_at_tick:
                if pitch in active:
                    for attack in attacks_at_tick:
                        parsed.diagnostics.append(
                            _error(
                                ImportCode.MIDI_NOTE_PAIRING_ERROR,
                                "a pitch is attacked while the same pitch is already active",
                                attack.location,
                            )
                        )
                else:
                    active[pitch] = attacks_at_tick[0]
                    for attack in attacks_at_tick[1:]:
                        parsed.diagnostics.append(
                            _error(
                                ImportCode.MIDI_NOTE_PAIRING_ERROR,
                                "a pitch is attacked more than once at the same tick",
                                attack.location,
                            )
                        )
        if len(active) > 1:
            parsed.diagnostics.append(
                _error(
                    ImportCode.MIDI_POLYPHONY_UNSUPPORTED,
                    "more than one note is sounding after events at this tick",
                    _track_location(
                        note_track,
                        channel=note_channel,
                        tick=tick,
                        element="notes",
                    ),
                )
            )
        cursor = group_end
    for attack in sorted(active.values(), key=lambda item: (item.tick, item.pitch)):
        parsed.diagnostics.append(
            _error(
                ImportCode.MIDI_NOTE_PAIRING_ERROR,
                "note attack is still active at End-of-Track",
                attack.location,
            )
        )
    if len(resolved) > limits.max_midi_notes:
        parsed.diagnostics.append(
            _error(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                f"resolved MIDI notes exceed {limits.max_midi_notes}",
                SourceLocation(element="notes"),
            )
        )
    resolved.sort(key=lambda item: (item.onset, item.pitch, item.duration))
    return tuple(resolved), note_track, note_channel, note_track_end


def _printable_ascii(payload: bytes) -> str | None:
    if not payload or any(byte < 0x20 or byte > 0x7E for byte in payload):
        return None
    return payload.decode("ascii")


def _select_text(
    parsed: _ParsedMIDI,
    note_track: int,
) -> tuple[str, str, bool]:
    track_names = [
        event
        for event in parsed.text_events
        if event.meta_type == 0x03
        and event.location.track_index == note_track
        and event.tick == 0
        and _printable_ascii(event.payload) is not None
    ]
    copyrights = [
        event
        for event in parsed.text_events
        if event.meta_type == 0x02
        and event.tick == 0
        and _printable_ascii(event.payload) is not None
    ]
    title_event = track_names[0] if len(track_names) == 1 else None
    rights_event = copyrights[0] if len(copyrights) == 1 else None
    selected_count = int(title_event is not None) + int(rights_event is not None)
    ignored = len(parsed.text_events) != selected_count
    title = "" if title_event is None else title_event.payload.decode("ascii")
    rights = (
        "unprovided"
        if rights_event is None
        else "copyright-notice:" + rights_event.payload.decode("ascii")
    )
    return title, rights, ignored


def _metadata(parsed: _ParsedMIDI, limits: ImportLimits) -> MIDIPreflightMetadata | None:
    notes, note_track, note_channel, duration_ticks = _resolve_notes(parsed, limits)

    tempo_us: int | None = None
    tempo_bpm: float | None = None
    if not parsed.tempos:
        parsed.diagnostics.append(
            _error(
                ImportCode.MISSING_TEMPO,
                "MIDI requires exactly one tick-0 Set Tempo event",
                SourceLocation(element="tempo"),
            )
        )
    elif len(parsed.tempos) != 1:
        parsed.diagnostics.append(
            _error(
                ImportCode.TEMPO_CHANGE_UNSUPPORTED,
                "MIDI requires exactly one Set Tempo event",
                parsed.tempos[1].location,
            )
        )
    else:
        tempo = parsed.tempos[0]
        tempo_us = int.from_bytes(tempo.value, "big")
        if tempo.tick != 0:
            parsed.diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_TEMPO,
                    "Set Tempo must occur at tick 0",
                    tempo.location,
                )
            )
        elif not 60_000 <= tempo_us <= 16_777_215:
            parsed.diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_TEMPO,
                    "tempo must resolve exactly within 1..1000 BPM",
                    tempo.location,
                )
            )
        else:
            tempo_bpm = float(Fraction(60_000_000, tempo_us))

    if not parsed.time_signatures:
        parsed.diagnostics.append(
            _error(
                ImportCode.MISSING_TIME_SIGNATURE,
                "MIDI requires exactly one tick-0 time signature",
                SourceLocation(element="time-signature"),
            )
        )
    elif len(parsed.time_signatures) != 1:
        parsed.diagnostics.append(
            _error(
                ImportCode.TIME_SIGNATURE_CHANGE_UNSUPPORTED,
                "MIDI requires exactly one time-signature event",
                parsed.time_signatures[1].location,
            )
        )
    else:
        time = parsed.time_signatures[0]
        if time.tick != 0 or time.value != b"\x04\x02\x18\x08":
            parsed.diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_TIME_SIGNATURE,
                    "only canonical tick-0 4/4 time signature data is supported",
                    time.location,
                )
            )

    key = "key-signature:unprovided"
    key_fifths: int | None = None
    key_mode: int | None = None
    if len(parsed.key_signatures) > 1:
        parsed.diagnostics.append(
            _error(
                ImportCode.KEY_CHANGE_UNSUPPORTED,
                "MIDI permits at most one tick-0 key signature",
                parsed.key_signatures[1].location,
            )
        )
    elif parsed.key_signatures:
        key_event = parsed.key_signatures[0]
        signed_fifths = key_event.value[0]
        if signed_fifths >= 0x80:
            signed_fifths -= 0x100
        mode = key_event.value[1]
        if key_event.tick != 0 or not -7 <= signed_fifths <= 7 or mode not in {0, 1}:
            parsed.diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_KEY,
                    "key signature must be tick-0 traditional major/minor with -7..7 fifths",
                    key_event.location,
                )
            )
        else:
            key_fifths = signed_fifths
            key_mode = mode
            key = (_MAJOR_KEYS if mode == 0 else _MINOR_KEYS)[signed_fifths + 7]

    if note_track is None or note_channel is None or duration_ticks is None:
        return None
    title, rights, text_ignored = _select_text(parsed, note_track)
    if not parsed.key_signature_seen:
        parsed.diagnostics.append(
            _warning(
                ImportCode.MIDI_KEY_UNPROVIDED,
                "MIDI supplies no key signature; no key was inferred",
                SourceLocation(element="key-signature"),
            )
        )
    parsed.diagnostics.append(
        _warning(
            ImportCode.MIDI_HARMONY_UNPROVIDED,
            "MIDI supplies no reliable chord-symbol or bass-root evidence",
            SourceLocation(element="harmony"),
        )
    )
    if parsed.performance_data_ignored:
        parsed.diagnostics.append(
            _warning(
                ImportCode.MIDI_PERFORMANCE_DATA_IGNORED,
                "velocity, program, port, or whitelisted mix data was not represented",
                SourceLocation(element="performance-data"),
            )
        )
    if text_ignored:
        parsed.diagnostics.append(
            _warning(
                ImportCode.MIDI_TEXT_IGNORED,
                "MIDI text outside the bounded title/rights contract was ignored",
                SourceLocation(element="text"),
            )
        )
    if rights == "unprovided":
        parsed.diagnostics.append(
            _warning(
                ImportCode.RIGHTS_UNPROVIDED,
                "MIDI source provides no bounded copyright notice",
                SourceLocation(element="rights"),
            )
        )

    if tempo_us is None or tempo_bpm is None:
        return None
    if not parsed.time_signatures or parsed.time_signatures[0].value != b"\x04\x02\x18\x08":
        return None
    return MIDIPreflightMetadata(
        parsed.format_type,
        parsed.ticks_per_quarter,
        tempo_us,
        tempo_bpm,
        (4, 4),
        key,
        key_fifths,
        key_mode,
        title,
        rights,
        duration_ticks,
        Fraction(duration_ticks, parsed.ticks_per_quarter),
        note_track,
        note_channel,
        notes,
    )


def _encode_vlq(value: int) -> bytes:
    if not 0 <= value <= 0x0FFFFFFF:
        raise ValueError("canonical MIDI delta is outside the four-byte VLQ range")
    output = bytearray((value & 0x7F,))
    value >>= 7
    while value:
        output.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(output)


def build_canonical_midi(metadata: MIDIPreflightMetadata) -> bytes:
    """Project validated metadata into a minimal single-track format-0 SMF."""

    events: list[tuple[int, int, int, bytes]] = [
        (
            0,
            0,
            0,
            b"\xff\x51\x03" + metadata.tempo_microseconds_per_quarter.to_bytes(3, "big"),
        ),
        (0, 0, 1, b"\xff\x58\x04\x04\x02\x18\x08"),
    ]
    serial = 2
    if metadata.key_fifths is not None and metadata.key_mode is not None:
        events.append(
            (
                0,
                0,
                serial,
                b"\xff\x59\x02"
                + bytes((metadata.key_fifths & 0xFF, metadata.key_mode)),
            )
        )
        serial += 1
    for note in metadata.note_events:
        events.append((note.onset_ticks, 2, serial, bytes((0x90, note.pitch, 64))))
        serial += 1
        events.append(
            (
                note.onset_ticks + note.duration_ticks,
                1,
                serial,
                bytes((0x80, note.pitch, 0)),
            )
        )
        serial += 1
    events.append((metadata.duration_ticks, 3, serial, b"\xff\x2f\x00"))
    events.sort()
    previous_tick = 0
    track = bytearray()
    for tick, _priority, _serial, payload in events:
        track.extend(_encode_vlq(tick - previous_tick))
        track.extend(payload)
        previous_tick = tick
    header = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01" + metadata.ticks_per_quarter.to_bytes(
        2, "big"
    )
    return header + b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)


def preflight_midi(data: bytes, limits: ImportLimits) -> MIDIPreflightResult:
    """Validate exact SMF bytes and return typed metadata plus adapter projection."""

    if type(data) is not bytes:
        return MIDIPreflightResult(
            (
                _error(
                    ImportCode.INVALID_INPUT,
                    "MIDI preflight data must be exact bytes",
                    SourceLocation(element="data"),
                ),
            ),
            None,
            None,
        )
    if type(limits) is not ImportLimits:
        return MIDIPreflightResult(
            (
                _error(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    "MIDI preflight limits must be an exact ImportLimits instance",
                    SourceLocation(element="limits"),
                ),
            ),
            None,
            None,
        )
    try:
        parsed = _parse_smf(data, limits)
    except _StructuralFailure as exc:
        return MIDIPreflightResult((exc.diagnostic,), None, None)
    metadata = _metadata(parsed, limits)
    diagnostics = tuple(parsed.diagnostics)
    if metadata is None or any(
        diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in diagnostics
    ):
        return MIDIPreflightResult(diagnostics, None, None)
    try:
        canonical = build_canonical_midi(metadata)
    except (OverflowError, ValueError) as exc:
        return MIDIPreflightResult(
            (
                *diagnostics,
                _error(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    "canonical MIDI projection exceeded its numeric envelope: "
                    f"{type(exc).__name__}",
                    SourceLocation(element="canonical-midi"),
                ),
            ),
            None,
            None,
        )
    return MIDIPreflightResult(diagnostics, metadata, canonical)


__all__ = [
    "MIDINoteEvent",
    "MIDIPreflightMetadata",
    "MIDIPreflightResult",
    "build_canonical_midi",
    "preflight_midi",
]
