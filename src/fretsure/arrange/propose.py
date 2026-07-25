"""Deterministic fallback arrangement proposer.

Keep source melody and bass voices and drop optional harmony notes.  MusicXML
lead sheets commonly carry bass intent only as chord annotations, so when (and
only when) the source has no explicit bass voice, seed a lowest-playable chord
root through each chord segment.  Synthesized roots are re-articulated at melody
attacks so a new attack frame can choose a fresh fingering instead of inheriting
the left finger of one long held bass.  This is an arrangement fallback, not
importer data: the source :class:`~fretsure.ir.MusicIR` remains an honest
transcription of the file.
"""

from bisect import bisect_left, bisect_right
from fractions import Fraction

from fretsure.arrange.style_profiles import corpus_style_harmony_offsets
from fretsure.arrange.styles import DEFAULT_ARRANGEMENT_STYLE, arrangement_style
from fretsure.difficulty.tiers import difficulty_tier as resolve_difficulty_tier
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import ChordSymbol, MusicIR, Note, snapshot_music_ir
from fretsure.oracle.input import ensure_solver_domain
from fretsure.oracle.profiles import MEDIAN_HAND, Profile


def _lowest_pitch_with_pc(lowest: int, pitch_class: int) -> int:
    """Return the lowest MIDI pitch >= ``lowest`` with ``pitch_class``."""
    return lowest + (pitch_class - lowest) % 12


def _bar_duration(ir: MusicIR) -> Fraction:
    numerator, denominator = ir.meta.time_sig
    if numerator <= 0 or denominator <= 0:
        return Fraction(1)
    return Fraction(numerator * 4, denominator)


def _chords_by_onset(ir: MusicIR) -> tuple[ChordSymbol, ...]:
    # A normalised lead sheet has one symbol per onset.  If an internal caller
    # supplies duplicates, prefer the final annotation deterministically rather
    # than creating two simultaneous bass notes.
    by_onset = {chord.onset: chord for chord in ir.chords}
    return tuple(by_onset[onset] for onset in sorted(by_onset))


def _seed_chord_root_bass(ir: MusicIR, tuning: tuple[int, ...], capo: int) -> tuple[Note, ...]:
    chords = _chords_by_onset(ir)
    if not chords:
        return ()
    if not tuning:
        raise ValueError("tuning must contain at least one open-string pitch")

    inferred_piece_end = max((n.onset + n.duration for n in ir.notes), default=Fraction(0))
    piece_end = ir.meta.duration_beats if ir.meta.duration_beats is not None else inferred_piece_end
    if piece_end <= chords[-1].onset:
        piece_end = chords[-1].onset + _bar_duration(ir)
    lowest = min(tuning) + capo
    melody_attacks = tuple(sorted({note.onset for note in ir.notes if note.voice == "melody"}))
    bass: list[Note] = []
    for index, chord in enumerate(chords):
        end = chords[index + 1].onset if index + 1 < len(chords) else piece_end
        if end <= chord.onset:
            # Defensive fallback for malformed internal IR.  ``run_pipeline``
            # validates imported IR, but this public helper remains total for a
            # chord at/after the final note.
            end = chord.onset + _bar_duration(ir)

        # A synthesized accompaniment note is ours to articulate.  Split it at
        # each retained melody attack inside the chord segment: the root still
        # covers the same half-open interval continuously, but every changing
        # attack frame may assign the bass a different left finger.  Bisect keeps
        # this O((notes + chords) log notes) rather than rescanning all attacks
        # for every chord.
        first = bisect_right(melody_attacks, chord.onset)
        stop = bisect_left(melody_attacks, end)
        starts = (chord.onset, *melody_attacks[first:stop])
        pitch = _lowest_pitch_with_pc(lowest, chord.root_pc)
        for split_index, onset in enumerate(starts):
            split_end = starts[split_index + 1] if split_index + 1 < len(starts) else end
            bass.append(
                Note(
                    onset,
                    split_end - onset,
                    pitch,
                    "bass",
                )
            )
    return tuple(bass)


def propose_fingerstyle(
    ir: MusicIR,
    tuning: tuple[int, ...] = STANDARD_TUNING,
    capo: int = 0,
    *,
    profile: Profile = MEDIAN_HAND,
    tempo_bpm: float = 90.0,
) -> tuple[Note, ...]:
    """Build a sparse deterministic target while preserving source bass intent.

    Explicit source bass wins wholesale: chord-root targets are only synthesized
    when the source contains no bass voice at all, so imported and authored bass
    lines are never doubled or silently replaced.
    """
    # This helper is a public arrangement entry point, not merely an internal
    # implementation detail.  Validate before chord-root seeding reaches
    # ``min(tuning)`` so malformed configs fail with the same typed contract as
    # the solver/harness rather than leaking IndexError/ValueError variants.
    ir = snapshot_music_ir(ir)
    notes, tuning, capo, profile, tempo_bpm = ensure_solver_domain(
        ir.notes,
        tuning,
        capo,
        profile,
        tempo_bpm=tempo_bpm,
    )
    ir = MusicIR(notes, tuple(ir.chords), ir.meta)

    kept = [n for n in ir.notes if n.voice in ("melody", "bass")]
    if not any(n.voice == "bass" for n in kept):
        occupied = {(n.onset, n.pitch) for n in kept}
        kept.extend(
            bass
            for bass in _seed_chord_root_bass(ir, tuning, capo)
            if (bass.onset, bass.pitch) not in occupied
        )
    kept.sort(key=lambda n: (n.onset, n.pitch, n.voice))
    return tuple(kept)


