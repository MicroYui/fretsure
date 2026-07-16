# 3-Minute Demo Script

A tight, reproducible walkthrough for a screen recording or live demo. The default
path is fully offline, including the MusicXML import. A real-LLM benchmark or
`--llm` arrangement needs the local proxy and defaults to canonical
`gpt-5.6-sol`. Target length ≈ 3 minutes.

Setup (once, off-camera):

```bash
uv sync --extra dev --extra musicxml
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
- FAITHFULNESS — melody-F1 `1.00`, bass-root `1.00`, harmony `0.29`, FAIL,
  stamped `fidelity@0.2.0`.
- REPLAY — public `agent-trace@0.1.0` steps from plan/proposal through oracle and
  selection, with typed evidence rather than hidden chain-of-thought.

**Say:** "This path is offline and deterministic, but it is the real browser/API/
application pipeline—not a UI mock. A constant stub deliberately triggers the
rule-based proposal fallback. The proposal path does not decide feasibility—the
versioned oracle does. GREEN is certification inside the simplified model and
profile, while the independent fidelity failure remains visible."

## Beat 2 — A real file, and two genuinely independent gates (0:55–1:25)

**Do:**

```bash
uv run fretsure-arrange tests/fixtures/musicxml/supported_basic.musicxml \
  --n 1 --no-critic --trace-jsonl /tmp/fretsure-supported-basic.trace.jsonl
```

**Point at:** `musicxml@0.2.0`, the raw/root SHA-256 provenance, source and effective
tempo both 96 bpm, the rendered tab, and the JSONL trace location. Note that the same
entry point accepts a strict `.mxl` container and then prints its selected rootfile
member; the root MusicXML semantic allowlist is unchanged. Then linger on:

```text
ORACLE VERDICT
  GREEN ... checker oracle@0.2.0
FAITHFULNESS TO INPUT
  melody-F1 1.00   bass-root 1.00   harmony 0.29
  gate FAIL   checker fidelity@0.2.0
```

**Say:** "This is not a contradiction or a bug. GREEN answers only whether this
displayed fingering passes the versioned playability model. The independent fidelity
gate says the playable arrangement did not preserve enough harmony. Joint success
requires both GREEN and fidelity PASS. The file is a supported-subset regression
fixture; unmodified real-exporter fixture coverage is still an open input gate."

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

**Say:** "The final repository gate is 1494 offline tests plus 6 proxy-backed
integration tests, with ruff, strict mypy, lock and package smokes green. Invalid
public Tab/profile/solver/gold inputs now fail typed and resource-bounded; a
zero-GREEN split is explicitly `no_green` with no rate or bound. But the human gold
set has not been collected, so I still report no real-player false-accept bound.
Human work can proceed in parallel, but it gates real-world error rates, profile/tier
calibration, musicality evidence, and stronger human claims."

## Beat 4 — Benchmark evidence, with the metric boundary visible (2:00–2:45)

**Do:** show the warning and legacy tables at the top of
`docs/BENCHMARK_RESULTS.md`. Do not present them as current checker-pair numbers.
The 2026-07-10/11 runs used `oracle@0.1.0` and the old, unversioned note-onset
harmony metric; the consolidated historical snapshot is pinned at `bee8a1c`.

**Say:** "The historical two-seed snapshot gives useful directional evidence:
repair had the largest association, paired best-of-N showed a modest positive joint
delta, and the critic improved only its own score by about 0.01. But the fidelity
definition has since changed, so these are legacy numbers, not today's headline.
Benchmark v2 must rerun every arm under `fidelity@0.2.0`, retain paired item rows,
and run the appropriate paired tests."

If network behavior itself must be shown, pre-run or use:

```bash
uv run fretsure-bench --seed 1 --items 2 --paired
```

Explicitly label the result a stochastic smoke under the current metric, not a
reproduction of the old tables and not a new headline estimate.

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
  `--allow-proxy` Web server, the six integration tests, and a real-LLM benchmark
  need the proxy.
- No MusicXML dependency? Run `uv sync --extra musicxml` (or the setup command above).
- The default `fretsure-demo` is deterministic; changing `--seed N` or `--bars N`
  intentionally changes its tab and scores.
- Full gate before recording:

  ```bash
  uv run ruff check .
  uv run mypy src
  uv run pytest -q -m "not integration"  # 1494 passed, 6 deselected
  ```

- The six integration tests are deliberately excluded from that offline count;
  `uv run pytest --collect-only -q` currently collects 1500 tests total.
