# 3-Minute Demo Script

A tight, reproducible walkthrough for a screen recording or live demo. The default
path is fully offline, including the MusicXML and MIDI imports. A real-LLM benchmark or
`--llm` arrangement needs the local proxy and defaults to canonical
`gpt-5.6-sol`. The current package/router/importers are `0.6.0`,
`score-input@0.1.0`, and `musicxml@0.3.0` / `midi@0.1.0`, with
`music21==10.5.0` pinned exactly. Target length ≈ 3 minutes.

Setup (once, off-camera):

```bash
uv sync --extra dev --extra score
uv run fretsure-serve  # 另开终端并保持运行
# only for optional --llm / real-LLM benchmark segments:
export ANTHROPIC_BASE_URL=http://127.0.0.1:4141 ANTHROPIC_AUTH_TOKEN=<token>
```

---

## Beat 0 — The hook (0:00–0:20)

**Say:** "AI can generate a song in seconds. It cannot reliably tell you whether a
human can *play* it. Fretsure turns symbolic music into guitar tab, then a
deterministic checker certifies playability against a published model and profile —
note by note. Source faithfulness is a separate checker, and real-player calibration
is a separate, still-open measurement."

**Show:** the README first screen (positioning line + architecture diagram).

## Beat 1 — The deterministic product path (0:20–0:55)

**Do:**

Open `http://127.0.0.1:8000/`, click **Or load the CC0 example**, then
**Arrange and verify**.

**Point at the actual default output, in order:**

- INPUT — a real, bundled CC0 MusicXML lead sheet sent as a raw request body.
- ARRANGED TAB — a fingerstyle tab, high-e on top, with source SHA-256 provenance.
- ORACLE VERDICT — GREEN, stamped `oracle@0.2.0` / `tab-input@0.2.0` /
  `median@0.1` plus the canonical profile SHA-256.
- FAITHFULNESS — melody-F1 `1.00`, bass-root `1.00`, harmony `0.29`, REVIEW,
  stamped `fidelity@0.3.0`; every score is backed by source evidence.
- REPLAY — public `agent-trace@0.2.0` steps from plan/proposal through oracle and
  selection, with typed evidence rather than hidden chain-of-thought.

**Say:** "This path is offline and deterministic, but it is the real browser/API/
application pipeline—not a UI mock. A constant stub deliberately triggers the
rule-based proposal fallback. The proposal path does not decide feasibility—the
versioned oracle does. GREEN is certification inside the simplified model and
profile, while the independent fidelity failure remains visible."

## Beat 2 — Exact MIDI evidence without invented harmony (0:55–1:25)

**Do:**

```bash
uv run fretsure-arrange tests/fixtures/midi/producers/musescore-4.7.4-melody_only.mid \
  --n 1 --max-iters 0 --no-critic \
  --trace-jsonl /tmp/fretsure-midi.trace.jsonl
```

**Point at:** `score-input@0.1.0`, `midi@0.1.0`, exact raw/root SHA-256 equality,
the located loss warnings, and 96 bpm. Briefly open
`tests/fixtures/midi/producers/provenance.json`: the frozen row records exactly 7
beats and each MuseScore note released one PPQN tick early. Fretsure preserves that
performance instead of quantizing it back to notation. Then linger on:

```text
ORACLE VERDICT
  GREEN ... checker oracle@0.2.0
FAITHFULNESS TO INPUT
  melody-F1 1.00   bass-root N/A   harmony N/A
  available-dimension gate PASS (1/3 evaluated)
  checker fidelity@0.3.0
```

**Say:** "This is one exact, unedited MuseScore Studio 4.7.4 artifact, not a claim
about every MIDI file or producer version. Standard MIDI supplied a melody, not
authoritative bass or chord roles, so those fidelity dimensions are N/A—not 100% and
not inferred. The frozen music21 10.5.0 positive retains an 8-beat performance; the
two harmony-realized producer files are typed negatives. The importer keeps those
differences visible instead of choosing a melody or repairing timing."

