# 3-Minute Demo Script

A tight, reproducible walkthrough for a screen recording or live demo. Everything
here runs from a clean checkout; the only non-offline step is the real-LLM
benchmark, which needs the local proxy. Target length ≈ 3 minutes.

Setup (once, off-camera):
```bash
uv sync --extra dev
# for the --llm / bench segments only:
export ANTHROPIC_BASE_URL=http://localhost:4141 ANTHROPIC_AUTH_TOKEN=<token>
```

---

## Beat 0 — The hook (0:00–0:20)

**Say:** "AI can generate a song in seconds. It cannot tell you whether a human
can *play* it. Suno hands you audio; Fretsure hands you a guitar tab that a
deterministic checker has *proven* your hands can reach — note by note."

**Show:** the README first screen (positioning line + architecture diagram).

## Beat 1 — One command, the product runs (0:20–1:00)

**Do:**
```bash
uv run fretsure-demo
```

**Point at the output, in order:**
- INPUT — a lead sheet (melody + chord symbols).
- ARRANGED TAB — a real fingerstyle tab, high-e on top.
- ORACLE VERDICT — **GREEN**, with the checker + profile version stamped.
- FAITHFULNESS — melody-F1 / bass-root / harmony, gate PASS.

**Say:** "This is offline and deterministic — no API key. The LLM proposed the
musical intent; a millimetre-geometry oracle checked every fret against a
conservatively-tightened hand and signed off. GREEN means *provably playable*,
not *the AI thinks so*."

## Beat 2 — Why you can trust the GREEN (1:00–1:45)

**Say:** "The whole project gates on one question: *who checks the checker?*"

**Do (fast, let it scroll):**
```bash
uv run pytest -q tests/oracle -m "not integration"
```

**Call out three self-checks (from `docs/PLAN1_ACCEPTANCE.md`):**
- **Monotone-in-resources** property over 1000 random tabs — more hand never
  turns a GREEN into a RED.
- **Mutation** kill-rate 1.0 — deliberately broken oracles are all caught.
- **N-version** differential — the fast fingering solver matches an exhaustive
  brute force on 500 random frames.
- Span is in **millimetres**, not fret count — the same 3-fret stretch is harder
  low on the neck; a test proves the verdict changes with position.

**Say:** "It's not a vibe. It's a checker with an audited false-accept bound."

## Beat 3 — The benchmark, and the honesty (1:45–2:40)

**Do:**
```bash
uv run fretsure-bench --seed 1 --items 16
```
(or show the saved `docs/BENCHMARK_RESULTS.md` table if you don't want to wait on
the live run).

**Say, pointing at the ablation table:**
- "Every capability has to *earn its existence* by leave-one-out ablation, scored
  by the checker — not an LLM judge."
- "**Repair earns it, hard:** remove the verifier-guided repair loop and success
  falls 0.81 → 0.31, melody-F1 1.00 → 0.56. The Wilson intervals don't overlap."
- "**And here's the part most demos hide:** the critic and best-of-N *don't* earn
  it on this corpus. I report that too. Components that don't pay their way are
  on notice, in public."

**Optional 10-second kicker:** "One `joint_success = 0` run turned out to be a bug
in my *corpus labels*, not the agent — same 'who checks the checker' discipline,
turned on the test set. It's in the results doc."

## Beat 4 — What it means (2:40–3:00)

**Say:** "So: a front-tier LLM for musical taste, a deterministic oracle for
ground truth, a self-research harness that makes every part prove it belongs.
The moat isn't a secret algorithm — it's execution and a benchmark I can't
fool myself with. That's Fretsure."

**Show:** `docs/BENCHMARK_RESULTS.md` headline + the repo.

---

## Fallback / troubleshooting

- No proxy? Everything except Beat 3's live run and `--llm` works offline. Show
  the saved `docs/BENCHMARK_RESULTS.md` table for the numbers.
- `fretsure-demo` output varies with `--seed N` / `--bars N` if you want a
  different tab on camera.
- Full gate for confidence before recording:
  `uv run ruff check && uv run mypy src && uv run pytest -q -m "not integration"`.
