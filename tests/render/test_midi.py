from __future__ import annotations

from fractions import Fraction

import pytest

from fretsure.render.midi import (
    MIDI_TICKS_PER_QUARTER,
    MidiExportCode,
    MidiExportError,
    render_midi,
)
from fretsure.tab import Tab, TabNote

TUNING = (40, 45, 50, 55, 59, 64)


def _note(
    onset: Fraction,
    duration: Fraction,
    string: int,
    fret: int,
) -> TabNote:
    return TabNote(onset, duration, string, fret, 0 if fret == 0 else 1, "p")


def _read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if byte & 0x80 == 0:
            return value, offset


def _track_events(data: bytes) -> list[tuple[int, bytes]]:
    assert data[:4] == b"MThd"
    assert int.from_bytes(data[4:8], "big") == 6
    assert data[8:14] == b"\x00\x00\x00\x01\x01\xe0"
    assert data[14:18] == b"MTrk"
    track_size = int.from_bytes(data[18:22], "big")
    track = data[22 : 22 + track_size]
    assert 22 + track_size == len(data)

    events: list[tuple[int, bytes]] = []
    tick = 0
    offset = 0
    while offset < len(track):
        delta, offset = _read_vlq(track, offset)
        tick += delta
        status = track[offset]
        offset += 1
        if status == 0xFF:
            meta_type = track[offset]
            length, payload_offset = _read_vlq(track, offset + 1)
            payload = track[payload_offset : payload_offset + length]
            events.append((tick, bytes((status, meta_type)) + payload))
            offset = payload_offset + length
        elif status & 0xF0 == 0xC0:
            events.append((tick, track[offset - 1 : offset + 1]))
            offset += 1
        else:
            events.append((tick, track[offset - 1 : offset + 2]))
            offset += 2
    return events


def _error_from(call: object) -> MidiExportError:
    assert callable(call)
    with pytest.raises(MidiExportError) as caught:
        call()
    return caught.value


def test_midi_is_one_format_zero_guitar_track_with_exact_tempo() -> None:
    tab = Tab((_note(Fraction(0), Fraction(1), 0, 3),), TUNING, 2)

    first = render_midi(tab, tempo_bpm=96.0)
    second = render_midi(tab, tempo_bpm=96.0)

    assert first == second
    assert int.from_bytes(first[8:10], "big") == 0
    assert int.from_bytes(first[10:12], "big") == 1
    assert int.from_bytes(first[12:14], "big") == MIDI_TICKS_PER_QUARTER
    assert _track_events(first) == [
        (0, b"\xff\x51\x09\x89\x68"),  # 625,000 microseconds = 96 BPM
        (0, b"\xc0\x18"),  # one channel, GM Acoustic Guitar (nylon)
        (0, b"\x90\x2d\x50"),  # E2 + capo 2 + fret 3 = A2 (45)
        (480, b"\x80\x2d\x00"),
        (480, b"\xff\x2f"),
    ]


def test_chord_attacks_share_a_tick_and_note_off_precedes_rearticulation() -> None:
    notes = (
        _note(Fraction(1), Fraction(1), 5, 0),
        _note(Fraction(0), Fraction(1), 5, 0),
        _note(Fraction(0), Fraction(1), 1, 3),
    )

    events = _track_events(render_midi(Tab(notes, TUNING, 0), tempo_bpm=120.0))

    channel_events = [event for event in events if event[1][0] != 0xFF]
    assert channel_events == [
        (0, b"\xc0\x18"),
        (0, b"\x90\x30\x50"),
        (0, b"\x90\x40\x50"),
        (480, b"\x80\x30\x00"),
        (480, b"\x80\x40\x00"),
        (480, b"\x90\x40\x50"),
        (960, b"\x80\x40\x00"),
    ]


def test_sub_tick_duration_remains_one_audible_tick() -> None:
    tab = Tab(
        (_note(Fraction(0), Fraction(1, 10_000), 0, 0),),
        TUNING,
        0,
    )

    events = _track_events(render_midi(tab))

    assert (0, b"\x90\x28\x50") in events
    assert (1, b"\x80\x28\x00") in events


def test_invalid_tab_and_unrepresentable_midi_domains_are_typed() -> None:
    empty = Tab((), TUNING, 0)
    invalid = _error_from(lambda: render_midi(empty))
    assert invalid.code is MidiExportCode.INVALID_TAB

    remote = Tab(
        (_note(Fraction(1_000_000), Fraction(1), 0, 0),),
        TUNING,
        0,
    )
    timeline = _error_from(lambda: render_midi(remote))
    assert timeline.code is MidiExportCode.TIMELINE_UNREPRESENTABLE


def test_supported_slow_tempo_is_scaled_without_changing_playback_duration() -> None:
    ordinary = Tab((_note(Fraction(0), Fraction(1), 0, 0),), TUNING, 0)

    events = _track_events(render_midi(ordinary, tempo_bpm=1.0))
    tempo_event = next(
        payload
        for tick, payload in events
        if tick == 0 and payload[:2] == b"\xff\x51"
    )
    encoded_micros = int.from_bytes(tempo_event[2:], "big")
    note_off_tick = next(tick for tick, payload in events if payload[:1] == b"\x80")

    assert encoded_micros == 15_000_000
    assert note_off_tick == 4 * MIDI_TICKS_PER_QUARTER
    assert encoded_micros * note_off_tick // MIDI_TICKS_PER_QUARTER == 60_000_000
