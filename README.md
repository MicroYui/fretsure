<div align="center">

<img src="docs/assets/readme/landing.png" alt="Fretsure — don't just generate it, make sure hands can reach it" width="820">

# Fretsure

**Don't just generate it. Make sure hands can reach it.**

A guitar-arrangement agent whose output has to answer to physics, not to another model.
An LLM proposes the arrangement; a deterministic playability oracle checks every note
against a versioned hand model and either certifies it or says exactly where it fails.

[![CI](https://github.com/MicroYui/fretsure/actions/workflows/ci.yml/badge.svg)](https://github.com/MicroYui/fretsure/actions/workflows/ci.yml)
[![Web CI](https://github.com/MicroYui/fretsure/actions/workflows/web.yml/badge.svg)](https://github.com/MicroYui/fretsure/actions/workflows/web.yml)
[![Docs CI](https://github.com/MicroYui/fretsure/actions/workflows/docs.yml/badge.svg)](https://github.com/MicroYui/fretsure/actions/workflows/docs.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)

**English** · [简体中文](README.zh-CN.md)

</div>

---

## The problem

Generative music tools produce guitar tablature that looks right and cannot be played:
two notes on one string at once, a stretch no hand makes, a position jump with no time
to travel. The model has no idea, because nothing ever told it.

Fretsure keeps the generative part — an LLM is genuinely better at *musical* choices —
and puts a deterministic verifier underneath it. The verifier is not a vibe check. It is
millimetre geometry over a fingerprinted hand profile, and it runs on every note.

## How it works

```
lead sheet / MusicXML / MIDI
        │
        ▼
   LLM proposes an arrangement            ← taste: which notes, which voicing
        │
        ▼
   deterministic beam fingering solver    ← where those notes go on the neck
        │
        ▼
   PLAYABILITY ORACLE  ── RED ────────────► located, typed diagnostics
   (mm geometry, active sustain,           (measure, beat, string, reason)
    reach/stretch, shift speed)
        │ GREEN / AMBER
        ▼
   independent FIDELITY gate               ← did it keep the melody, bass and harmony?
        │
        ▼
   canonical Tab  →  notation · playback · fretboard · GP7/GP5/MusicXML/MIDI/PDF/ASCII
```

The oracle is the environment; the LLM is the policy. Nothing downstream trusts a
proposal that has not passed the gate, and no gate ever asks a model whether the answer
looks good.

With a real LLM the loop is deliberately **baseline-first**: the source melody's onsets,
pitches and durations become a GREEN baseline as they are, and the model may only *add* —
bass, harmony, and fills that land in genuine silence. Every increment is checked, and a
failed one rolls back rather than overwriting the melody or spending the last GREEN
checkpoint.

<img src="docs/assets/readme/workspace.png" alt="The Fretsure workspace: notation, synced fretboard and the oracle verdict" width="100%">

## Quickstart

```bash
uv sync --extra dev
uv run fretsure-demo          # fully offline, deterministic
```

```
ARRANGED TAB (high-e on top)
  e|----5---5-------7-------0-------|
  B|--6---------6-8-----------------|
  G|5-----5---5-------7-7-5---5-5-5-|
  D|7---------------5-------7-------|
  A|--------8-----------------------|
  E|--------------------------------|

ORACLE VERDICT
  GREEN — passes the pessimistically tightened versioned model/profile (median@0.1)
  checker oracle@0.3.0, profile median@0.1
  profile SHA-256 fcefa5394cba876b94881fc77886e6db130d8be10406d46538ad6c83c40b7b62
  input schema tab-input@0.2.0

FAITHFULNESS TO INPUT
  melody-F1 1.00   bass-root 1.00   harmony 0.75
  available-dimension gate PASS (3/3 evaluated)
  checker fidelity@0.3.0
```

Arrange a real file, or open the local workspace:

```bash
uv run fretsure-arrange path/to/score.musicxml       # also .xml, .mxl, .mid
uv run fretsure-serve                                # http://127.0.0.1:8000
```

`fretsure-serve` ships the built web workspace and runs the deterministic engine by
default — no network, no key, no model call. Add `--allow-proxy` only when you want a
real LLM through a loopback proxy.

Install just the pieces you need:

```bash
pip install 'fretsure-oracle'                        # the oracle and solver alone
pip install 'fretsure-oracle[service,score,agent]'   # web workspace + importers + LLM
```

| extra | what it adds |
|---|---|
| `score` / `musicxml` / `midi` | symbolic import (music21 pinned to `10.5.0`) |
| `agent` | real LLM arrangement through a loopback proxy |
| `service` | HTTP API, bundled web workspace, exports |
| `exports` | Guitar Pro 5 and A4 vector PDF |
| `mcp` | `fretsure-mcp` stdio server |
| `benchmark` | `fretsure-bench` collection |

## What GREEN means — and what it does not

<img src="docs/assets/readme/evidence.png" alt="Oracle verdict card: GREEN, profile median@0.1, difficulty gate, fidelity meters" width="620">

GREEN means: **inside `oracle@0.3.0` under the `median@0.1` hand profile**, every note
survived a pessimistically tightened check of reach, stretch, string conflicts, barre
geometry and shift speed. The profile fingerprint travels with the verdict, so a claim
can always be traced to the model that made it.

GREEN does **not** mean "every guitarist can play this." The one human playthrough on
record is `PARTIAL`: an amateur player found the arrangement's overall difficulty low but
could not cleanly execute a `12-10-8-12` position sequence. Real-player false-accept
rate, profile calibration and musicality all remain open, and the docs say so wherever
the number appears.

Fidelity is a **separate** gate with its own honesty rule: a dimension with no source
evidence scores `None`, never `1.0`. A melody-only MIDI import is scored on melody and
reports bass-root and harmony as unavailable rather than perfect.

## Honest results

The benchmark scores with a checker, not an LLM judge. Every agent capability has to earn
its place through a preregistered ablation, and the ones that fail stay in the README.

**Benchmark v2** — 500 independently seeded procedural families plus 3 licensed public
controls, `gpt-5.6-sol`, 10,563 rows, 45,215 logical calls, two byte-identical replays:

| capability | effect on joint success | verdict |
|---|---|---|
| verifier-guided repair | **+0.0566** `[0.0456, 0.0682]`, 283 improved / 0 worsened | **`NOT_KEPT`** — real, but below the preregistered `0.10` SESOI |
| best-of-4 search | **+0.068** `[0.048, 0.088]`, McNemar 34/0 | **`PROBATION_COST_UNKNOWN`** — efficacy passes, provider token cost is `null` |
| critic | joint **−0.002**, self-score `+0.0027` | **`HUMAN_BLOCKED_PROBATION`** — no blind human evidence |

The headline number is not flattering and is not hidden: the selected `full` policy
reaches **74/500 = 14.8%** joint success (Wilson 95% `[11.96%, 18.18%]`). **All three
high-complexity strata scored zero. All three public controls scored zero.** That is a
material negative generalization result: 14.8% on procedural families is not evidence
that arbitrary real pieces work.

Because repair, search and critic did not clear their bars, the product defaults are
`n=1, max_iters=0, use_critic=false`. All three remain available as explicit opt-ins.

Supervision from published editions is treated the same way. A ranker trained on 21
Public Domain works improved exact fingering agreement on composer-held-out data from
29/60 to 30/60 (development) and 30/78 to 32/78 (test), with zero oracle regressions —
and a later expansion to 58 examples **did not** retain that gain, so it was not
promoted. Full numbers in
[`docs/BENCHMARK_RESULTS.md`](docs/BENCHMARK_RESULTS.md) and
[`docs/PLAN7B_ACCEPTANCE.md`](docs/PLAN7B_ACCEPTANCE.md).

## Who checks the checker

An oracle nobody audits is just an opinion with an API. The verifier has its own test
bench:

- **monotone in resources** across 1000 random tabs — a bigger hand never rejects what a
  smaller hand accepted;
- **mutation kill rate 1.0** — all 12 seeded oracle mutants are caught;
- **N-version differential agreement** on 500 random frames against an independent
  implementation;
- spans measured in **millimetres**, not fret counts, so tuning and scale length matter.

## Replay, not chain-of-thought

<img src="docs/assets/readme/trace.png" alt="Trace panel: what changed and why, with typed evidence per step" width="100%">

Every run emits a typed, replayable trace: what was proposed, what the solver returned,
what the oracle said, which candidate was selected. It is a record of decisions and
evidence — not a transcript of model reasoning dressed up as an explanation.

## Supported input — deliberately narrow

Fretsure would rather reject a file than guess about it. Anything outside the contract
fails closed with a typed, located diagnostic.

| input | contract |
|---|---|
| MusicXML 3.1/4.0 | single part/staff/voice lead sheet; uncompressed `.musicxml`/`.xml` and safe `.mxl`; plus one strict two-staff piano reduction that reports `PIANO_REDUCTION_DERIVED` |
| MIDI | format 0/1, PPQN, fixed tick-zero tempo and 4/4, one non-percussion monophonic note stream; raw ticks are authoritative and every note is melody |
| audio | not implemented, and not promised |

Version stamps ride on every result:

| contract | version |
|---|---|
| playability oracle | `oracle@0.3.0` |
| public tab input | `tab-input@0.2.0` |
| faithfulness | `fidelity@0.3.0` |
| importers | `musicxml@0.4.0`, `midi@0.1.0`, `score-input@0.1.0` |
| solvers | `fingering-solver@0.6.0`, `score-solver@0.4.0` |
| supervision | `published-fingering-ranker@0.1.0`, `published-grade-estimator@0.1.0` |
| service / API / Web / trace | `0.3.0` · MCP `0.2.0` |

## API and MCP

```bash
curl -s http://127.0.0.1:8000/api/v1/capabilities
curl -s --data-binary @score.musicxml \
  'http://127.0.0.1:8000/api/v1/arrangements?filename=score.musicxml'
```

Exports come from the same verified checkpoint the player and fretboard use:
`.gp` (Guitar Pro 7), `.gp5`, MusicXML TAB, MIDI, A4 PDF, WAV, ASCII and canonical Tab
JSON. The MCP server exposes `check_playability`, `check_difficulty`,
`feasible_fingerings`, `render_notation` and `render_audio` over stdio:

```bash
uv run fretsure-mcp
```

Details in [`docs/WEB_API_MCP.md`](docs/WEB_API_MCP.md).

<img src="docs/assets/readme/mobile.png" alt="The workspace on a 390px viewport" width="300">

## Status

Working: the oracle and solver, symbolic import, the agent loop, difficulty tiers and
simplification, accompaniment, the benchmark, the performance workspace with notation,
playback, synced fretboard, section regeneration, manual fingering edits and exports.

Open, and staying open until there is evidence: real-player gold labels, false-accept
rate in the world, profile and difficulty calibration against actual players,
cross-provider comparison, and public redistribution of the full replay package.

## Development

```bash
uv sync --extra dev
uv run ruff check && uv run mypy --strict src
uv run pytest -q -m 'not integration'      # 2,730 offline tests
cd web && npm ci && npm test && npm run build
```

`Full validation` (a manual GitHub workflow) additionally runs the integration boundary,
minimum-dependency compatibility, the distribution audit and clean-install smoke tests.

## Acknowledgements

Built on [music21](https://web.mit.edu/music21/), [alphaTab](https://alphatab.net/),
[FluidSynth](https://www.fluidsynth.org/), the [Mutopia Project](https://www.mutopiaproject.org/),
[OpenScore Lieder](https://github.com/OpenScore/Lieder) and
[GuitarSet](https://guitarset.weebly.com/). Licences and digests for every bundled asset
are in [`NOTICE`](NOTICE) and
[`src/fretsure/web_static/licenses/THIRD_PARTY_NOTICES.txt`](src/fretsure/web_static/licenses/THIRD_PARTY_NOTICES.txt).

## License

[Apache-2.0](LICENSE).
