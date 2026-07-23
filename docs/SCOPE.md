# Fretsure Oracle — Scope of Certification (honest claims)

The oracle makes a **narrow, bounded** claim. Read this before trusting a GREEN.

> **Current empirical status (2026-07-17):** GREEN is currently a deterministic,
> model-relative certification against `oracle@0.2.0`, `tab-input@0.2.0`, and a
> versioned, fingerprinted profile. The
> real human-played gold set has **not** been collected; the repository contains only
> six constructed sample labels for exercising the statistics pipeline. Therefore no
> real-player false-accept rate, calibrated AMBER band, or population-wide guarantee
> is claimed yet. This does not block software development, but it does block those
> empirical claims, profile/tier calibration, human-musicality conclusions, and any
> claim that a matching real player is guaranteed to perform every GREEN result.

> **Benchmark v2 evidence update (2026-07-23):** the 500-family current-model run is
> complete. Its controlled full policy reached 74/500 joint successes, while every
> high-complexity stratum and all three public controls were zero. Repair was
> `NOT_KEPT`, best-of-4 is `PROBATION_COST_UNKNOWN`, and critic is
> `HUMAN_BLOCKED_PROBATION`. These are software/statistical outcomes inside this scope,
> not evidence that real players can perform the outputs. Product defaults therefore
> use one candidate with repair and critic disabled; all three remain explicit opt-ins.

## What GREEN means

> For a valid ordinary six-string `Tab`, **GREEN** means that
> `oracle@0.2.0` found the exhibited fingering feasible under the parameters it
> actually consumes: hand span, hand-centre reach, shift-speed ceiling,
> right-finger repeat-rate ceiling, scale length and max fret from the selected profile, plus capo and the
> call-time tempo. `beats_per_bar` affects diagnostic measure/beat localization only,
> not the verdict. This is a certification inside the documented
> **simplified geometry + limited timing/rate model**, not yet a statement that a
> matching real-player population can play it.

Two claims are kept separate:

- **Model-relative claim (implemented)** — the decision procedure returns GREEN
  only when its modeled constraints admit the exhibited fingering. Property /
  metamorphic / mutation / N-version checks provide evidence about the implementation.
- **Empirical claim (pending)** — whether M is calibrated to real players must be
  tested on the human-played gold set and reported as the GREEN false-accept rate
  with a Clopper–Pearson upper bound + confusion matrix. Report inter-rater/retest
  κ only if collection includes a second rater or repeated-label subset. Those
  results do not exist yet.

## GREEN and faithfulness are independent gates

GREEN says **nothing** about whether an arrangement preserved the source melody,
bass, or harmony. That question is checked separately by `fidelity@0.3.0`: melody
and bass are aligned at exact onsets, while harmony is scored as chord-segment
pitch-class Jaccard, including sustained notes across segments. The source piece
boundary comes from `Meta.duration_beats` when available.

The 0.3 contract also records whether each dimension has source evidence. Scores are
nullable; `evaluated_dimensions` and `unavailable_dimensions` are canonically ordered,
complete complements, and `passed` is recomputable over at least one evaluated
dimension. Missing evidence is `None`/N/A, never a synthetic `1.0`. A melody-only
MIDI success therefore evaluates melody but makes bass-root and harmony unavailable.

A result can therefore be GREEN and still fail faithfulness. The implemented
MusicXML vertical slice has exercised exactly that outcome. It means “feasible in
the versioned playability model, but not faithful enough to the input,” not a
checker contradiction. Product/benchmark output may call a result a joint success
only when **oracle GREEN + faithfulness PASS** both hold. Neither gate is evidence
of human musical taste.

## Soundness direction

GREEN is the strictest verdict (passes the *pessimistic* profile), RED the
loosest (fails the *optimistic* profile), AMBER absorbs uncertainty in between.
**We never relax the GREEN threshold to reduce AMBER.** Once a real gold set exists,
the trust metric will be the GREEN false-accept rate with a one-sided confidence
bound; no such empirical rate is currently available.

## Public input-domain contract

`oracle@0.2.0` is defined for **valid, ordinary six-string `Tab` values**. The public
entry point enforces that boundary before any geometric or temporal predicate runs.
`tab-input@0.2.0` requires exact built-in containers and scalar types, six strictly
ascending MIDI tuning pitches, a bounded capo, non-negative exact-`Fraction` onsets,
positive exact-`Fraction` durations, string/fret/finger domains, bounded note/frame
counts, a valid `Profile`, and an exact built-in `int`/`float` finite tempo in the
published range. `bool`, numeric subclasses, NaN/Inf, hostile duck types and malformed
JSON adapters fail with typed input diagnostics; they never receive GREEN, AMBER or
RED.

