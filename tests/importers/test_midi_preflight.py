from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from fretsure.importers._midi_preflight import MIDIPreflightResult, preflight_midi
from fretsure.importers.contracts import (
    DEFAULT_LIMITS,
    DiagnosticSeverity,
    ImportCode,
)

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
_KEY_CASES = tuple(
    (fifths, mode, key)
    for mode, keys in enumerate((_MAJOR_KEYS, _MINOR_KEYS))
    for fifths, key in zip(range(-7, 8), keys, strict=True)
)


def _vlq(value: int) -> bytes:
    assert 0 <= value <= 0x0FFFFFFF
    encoded = bytearray((value & 0x7F,))
    value >>= 7
    while value:
        encoded.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(encoded)


def _event(delta: int, payload: bytes) -> bytes:
    return _vlq(delta) + payload


def _meta(delta: int, kind: int, payload: bytes = b"") -> bytes:
    return _event(delta, b"\xff" + bytes((kind,)) + _vlq(len(payload)) + payload)


def _track(payload: bytes) -> bytes:
    return b"MTrk" + len(payload).to_bytes(4, "big") + payload


def _smf(*tracks: bytes, format_type: int | None = None, ppqn: int = 480) -> bytes:
    resolved_format = (0 if len(tracks) == 1 else 1) if format_type is None else format_type
    header = (
        b"MThd\x00\x00\x00\x06"
        + resolved_format.to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + ppqn.to_bytes(2, "big")
    )
    return header + b"".join(_track(track) for track in tracks)


def _prefix(*, key: bytes | None = b"\x00\x00") -> bytes:
    result = _meta(0, 0x51, b"\x07\xa1\x20")
    result += _meta(0, 0x58, b"\x04\x02\x18\x08")
    if key is not None:
        result += _meta(0, 0x59, key)
    return result


def _valid_midi(
    *,
    ppqn: int = 480,
    key: bytes | None = b"\x00\x00",
    before_note: bytes = b"",
    between_notes: bytes = b"",
    note_on: bytes = b"\x90\x3c\x40",
    note_off: bytes = b"\x80\x3c\x00",
    note_delta: int | None = None,
    trailing_delta: int | None = None,
    title: bytes | None = None,
    copyright_text: bytes | None = None,
) -> bytes:
    duration = ppqn if note_delta is None else note_delta
    trailing = ppqn if trailing_delta is None else trailing_delta
    payload = _prefix(key=key)
    if title is not None:
        payload += _meta(0, 0x03, title)
    if copyright_text is not None:
        payload += _meta(0, 0x02, copyright_text)
    payload += before_note
    payload += _event(0, note_on)
    payload += between_notes
    payload += _event(duration, note_off)
    payload += _meta(trailing, 0x2F)
    return _smf(payload, ppqn=ppqn)


def _codes(result: MIDIPreflightResult) -> list[ImportCode]:
    return [diagnostic.code for diagnostic in result.diagnostics]


def _assert_error(data: bytes, code: ImportCode) -> MIDIPreflightResult:
    result = preflight_midi(data, DEFAULT_LIMITS)
    assert result.metadata is None
    assert result.canonical_midi is None
    assert code in _codes(result), _codes(result)
    return result


def test_format_zero_success_preserves_exact_ticks_metadata_and_trailing_silence() -> None:
    raw = _valid_midi(title=b"Lead", copyright_text=b"CC0-1.0")

    result = preflight_midi(raw, DEFAULT_LIMITS)

    assert result.metadata is not None
    assert result.canonical_midi is not None
    metadata = result.metadata
    assert metadata.format_type == 0
    assert metadata.ticks_per_quarter == 480
    assert metadata.tempo_microseconds_per_quarter == 500_000
    assert metadata.tempo_bpm == 120.0
    assert metadata.time_sig == (4, 4)
    assert metadata.key == "C"
    assert metadata.key_fifths == 0
    assert metadata.key_mode == 0
    assert metadata.title == "Lead"
    assert metadata.rights == "copyright-notice:CC0-1.0"
    assert metadata.duration_ticks == 960
    assert metadata.duration_beats == Fraction(2)
    assert metadata.note_track == 0
    assert metadata.note_channel == 0
    assert metadata.note_events == (
        metadata.note_events[0].__class__(
            onset=Fraction(0),
            duration=Fraction(1),
            pitch=60,
            onset_ticks=0,
            duration_ticks=480,
            track=0,
            channel=0,
        ),
    )
    assert _codes(result) == [
        ImportCode.MIDI_HARMONY_UNPROVIDED,
        ImportCode.MIDI_PERFORMANCE_DATA_IGNORED,
    ]
    assert b"Lead" not in result.canonical_midi
    assert b"CC0-1.0" not in result.canonical_midi

    round_trip = preflight_midi(result.canonical_midi, DEFAULT_LIMITS)
    assert round_trip.metadata is not None
    assert round_trip.metadata.note_events == metadata.note_events
    assert round_trip.metadata.duration_beats == Fraction(2)


