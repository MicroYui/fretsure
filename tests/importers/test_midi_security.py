from __future__ import annotations

from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from fretsure.importers._midi_preflight import MIDIPreflightResult, preflight_midi
from fretsure.importers.contracts import DEFAULT_LIMITS, ImportCode, ImportLimits


def _vlq(value: int) -> bytes:
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


def _smf(*tracks: bytes, ppqn: int = 480) -> bytes:
    format_type = 0 if len(tracks) == 1 else 1
    header = (
        b"MThd\x00\x00\x00\x06"
        + format_type.to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + ppqn.to_bytes(2, "big")
    )
    return header + b"".join(_track(track) for track in tracks)


def _prefix() -> bytes:
    return (
        _meta(0, 0x51, b"\x07\xa1\x20")
        + _meta(0, 0x58, b"\x04\x02\x18\x08")
        + _meta(0, 0x59, b"\x00\x00")
    )


def _valid(*, title: bytes | None = None, copyright_text: bytes | None = None) -> bytes:
    payload = _prefix()
    if title is not None:
        payload += _meta(0, 0x03, title)
    if copyright_text is not None:
        payload += _meta(0, 0x02, copyright_text)
    payload += _event(0, b"\x90\x3c\x40")
    payload += _event(480, b"\x80\x3c\x00")
    payload += _meta(480, 0x2F)
    return _smf(payload)


def _span_fixture(kind: str, span: int) -> bytes:
    payload = _prefix()
    if kind == "leading-rest":
        payload += _event(span - 1, b"\x90\x3c\x40")
        payload += _event(1, b"\x80\x3c\x00")
        payload += _meta(0, 0x2F)
    elif kind == "long-note":
        payload += _event(0, b"\x90\x3c\x40")
        payload += _event(span, b"\x80\x3c\x00")
        payload += _meta(0, 0x2F)
    elif kind == "trailing-silence":
        payload += _event(0, b"\x90\x3c\x40")
        payload += _event(1, b"\x80\x3c\x00")
        payload += _meta(span - 1, 0x2F)
    else:
        raise AssertionError(f"unknown span fixture {kind}")
    return _smf(payload, ppqn=1)


def _assert_success(data: bytes, limits: ImportLimits) -> MIDIPreflightResult:
    result = preflight_midi(data, limits)
    assert result.metadata is not None, [item.code for item in result.diagnostics]
    assert result.canonical_midi is not None
    return result


def _assert_limit(data: bytes, limits: ImportLimits) -> MIDIPreflightResult:
    result = preflight_midi(data, limits)
    assert result.metadata is None
    assert result.canonical_midi is None
    assert ImportCode.INPUT_LIMIT_EXCEEDED in [item.code for item in result.diagnostics]
    return result


def test_raw_byte_limit_is_inclusive_and_checked_before_parsing() -> None:
    raw = _valid()
    _assert_success(raw, replace(DEFAULT_LIMITS, max_midi_bytes=len(raw)))
    _assert_limit(raw, replace(DEFAULT_LIMITS, max_midi_bytes=len(raw) - 1))


def test_track_event_note_and_tick_limits_are_inclusive() -> None:
    conductor = _prefix() + _meta(0, 0x2F)
    melody = _event(0, b"\x90\x3c\x40")
    melody += _event(480, b"\x80\x3c\x00") + _meta(0, 0x2F)
    format_one = _smf(conductor, melody)
    _assert_success(format_one, replace(DEFAULT_LIMITS, max_midi_tracks=2))
    _assert_limit(format_one, replace(DEFAULT_LIMITS, max_midi_tracks=1))

    raw = _valid()
    _assert_success(raw, replace(DEFAULT_LIMITS, max_midi_events=6))
    _assert_limit(raw, replace(DEFAULT_LIMITS, max_midi_events=5))
    _assert_success(raw, replace(DEFAULT_LIMITS, max_midi_notes=1))
    _assert_limit(raw, replace(DEFAULT_LIMITS, max_midi_notes=0))
    _assert_success(raw, replace(DEFAULT_LIMITS, max_midi_tick=960))
    _assert_limit(raw, replace(DEFAULT_LIMITS, max_midi_tick=959))


