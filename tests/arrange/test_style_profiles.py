from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path

import pytest

from fretsure.arrange.style_profiles import (
    STYLE_PROFILE_SHA256,
    STYLE_PROFILE_VERSION,
    corpus_style_harmony_offsets,
    style_rhythm_profile,
)

ROOT = Path(__file__).parents[2]
MODEL = (
    ROOT
    / "src"
    / "fretsure"
    / "arrange"
    / "models"
    / "guitarset-style-profiles-v0.1.0.json"
)


def test_frozen_style_profile_has_attributed_grouped_evidence() -> None:
    raw = MODEL.read_bytes()
    document = json.loads(raw)

    assert hashlib.sha256(raw).hexdigest() == STYLE_PROFILE_SHA256
    assert document["profile_version"] == STYLE_PROFILE_VERSION
    assert document["source"]["license"] == "CC-BY-4.0"
    assert document["source"]["member_count"] == 360
    assert document["split_policy"] == {
        "train": ["00", "01", "02", "03"],
        "dev": ["04"],
        "test": ["05"],
    }
    assert document["profiles"]["jazz"]["source_documents"] == {
        "train": 24,
        "dev": 6,
        "test": 6,
    }
    assert document["profiles"]["jazz"]["evidence_role"] == (
        "direct-jazz-performance"
    )
    assert document["profiles"]["rnb"]["evidence_role"] == (
        "adjacent-funk-proxy-not-rnb-supervision"
    )


def test_runtime_profiles_preserve_direct_vs_proxy_boundary() -> None:
    jazz = style_rhythm_profile("jazz")
    rnb = style_rhythm_profile("rnb")

    assert jazz.source_family == "Jazz"
    assert jazz.intermediate_phases == (Fraction(11, 4), Fraction(15, 4))
    assert jazz.answer_duration == Fraction(3, 4)
    assert rnb.source_family == "Funk"
    assert "not-rnb-supervision" in rnb.evidence_role
    assert rnb.intermediate_phases == (Fraction(1, 2), Fraction(3, 2))
    assert rnb.answer_duration == Fraction(1, 4)
    with pytest.raises(ValueError, match="only for jazz and rnb"):
        style_rhythm_profile("classical")


def test_difficulty_adds_only_the_third_corpus_phase() -> None:
    assert corpus_style_harmony_offsets("jazz", "beginner") == ()
    intermediate = corpus_style_harmony_offsets("jazz", "intermediate")
    advanced = corpus_style_harmony_offsets("jazz", "advanced")

    assert advanced[:2] == intermediate
    assert advanced[2] == (Fraction(7, 4), Fraction(3, 4))