def test_format_one_accepts_checked_producer_setup_and_uses_note_track_eot() -> None:
    conductor = _prefix() + _meta(0, 0x02, b"Example Org") + _meta(480, 0x2F)
    setup = b"".join(
        (
            _meta(0, 0x03, b"Melody"),
            _meta(0, 0x20, b"\x00"),
            _meta(0, 0x21, b"\x00"),
            _event(0, b"\xe0\x00\x40"),
            _event(0, b"\xb0\x79\x00"),
            _event(0, b"\xb0\x64\x00"),
            _event(0, b"\xb0\x65\x00"),
            _event(0, b"\xb0\x06\x0c"),
            _event(0, b"\xb0\x07\x64"),
            _event(0, b"\xb0\x0a\x40"),
            _event(0, b"\xb0\x5b\x00"),
            _event(0, b"\xb0\x5d\x00"),
            _event(0, b"\xb0\x64\x7f"),
            _event(0, b"\xb0\x65\x7f"),
            _event(0, b"\xc0\x18"),
        )
    )
    melody = setup + _event(0, b"\x90\x3c\x55")
    melody += _event(480, b"\x80\x3c\x20")
    melody += _event(0, b"\xb0\x02\x40")
    melody += _meta(480, 0x2F)

    result = preflight_midi(_smf(conductor, melody), DEFAULT_LIMITS)

    assert result.metadata is not None
    assert result.metadata.format_type == 1
    assert result.metadata.note_track == 1
    assert result.metadata.note_channel == 0
    assert result.metadata.duration_ticks == 960
    assert result.metadata.title == "Melody"
    assert result.metadata.rights == "copyright-notice:Example Org"
    assert not any(
        diagnostic.severity is DiagnosticSeverity.ERROR
        for diagnostic in result.diagnostics
    )
    assert _codes(result) == [
        ImportCode.MIDI_HARMONY_UNPROVIDED,
        ImportCode.MIDI_PERFORMANCE_DATA_IGNORED,
    ]


def test_channel_prefix_rejects_out_of_range_channel() -> None:
    melody = _prefix() + _meta(0, 0x20, b"\x10")
    melody += _event(0, b"\x90\x3c\x40")
    melody += _event(480, b"\x80\x3c\x00") + _meta(0, 0x2F)

    _assert_error(_smf(melody), ImportCode.MALFORMED_MIDI)


def test_running_status_and_velocity_zero_note_off_are_equivalent() -> None:
    payload = _prefix()
    payload += _event(0, b"\x90\x3c\x40")
    payload += _event(480, b"\x3c\x00")
    payload += _meta(0, 0x2F)

    result = preflight_midi(_smf(payload), DEFAULT_LIMITS)

    assert result.metadata is not None
    assert result.metadata.note_events[0].duration == Fraction(1)


@pytest.mark.parametrize("next_pitch", [60, 62])
def test_same_tick_note_handoff_is_independent_of_raw_on_off_order(
    next_pitch: int,
) -> None:
    payload = _prefix()
    payload += _event(0, b"\x90\x3c\x40")
    payload += _event(480, bytes((0x90, next_pitch, 0x40)))
    payload += _event(0, b"\x80\x3c\x00")
    payload += _event(480, bytes((0x80, next_pitch, 0)))
    payload += _meta(0, 0x2F)

    result = preflight_midi(_smf(payload), DEFAULT_LIMITS)

    assert result.metadata is not None
    assert [(note.pitch, note.onset, note.duration) for note in result.metadata.note_events] == [
        (60, Fraction(0), Fraction(1)),
        (next_pitch, Fraction(1), Fraction(1)),
    ]