def _style_harmony_offsets(
    style: str,
    difficulty_tier: str,
) -> tuple[tuple[Fraction, Fraction], ...]:
    if difficulty_tier == "beginner":
        return ()
    if style in {"jazz", "rnb"}:
        return corpus_style_harmony_offsets(style, difficulty_tier)
    advanced: tuple[tuple[Fraction, Fraction], ...] = ()
    if difficulty_tier == "advanced":
        if style == "fingerstyle":
            return ((Fraction(2), Fraction(1)),)
        if style == "classical":
            advanced = ((Fraction(2), Fraction(1)),)
    if style == "classical":
        return ((Fraction(1), Fraction(1)), (Fraction(3), Fraction(1)), *advanced)
    return ()


def _harmony_pitch(
    pitch_class: int,
    *,
    low: int,
    high: int,
    melody_ceiling: int | None,
) -> int | None:
    upper = high if melody_ceiling is None else min(high, melody_ceiling - 3)
    candidates = tuple(pitch for pitch in range(low, upper + 1) if pitch % 12 == pitch_class)
    if not candidates:
        return None
    preferred = max(low + 12, 48)
    return min(candidates, key=lambda pitch: (abs(pitch - preferred), -pitch))


def propose_style(
    ir: MusicIR,
    style: str = DEFAULT_ARRANGEMENT_STYLE,
    tuning: tuple[int, ...] = STANDARD_TUNING,
    capo: int = 0,
    *,
    profile: Profile = MEDIAN_HAND,
    tempo_bpm: float = 90.0,
    difficulty_tier: str = "intermediate",
) -> tuple[Note, ...]:
    """Build the deterministic offline sketch for one public arrangement style.

    All styles share the conservative melody/root foundation.  Classical,
    jazz, and R&B add short chord-tone answers at style-specific rhythmic
    positions. Beginner omits optional inner voices, intermediate preserves the
    sparse baseline, and advanced permits one additional answer. The solver
    still decides whether and how those notes can be fingered.
    """

    definition = arrangement_style(style)
    tier = resolve_difficulty_tier(difficulty_tier)
    base = list(
        propose_fingerstyle(
            ir,
            tuning,
            capo,
            profile=profile,
            tempo_bpm=tempo_bpm,
        )
    )
    offsets = _style_harmony_offsets(definition.id, tier.name)
    if not offsets or not ir.chords:
        return tuple(base)

    source = snapshot_music_ir(ir)
    chords = _chords_by_onset(source)
    inferred_end = max(
        (note.onset + note.duration for note in source.notes),
        default=Fraction(0),
    )
    piece_end = source.meta.duration_beats or inferred_end
    low = min(tuning) + capo
    high = max(tuning) + capo + profile.max_fret
    occupied = {(note.onset, note.pitch) for note in base}
    melody = tuple(note for note in source.notes if note.voice == "melody")

    for chord_index, chord in enumerate(chords):
        segment_end = chords[chord_index + 1].onset if chord_index + 1 < len(chords) else piece_end
        if segment_end <= chord.onset:
            continue
        colors = tuple(
            pitch_class
            for pitch_class in sorted(chord.pitch_classes)
            if pitch_class != chord.root_pc
        )
        if not colors:
            continue
        for answer_index, (offset, duration) in enumerate(offsets):
            onset = chord.onset + offset
            if onset >= segment_end:
                continue
            sounding_melody = tuple(
                note.pitch for note in melody if note.onset <= onset < note.onset + note.duration
            )
            pitch = _harmony_pitch(
                colors[answer_index % len(colors)],
                low=low,
                high=high,
                melody_ceiling=min(sounding_melody, default=None),
            )
            if pitch is None or (onset, pitch) in occupied:
                continue
            base.append(
                Note(
                    onset,
                    min(duration, segment_end - onset),
                    pitch,
                    "harmony",
                )
            )
            occupied.add((onset, pitch))
    return tuple(sorted(base, key=lambda note: (note.onset, note.pitch, note.voice)))


__all__ = ["propose_fingerstyle", "propose_style"]
