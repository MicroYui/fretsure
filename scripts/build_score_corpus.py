#!/usr/bin/env python3
"""Build a canonical score-supervision artifact from reviewed local sources."""

from __future__ import annotations

import argparse
from pathlib import Path

from fretsure.score_corpus import build_score_corpus_from_manifest, score_corpus_json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--split-seed", default="plan7b-v1")
    args = parser.parse_args()

    examples = build_score_corpus_from_manifest(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(score_corpus_json_bytes(examples, split_seed=args.split_seed))


if __name__ == "__main__":
    main()