def test_same_tick_new_attack_and_release_is_rejected_as_zero_duration() -> None:
    payload = _prefix()
    payload += _event(0, b"\x90\x3c\x40")
    payload += _event(0, b"\x80\x3c\x00")
    payload += _meta(0, 0x2F)

    _assert_error(_smf(payload), ImportCode.MIDI_NOTE_PAIRING_ERROR)


def test_missing_key_is_explicitly_unprovided_but_invalid_key_is_not() -> None:
    missing = preflight_midi(_valid_midi(key=None), DEFAULT_LIMITS)
    assert missing.metadata is not None
    assert missing.metadata.key == "key-signature:unprovided"
    assert ImportCode.MIDI_KEY_UNPROVIDED in _codes(missing)

    invalid = _assert_error(_valid_midi(key=b"\x08\x00"), ImportCode.UNSUPPORTED_KEY)
    assert ImportCode.MIDI_KEY_UNPROVIDED not in _codes(invalid)

    invalid_length = _assert_error(_valid_midi(key=b"\x00"), ImportCode.UNSUPPORTED_KEY)
    assert ImportCode.MIDI_KEY_UNPROVIDED not in _codes(invalid_length)


@pytest.mark.parametrize(("fifths", "mode", "expected"), _KEY_CASES)
def test_all_traditional_midi_key_signatures_map_exactly(
    fifths: int,
    mode: int,
    expected: str,
) -> None:
    result = preflight_midi(
        _valid_midi(key=bytes((fifths & 0xFF, mode))),
        DEFAULT_LIMITS,
    )

    assert result.metadata is not None
    assert result.metadata.key == expected
    assert result.metadata.key_fifths == fifths
    assert result.metadata.key_mode == mode


@pytest.mark.parametrize("meta_type", [0x08, 0x09, 0x7F, 0x10])
def test_unknown_or_sequencer_meta_events_are_rejected(meta_type: int) -> None:
    _assert_error(
        _valid_midi(before_note=_meta(0, meta_type, b"x")),
        ImportCode.MIDI_META_EVENT_UNSUPPORTED,
    )


def test_non_selected_bounded_text_is_ignored_once() -> None:
    raw = _valid_midi(
        title=b"Lead",
        before_note=_meta(0, 0x01, b"comment") + _meta(0, 0x05, b"lyric"),
    )
    result = preflight_midi(raw, DEFAULT_LIMITS)

    assert result.metadata is not None
    assert result.metadata.title == "Lead"
    assert _codes(result).count(ImportCode.MIDI_TEXT_IGNORED) == 1


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"", ImportCode.MALFORMED_MIDI),
        (b"RIFF\x00\x00\x00\x00RMID", ImportCode.MALFORMED_MIDI),
        (
            b"MThd\x00\x00\x00\x07\x00\x00\x00\x01\x01\xe0\x00",
            ImportCode.MALFORMED_MIDI,
        ),
        (
            b"MThd\x00\x00\x00\x06\x00\x02\x00\x01\x01\xe0",
            ImportCode.UNSUPPORTED_MIDI_FORMAT,
        ),
        (
            b"MThd\x00\x00\x00\x06\x00\x00\x00\x02\x01\xe0",
            ImportCode.MALFORMED_MIDI,
        ),
        (
            b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x00",
            ImportCode.MALFORMED_MIDI,
        ),
        (
            b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\xe7\x28",
            ImportCode.MIDI_TIMING_UNSUPPORTED,
        ),
    ],
)
def test_header_envelope_failures_are_typed(raw: bytes, code: ImportCode) -> None:
    _assert_error(raw, code)


def test_chunk_count_lengths_eot_and_eof_are_exact() -> None:
    valid = _valid_midi()
    _assert_error(valid.replace(b"MTrk", b"JUNK", 1), ImportCode.MALFORMED_MIDI)
    _assert_error(valid[:-1], ImportCode.MALFORMED_MIDI)
    _assert_error(valid + b"x", ImportCode.MALFORMED_MIDI)

    no_eot_payload = _prefix() + _event(0, b"\x90\x3c\x40")
    no_eot_payload += _event(480, b"\x80\x3c\x00")
    _assert_error(_smf(no_eot_payload), ImportCode.MALFORMED_MIDI)

    bad_eot = no_eot_payload + _meta(0, 0x2F, b"x")
    _assert_error(_smf(bad_eot), ImportCode.MALFORMED_MIDI)

    after_eot = no_eot_payload + _meta(0, 0x2F) + b"\x00"
    _assert_error(_smf(after_eot), ImportCode.MALFORMED_MIDI)


