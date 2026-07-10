"""Chord voicing + rhythm patterns for accompaniment (T2).

Realizes each chord as a bass root + mid-register chord tones, then lays them out
per beat as an arpeggio (one note per beat) or a strum (block chord). The solver
turns the resulting target into a playable Tab.
"""

from fractions import Fraction

from fretsure.ir import ChordSymbol, Note

_BASS_REGISTER = 48  # C3-ish
_TONE_REGISTER = 60  # C4-ish


def realize_chord(chord: ChordSymbol) -> tuple[int, list[int]]:
    root = _BASS_REGISTER + chord.root_pc
    tones = sorted(_TONE_REGISTER + pc for pc in chord.pitch_classes)
    return root, tones


def arpeggio(chord: ChordSymbol, *, beats_per_bar: int = 4) -> tuple[Note, ...]:
    root, tones = realize_chord(chord)
    upper = [t for t in tones if t % 12 != chord.root_pc]
    voicing = [root, *upper]
    notes = [
        Note(
            chord.onset + beat,
            Fraction(1),
            voicing[beat % len(voicing)],
            "bass" if voicing[beat % len(voicing)] == root else "harmony",
        )
        for beat in range(beats_per_bar)
    ]
    return tuple(notes)


def strum(chord: ChordSymbol, *, beats_per_bar: int = 4, max_voices: int = 4) -> tuple[Note, ...]:
    root, tones = realize_chord(chord)
    upper = [t for t in tones if t % 12 != chord.root_pc][: max_voices - 1]
    voicing = [root, *upper]
    notes: list[Note] = []
    for beat in range(beats_per_bar):
        onset = chord.onset + beat
        for pitch in voicing:
            notes.append(Note(onset, Fraction(1), pitch, "bass" if pitch == root else "harmony"))
    return tuple(notes)