## Beat 3 — Who checks the checker? (1:25–2:00)

**Say:** "The whole project gates on one question: *who checks the checker?*"

**Do (fast, let it scroll):**

```bash
uv run pytest -q tests/oracle tests/validation tests/test_geometry.py -m "not integration"
```

**Call out four self-checks (from `docs/PLAN1_ACCEPTANCE.md`):**

- **Monotone-in-resources** over 1000 random tabs — more hand never turns a
  GREEN into a worse verdict.
- **Mutation** kill-rate 1.0 — all `12/12` injected mutants are caught.
- **N-version** differential — fast fingering search agrees with exhaustive search
  on 500 random frames.
- Span is measured in **millimetres**, not fret count; the same fret span changes
  physical distance with neck position.

**Say:** "The acceptance record reports the exact final offline and proxy-backed
repository gates, with ruff, strict mypy, lock and package smokes. Invalid
public Tab/profile/solver/gold inputs now fail typed and resource-bounded; a
zero-GREEN split is explicitly `no_green` with no rate or bound. But the human gold
set has not been collected, so I still report no real-player false-accept bound.
Human work can proceed in parallel, but it gates real-world error rates, profile/tier
calibration, musicality evidence, and stronger human claims."

## Beat 4 — Benchmark evidence, with the metric boundary visible (2:00–2:45)

**Do:** show the benchmark v2 section at the top of `docs/BENCHMARK_RESULTS.md`, then
briefly scroll to the preserved legacy tables so the evidence boundary is visible.

**Say:** "The formal v2 run used 500 independent procedural families and three
separate public controls under `fidelity@0.3.0`. The controlled full policy reached
74/500, or 14.8%, and every high-complexity stratum plus all three public controls was
zero. Repair improved joint success by 5.66 points but missed the preregistered
10-point keep threshold, so it is NOT_KEPT. Best-of-4 gained 6.8 points but remains
cost-unknown probation because provider token totals are incomplete. The critic was
slightly negative on joint and still needs blind human evidence. Those negatives are
the point of preregistration: the benchmark decides the story, not the demo."

**Do not make a new provider call during the demo.** If replay provenance is asked,
show `docs/BENCHMARK_V2_ACCEPTANCE.md` and the public aggregate report/receipt. Explain
that byte-identical replay is bound to the recorded Darwin/arm64 runtime, the full
package is owner-controlled pending redistribution rights, and human empirical gates
remain OPEN.

## Beat 5 — What it means (2:45–3:00)

**Say:** "So: a policy proposes musical choices; a deterministic oracle gates
model-relative playability; an independent checker gates source faithfulness; and
the harness records what is evidence, what is legacy, and what remains open. The
moat is execution plus an auditable benchmark, not a hidden claim. That's Fretsure."

**Show:** the MusicXML output, then the `BENCHMARK_RESULTS.md` metric warning.

---

## Fallback / troubleshooting

- No proxy? `fretsure-demo`, MusicXML import/arrangement, checker tests, and the
  Web offline engine and stub benchmark work offline. Only `--llm`, an explicitly
  `--allow-proxy` Web server, the proxy integration tests, and a real-LLM benchmark
  need the proxy.
- No score dependency? Run `uv sync --extra score` (or the setup command above).
- The default `fretsure-demo` is deterministic; changing `--seed N` or `--bars N`
  intentionally changes its tab and scores.
- Full gate before recording:

  ```bash
  uv run ruff check .
  uv run mypy src
  uv run pytest -q -m "not integration"
  ```

- The proxy integration tests are deliberately excluded from that offline command;
  use `uv run pytest --collect-only -q` and
  [`MIDI_ACCEPTANCE.md`](MIDI_ACCEPTANCE.md) for the current exact closure counts
  rather than copying an older Plan 6A or producer number.