@pytest.mark.parametrize(
    "payload",
    [
        b"\x80\x00\xff\x2f\x00",
        b"\x81\x80\x80\x80\x00\xff\x2f\x00",
        b"\x00\x3c\x40\x00\xff\x2f\x00",
        b"\x00\x90\x80\x40\x00\xff\x2f\x00",
        b"\x00\x90\x3c\x40\x00\xff\x01\x00\x00\x3c\x00",
    ],
)
def test_vlq_running_status_and_data_byte_corruption_are_malformed(payload: bytes) -> None:
    _assert_error(_smf(payload), ImportCode.MALFORMED_MIDI)


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (b"\xb0\x40\x7f", ImportCode.MIDI_CONTROL_CHANGE_UNSUPPORTED),
        (b"\xe0\x01\x40", ImportCode.MIDI_PITCH_BEND_UNSUPPORTED),
        (b"\xa0\x3c\x20", ImportCode.MIDI_EVENT_UNSUPPORTED),
        (b"\xd0\x20", ImportCode.MIDI_EVENT_UNSUPPORTED),
    ],
)
def test_unsupported_channel_messages_have_typed_location(
    event: bytes,
    code: ImportCode,
) -> None:
    result = _assert_error(_valid_midi(before_note=_event(0, event)), code)
    diagnostic = next(item for item in result.diagnostics if item.code is code)
    assert diagnostic.location is not None
    assert diagnostic.location.track_index == 0
    assert diagnostic.location.channel == 1
    assert diagnostic.location.tick == 0
    assert diagnostic.location.event_index == 3


def test_sysex_and_percussion_are_typed() -> None:
    _assert_error(
        _valid_midi(before_note=_event(0, b"\xf0\x01\x00")),
        ImportCode.MIDI_SYSEX_UNSUPPORTED,
    )
    percussion = _valid_midi(note_on=b"\x99\x3c\x40", note_off=b"\x89\x3c\x00")
    result = _assert_error(percussion, ImportCode.MIDI_PERCUSSION_UNSUPPORTED)
    diagnostic = next(
        item for item in result.diagnostics if item.code is ImportCode.MIDI_PERCUSSION_UNSUPPORTED
    )
    assert diagnostic.location is not None
    assert diagnostic.location.channel == 10


def test_stream_selection_pairing_and_polyphony_fail_closed() -> None:
    no_notes = _smf(_prefix() + _meta(0, 0x2F))
    _assert_error(no_notes, ImportCode.NO_NOTE_BEARING_STREAM)

    two_streams = _prefix() + _event(0, b"\x90\x3c\x40")
    two_streams += _event(0, b"\x91\x40\x40")
    two_streams += _event(480, b"\x80\x3c\x00")
    two_streams += _event(0, b"\x81\x40\x00") + _meta(0, 0x2F)
    _assert_error(_smf(two_streams), ImportCode.MULTIPLE_NOTE_BEARING_STREAMS)

    polyphonic = _prefix() + _event(0, b"\x90\x3c\x40")
    polyphonic += _event(120, b"\x90\x40\x40")
    polyphonic += _event(120, b"\x80\x3c\x00")
    polyphonic += _event(120, b"\x80\x40\x00") + _meta(0, 0x2F)
    _assert_error(_smf(polyphonic), ImportCode.MIDI_POLYPHONY_UNSUPPORTED)

    orphan = _prefix() + _event(0, b"\x80\x3c\x00")
    orphan += _event(0, b"\x90\x40\x40")
    orphan += _event(480, b"\x80\x40\x00") + _meta(0, 0x2F)
    _assert_error(_smf(orphan), ImportCode.MIDI_NOTE_PAIRING_ERROR)

    dangling = _prefix() + _event(0, b"\x90\x3c\x40") + _meta(480, 0x2F)
    _assert_error(_smf(dangling), ImportCode.MIDI_NOTE_PAIRING_ERROR)