Public MusicIR consumers use a separate deep-snapshot boundary before pipeline,
arranger, critic, accompaniment or faithfulness work. The accepted representation is
the exact frozen dataclass shape documented in the design spec, with at most 20,000
notes and 20,000 chords, 10 Mi cumulative text characters, 256-bit exact-Fraction
components, source tempo in 1..1000 BPM, and time-signature numerator/denominator in
1..32 / 1..64. Tier controls are likewise detached and exact-validated before use;
benchmark controls are rejected before corpus generation or LLM-factory calls (signed
63-bit seed, items 1..1000, bars 1..64, items×bars ≤4096, exact-bool paired flag).
Standard-JSON Trace output proves its compact escaped UTF-8 size is at most 10 MiB
before encoding and cross-checks the encoder result.

The solver has a separate typed boundary for `MusicIR.Note` targets and an explicit
resource envelope: beam ≤1024, at most 48 retained frame configurations, 64 retained
left-hand fingerings per geometry, 16 final full-oracle checks, and at most 12,000,000
weighted input-specific work units. Work estimation includes configuration generation,
state extensions, diversity selection, path reconstruction, and the final checker's
three profiles plus sorting/frame-pair costs. Its bounded search can conservatively
return typed `Infeasible`; that is not proof that no fingering exists. Every returned
`Tab` still passes a complete final `oracle@0.2.0` check, so incompleteness cannot leak
a RED result.

The `score-input@0.1.0` router selects the actual importer from the inert suffix and
binds its version in provenance. The current `musicxml@0.3.0` entry point narrows
untrusted MusicXML files before this boundary.
It accepts uncompressed `.musicxml`/`.xml` and strict `.mxl` containers whose root is
MusicXML 3.1/4.0 `score-partwise` in the frozen single-note-bearing-part/staff/voice monophonic
lead-sheet subset, with one
fixed positive decimal divisions value, one fixed traditional key signature, 4/4 and quarter-note
tempo, ordinary notes/rests/ties, and whitelisted root+kind harmony. `defusedxml`
enforces byte/tree limits and disables entity/external resolution; URI/resource
elements and `xlink:href` are rejected before canonical XML without DTD/entities is
handed to `music21`. Unsupported sounding semantics fail closed. After the complete
raw tree passes preflight, the importer reconstructs a bounded event-only XML with
only divisions, harmony roots/kinds, and note/rest/duration/tie events for `music21`
cross-validation. Credits, instruments/MIDI, layout/print, lyrics/voice, key visual
metadata, and legal non-note-bearing parts stay outside that third-party boundary;
raw warnings and normalized metadata remain authoritative. This MusicXML contract
does not approximate MIDI; MIDI has the separate narrow contract below.

An explicit `major` or `minor` remains the only interpreted key mode. MusicXML 4.0
may legally omit `<mode>` from a traditional `<key>` with exactly one bounded
`<fifths>`; the importer preserves that loss as
`key-signature:fifths=N;mode=unprovided` and emits a located
`KEY_MODE_UNPROVIDED` warning. It does not infer mode or tonic from notes, spelling,
harmony, or `music21`. MusicXML 3.1 with omitted mode, empty/other modes, duplicate
key children/elements, and key changes remain typed failures. Every successful
import is revalidated against the public 256-bit exact-Fraction MusicIR boundary.
Repeated authoritative scalars, non-ASCII XSD integers in semantic numeric fields,
malformed traditional-key shape, non-XML Unicode whitespace around authoritative
numeric tokens, oversized locations, and
diagnostic amplification are bounded typed failures. This makes omitted mode the only
new sounding-semantic success domain; visual metadata that the frozen loss policy
already ignores no longer succeeds or fails according to incidental `music21` parsing.

For `.mxl`, `mxl-container@0.1.0` validates bounded raw EOCD/central/local ZIP records
before constructing `ZipFile`, rejects ZIP64/SFX/encryption/special files/path aliases
and unsupported metadata, streams every member without extraction, and verifies
declared/actual size, CRC and deflate completion. Only the unique safe root selected by
`META-INF/container.xml` reaches the unchanged MusicXML parser. Passing this importer proves only that the file
fits the frozen input contract—it does not expand the oracle's certification scope.