def test_quarter_span_limit_is_inclusive_for_all_duration_shapes() -> None:
    limits = replace(DEFAULT_LIMITS, max_midi_quarter_span=4)
    for kind in ("leading-rest", "long-note", "trailing-silence"):
        accepted = _assert_success(_span_fixture(kind, 4), limits)
        assert accepted.metadata is not None
        assert accepted.metadata.duration_beats == 4

        rejected = _assert_limit(_span_fixture(kind, 5), limits)
        diagnostic = next(
            item
            for item in rejected.diagnostics
            if item.code is ImportCode.INPUT_LIMIT_EXCEEDED
        )
        assert diagnostic.location is not None
        assert diagnostic.location.element == "track-duration"
        assert diagnostic.location.tick == 5


def test_single_and_cumulative_meta_payload_limits_gate_before_copy() -> None:
    raw = _valid(title=b"Lead", copyright_text=b"CC0")
    _assert_success(raw, replace(DEFAULT_LIMITS, max_midi_text_bytes=4))
    _assert_limit(raw, replace(DEFAULT_LIMITS, max_midi_text_bytes=3))

    # tempo(3) + time(4) + key(2) + title(4) + copyright(3) + EOT(0)
    _assert_success(raw, replace(DEFAULT_LIMITS, max_midi_total_text_bytes=16))
    _assert_limit(raw, replace(DEFAULT_LIMITS, max_midi_total_text_bytes=15))


def test_huge_declared_chunks_and_lengths_return_bounded_typed_failures() -> None:
    header = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
    result = preflight_midi(header + b"MTrk\xff\xff\xff\xff", DEFAULT_LIMITS)
    assert result.metadata is None
    assert [item.code for item in result.diagnostics] == [ImportCode.MALFORMED_MIDI]

    payload = _prefix() + b"\x00\xff\x01\x8f\xff\xff\x7f" + _meta(0, 0x2F)
    result = preflight_midi(_smf(payload), DEFAULT_LIMITS)
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code is ImportCode.INPUT_LIMIT_EXCEEDED


def test_source_diagnostics_are_capped_with_one_limit_sentinel() -> None:
    payload = _prefix()
    payload += b"".join(_event(0, b"\xb0\x40\x7f") for _ in range(300))
    payload += _event(0, b"\x90\x3c\x40")
    payload += _event(480, b"\x80\x3c\x00") + _meta(0, 0x2F)

    result = preflight_midi(_smf(payload), DEFAULT_LIMITS)

    assert result.metadata is None
    assert result.canonical_midi is None
    assert len(result.diagnostics) == 257
    assert all(
        item.code is ImportCode.MIDI_CONTROL_CHANGE_UNSUPPORTED
        for item in result.diagnostics[:256]
    )
    assert result.diagnostics[-1].code is ImportCode.INPUT_LIMIT_EXCEEDED
    assert "omitted" in result.diagnostics[-1].message


def test_preflight_requires_exact_bytes_and_exact_limits() -> None:
    raw = _valid()
    for value in (bytearray(raw), memoryview(raw), "not-midi"):
        result = preflight_midi(value, DEFAULT_LIMITS)  # type: ignore[arg-type]
        assert [item.code for item in result.diagnostics] == [ImportCode.INVALID_INPUT]

    class LimitsSubclass(ImportLimits):
        pass

    result = preflight_midi(raw, LimitsSubclass())
    assert [item.code for item in result.diagnostics] == [
        ImportCode.INPUT_LIMIT_EXCEEDED
    ]


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=4096))
def test_arbitrary_bytes_never_escape_the_typed_preflight_boundary(data: bytes) -> None:
    result = preflight_midi(data, DEFAULT_LIMITS)

    assert type(result) is MIDIPreflightResult
    assert type(result.diagnostics) is tuple
    assert all(type(item.message) is str for item in result.diagnostics)
    assert result.metadata is None or type(result.canonical_midi) is bytes