@pytest.mark.parametrize(
    ("prefix", "code"),
    [
        (_meta(0, 0x58, b"\x04\x02\x18\x08"), ImportCode.MISSING_TEMPO),
        (
            _meta(0, 0x51, b"\x07\xa1\x20") * 2
            + _meta(0, 0x58, b"\x04\x02\x18\x08"),
            ImportCode.TEMPO_CHANGE_UNSUPPORTED,
        ),
        (
            _meta(1, 0x51, b"\x07\xa1\x20")
            + _meta(0, 0x58, b"\x04\x02\x18\x08"),
            ImportCode.UNSUPPORTED_TEMPO,
        ),
        (
            _meta(0, 0x51, b"\x00\xea\x5f")
            + _meta(0, 0x58, b"\x04\x02\x18\x08"),
            ImportCode.UNSUPPORTED_TEMPO,
        ),
        (_meta(0, 0x51, b"\x07\xa1\x20"), ImportCode.MISSING_TIME_SIGNATURE),
        (
            _meta(0, 0x51, b"\x07\xa1\x20")
            + _meta(0, 0x58, b"\x03\x02\x18\x08"),
            ImportCode.UNSUPPORTED_TIME_SIGNATURE,
        ),
    ],
)
def test_required_global_metadata_is_exact(prefix: bytes, code: ImportCode) -> None:
    payload = prefix + _event(0, b"\x90\x3c\x40")
    payload += _event(480, b"\x80\x3c\x00") + _meta(0, 0x2F)
    _assert_error(_smf(payload), code)


@pytest.mark.parametrize(
    "rpn",
    [
        (b"\xb0\x06\x0c",),
        (b"\xb0\x64\x01", b"\xb0\x65\x00", b"\xb0\x06\x0c"),
        (b"\xb0\x64\x00", b"\xb0\x65\x00", b"\xb0\x06\x0c"),
        (
            b"\xb1\x64\x00",
            b"\xb1\x65\x00",
            b"\xb1\x06\x0c",
            b"\xb1\x64\x7f",
            b"\xb1\x65\x7f",
        ),
    ],
)
def test_rpn_data_entry_only_accepts_complete_note_channel_setup(
    rpn: tuple[bytes, ...],
) -> None:
    before_note = b"".join(_event(0, event) for event in rpn)
    _assert_error(
        _valid_midi(before_note=before_note),
        ImportCode.MIDI_CONTROL_CHANGE_UNSUPPORTED,
    )


def test_rpn_after_first_note_and_non_center_or_duplicate_bend_are_rejected() -> None:
    rpn = b"".join(
        _event(0, event)
        for event in (
            b"\xb0\x64\x00",
            b"\xb0\x65\x00",
            b"\xb0\x06\x0c",
            b"\xb0\x64\x7f",
            b"\xb0\x65\x7f",
        )
    )
    _assert_error(
        _valid_midi(between_notes=rpn),
        ImportCode.MIDI_CONTROL_CHANGE_UNSUPPORTED,
    )
    _assert_error(
        _valid_midi(before_note=_event(0, b"\xe0\x01\x40")),
        ImportCode.MIDI_PITCH_BEND_UNSUPPORTED,
    )
    _assert_error(
        _valid_midi(
            before_note=_event(0, b"\xe0\x00\x40")
            + _event(0, b"\xe0\x00\x40")
        ),
        ImportCode.MIDI_PITCH_BEND_UNSUPPORTED,
    )
    _assert_error(
        _valid_midi(between_notes=_event(0, b"\xe0\x00\x40")),
        ImportCode.MIDI_PITCH_BEND_UNSUPPORTED,
    )


def test_general_controller_allowlist_is_loss_only() -> None:
    before = b"".join(
        _event(0, bytes((0xB0, controller, 1)))
        for controller in (0, 1, 2, 7, 10, 11, 32, 91, 93, 121)
    )
    result = preflight_midi(_valid_midi(before_note=before), DEFAULT_LIMITS)

    assert result.metadata is not None
    assert _codes(result).count(ImportCode.MIDI_PERFORMANCE_DATA_IGNORED) == 1


def test_canonical_gap_over_four_byte_vlq_returns_typed_failure() -> None:
    maximum_delta = 0x0FFFFFFF
    payload = _prefix() + _event(0, b"\x90\x3c\x40")
    payload += _event(1, b"\x80\x3c\x00")
    payload += _event(maximum_delta - 1, b"\xb0\x02\x00")
    payload += _meta(maximum_delta, 0x2F)

    result = preflight_midi(
        _smf(payload),
        replace(DEFAULT_LIMITS, max_midi_quarter_span=2_000_000),
    )

    assert result.metadata is None
    assert result.canonical_midi is None
    assert ImportCode.INPUT_LIMIT_EXCEEDED in _codes(result)
    assert result.diagnostics[-1].location is not None
    assert result.diagnostics[-1].location.element == "canonical-midi"