Producer compatibility is evidence-specific: the runtime is pinned exactly to
`music21==10.5.0`; unedited music21 10.5.0 and musicxml 1.6.1 library/toolkit exports
pass. The exact frozen MuseScore Studio 4.7.4 XML/MXL artifacts in the producer
manifest also pass; those with omitted mode carry the descriptor and warning above.
This is not a claim that arbitrary MuseScore 4.7.4 scores, any other MuseScore
version, or full MusicXML are supported. The exact census, source/output differential
gate, and limitations are recorded in
[`2026-07-16-producer-musicxml-census.json`](experiments/2026-07-16-producer-musicxml-census.json)
and [`PRODUCER_MUSICXML_ACCEPTANCE.md`](PRODUCER_MUSICXML_ACCEPTANCE.md).

The separate `midi@0.1.0` entry point accepts only raw `.mid`/`.midi` Standard MIDI
Files in format 0 or 1 with PPQN timing, exactly one non-percussion monophonic
note-bearing `(track, channel)`, one fixed tick-zero tempo, fixed 4/4 meter, and an
optional fixed traditional major/minor key. Every note becomes `voice="melody"` and
`chords=()`; the importer does not infer track roles, bass, chord symbols, missing
key, notation grid, or quantized duration. Exact onset/duration comes from raw
tick/PPQN, and the note-bearing track's EOT defines the source duration.

A first-party complete parser validates SMF header/chunk count and sizes, exact EOF,
canonical 1–4-byte VLQs, running status, final EOT, data bytes, resource budgets,
note pairing, monophony, and the frozen event/controller/meta allowlist. Only a
zero-error parse is rebuilt as a minimal canonical SMF and cross-checked by exact
`music21==10.5.0` with `quantizePost=False`; music21 never receives raw untrusted
bytes and does not supply the timeline. Bounds are 10 MiB, 64 tracks, 250,000 events,
20,000 resolved notes, absolute tick `0..2**31-1`, PPQN `1..32767`, four VLQ bytes,
a 4096-quarter-note maximum note-track EOT span, 1 KiB per text/meta payload, 64 KiB
cumulative text/meta, and 256 retained diagnostics plus one overflow sentinel. The
EOT-span gate precedes music21 and jointly bounds leading rest, sounding duration,
and trailing silence.

The exact producer corpus contains two successes and two typed failures. MuseScore
Studio 4.7.4 melody-only output is a 7-beat performance with every note released one
PPQN tick early; music21 10.5.0 melody-only output retains notation durations and an
8-beat EOT. Both are authoritative and no cross-producer IR equality is claimed.
Both producers' frozen harmony-realized `supported_basic` exports fail typed rather
than triggering melody/chord-role heuristics. Evidence and limitations are recorded
in [`2026-07-17-midi-census.json`](experiments/2026-07-17-midi-census.json) and the
completed [`MIDI_ACCEPTANCE.md`](MIDI_ACCEPTANCE.md).

## In scope

- Left-hand geometry: millimetre fret spacing, hand-span-derived pairwise fingertip
  distance limits, finger–fret monotonicity, barre feasibility, and the capo-aware
  absolute neck bound (`0 <= fret` and `capo + fret <= max_fret`). Every note still
  sounding at an attack participates in finger count, monotonicity, barre and span;
  geometry is not limited to newly attacked notes.
- Right hand: p-i-m-a assignment, one-finger-per-string, ≤4 simultaneous
  plucks, single-finger repeat rate.
- Temporal: every fretted attack/release event uses half-open sounding intervals and
  release-before-attack ordering. The reachable hand-centre interval expands by
  `v_shift * dt` and is intersected with every active press interval
  `[press_x - reach_mm, press_x + reach_mm]`; guide notes do not reset or bypass this
  state, and open-only attacks do not change it. Finger reuse at a different fret and
  overlapping durations on the same physical string are separate typed sustain
  conflicts.
- Parameters actually consumed by the checker: hand span, hand-centre reach,
  shift-speed ceiling, right-finger repeat-rate ceiling, scale length, and max fret
  from `Profile`, plus `Tab.capo` and call-time tempo. `beats_per_bar` only localizes
  diagnostics. Tuning is validated for shape/order/MIDI and sounding-range safety,
  but the oracle does not compare a tab with a source `MusicIR` pitch intention; that
  belongs to the independent fidelity gate. Tier rules are a separate layer.

