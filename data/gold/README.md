# Oracle gold set (human-played playability labels)

Ground-truth labels for **validating the checker itself** (see `docs/SCOPE.md`
and roadmap §14 A.8). A guitarist with a measured hand span plays each tab and
records whether it is physically playable. This is the empirical anchor for the
GREEN false-accept rate.

## Schema — `*.jsonl` (one JSON object per line)

```json
{
  "tab": {
    "tuning": [40, 45, 50, 55, 59, 64],
    "capo": 0,
    "notes": [
      {"onset": "0/1", "duration": "1/1", "string": 0, "fret": 2,
       "left_finger": 1, "right_finger": "p"}
    ]
  },
  "human_playable": true,
  "note": "free-text provenance / why"
}
```

- `tab` is the exact JSON emitted by `fretsure.tab.tab_to_json`. `string` 0 =
  lowest-pitched (6th) string. `right_finger` ∈ {p, i, m, a}.
- `human_playable` is the label from a real player at the player's measured hand
  span (recorded per collection session, not per row here).

## Files

- `sample_labeled.jsonl` — a tiny hand-authored **sample** (not the real gold
  set): a few obviously-playable and obviously-impossible tabs to exercise the
  stats code. `green_unplayable` on this sample is 0 by construction.

## Collection protocol (the real gold set, built once, ~2–3h)

1. Draw ~300 tabs stratified by difficulty/texture, **including adversarial
   near-misses** (tabs just inside / just outside feasibility).
2. A guitarist with a **measured** hand span plays each and labels
   playable / unplayable.
3. Split **train / dev / test by tab**. Calibrate `d_max`, `v_shift`, etc. on
   train/dev only. **The test split is never used for calibration** — it is
   held out to report the honest false-accept rate.
4. Cohen's κ on the human labels also defines the AMBER band width (honest
   quantification that "playable" itself is fuzzy near the boundary).

**Do not** feed this set to any arranger/agent — it exists only to validate the
oracle. Using human-played tabs as agent input would leak playability.