## Out of scope (explicitly)

- **Simplified geometry plus limited timing/rate predicates only.** We model pairwise
  fingertip distance, shift speed, sustain conflicts, and right-hand repeat rate,
  **not** tendon coupling,
  fatigue, or endurance. The current schema cannot detect or flag fatigue.
- **Only the notated tempo.** No rubato/expressive-timing modeling.
- **Not "idiomatic".** We certify that *a* feasible fingering exists, not that it
  is the most natural one. Musicality is a separate axis; the current LLM critic
  has not earned a human-musicality claim.
- **Model/profile-relative.** A preset currently selects model parameters only.
  Mapping those presets—or a user's measured hand size—to real-player capability
  is pending human calibration, so no “matching player” claim is made yet.
  Custom profiles are explicit model-relative numeric resources, not evidence that
  arbitrary values describe an ordinary human or guitar. The permissive numeric
  lower bound exists for deterministic API validation, not biological calibration.
- **Audio transcription** is out of the guaranteed path (best-effort, v2).
- **Deferred file semantics are not approximated.** MusicXML polyphony, multiple
  note-bearing parts/staves/voices, repeats/navigation, pickup/incomplete measures,
  key/time/tempo changes, tuplets/grace/cue/unpitched/microtonal/transposing input,
  nontraditional keys and modes other than explicit major/minor, complex/slash harmony,
  and performance techniques remain rejected. MIDI format 2/SMPTE, percussion,
  polyphony/multiple note streams, sustain/sostenuto, non-centre bend/tuning, SysEx,
  changing tempo/meter/key and unknown system/meta/control events are likewise
  rejected, not approximated. Audio is not a current guaranteed input.

## Techniques outside the current schema

`TabNote` currently has no technique field. Thumb-over, tapping, bends, hybrid
picking, and other technique-specific mechanics therefore cannot be represented
or certified by this oracle version. Same-finger, same-fret notes on multiple
strings—including partial-barre-like grips—are represented and checked by
`check_barre`, but pressure, contact continuity, full-vs-partial technique metadata,
and full barre mechanics are not modeled. Upstream importers must
reject or explicitly mark unsupported techniques; a GREEN verdict is valid only
for the ordinary fretting/plucking schema the checker actually receives.
Technique-aware AMBER diagnostics are a future requirement, not a current capability.

## Version stamping

The current distribution package is `0.6.0`. Every oracle verdict carries `checker_version`, `profile_version`, a canonical
profile SHA-256 fingerprint, and `input_schema_version`; current values are
`oracle@0.2.0` and `tab-input@0.2.0`, while the bundled preset remains
`median@0.1` with fingerprint
`fcefa5394cba876b94881fc77886e6db130d8be10406d46538ad6c83c40b7b62`.
Current CLI/product output names `fidelity@0.3.0` and `score-input@0.1.0` plus the
actual importer. Successful MusicXML imports carry `musicxml@0.3.0`; successful MIDI
imports carry `midi@0.1.0`. Both retain structured provenance and raw SHA-256; `.mxl`
additionally binds the root XML SHA-256, exact rootfile member and
`mxl-container@0.1.0`, while MIDI requires raw/root hashes to match and has no root
member/container version. Public contracts are `agent-trace@0.2.0`,
`fretsure-service@0.2.0`, `fretsure-api@0.2.0`, `fretsure-mcp@0.2.0`, and
`fretsure-web@0.2.0`. Exact benchmark
reproduction still requires the Git commit and corpus artifact hash. In particular, the 2026-07-10/11
LLM benchmark tables remain stamped `oracle@0.1.0` plus a legacy/unversioned fidelity
snapshot; they are not results under the current `oracle@0.2.0` / `fidelity@0.3.0`
checker pair.

## Gold/statistics trust boundary

The JSONL and in-memory label paths are resource-bounded and fail closed before
checker work: 64 MiB cumulative bytes/scalars, 200,000 declared/validated notes,
2,000,000 checker-work units, 1,000,000 physical lines, and 1,000,000 cumulative
JSON nodes, with per-row depth/node/token limits. Loaded rows carry provenance tied
to a canonical content digest; mutation or reordering drops back to logical row
locations rather than reporting stale physical lines. A split with zero GREEN
predictions now returns `status="no_green"` and `None` for both rate and upper bound:
it is explicitly no evidence, never a perfect `0.0` result.
