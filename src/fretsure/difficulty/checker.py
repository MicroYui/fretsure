"""Verifiable difficulty checker.

``check_tier`` is the "verifiable difficulty simplification" gate nobody else
does: a tab meets a tier iff it is GREEN under that tier's tightened profile AND
satisfies the tier's non-geometric constraints. Reuses the Plan 1 oracle.
"""

from dataclasses import dataclass

from fretsure.difficulty.tiers import Tier, snapshot_tier, tier_violations
from fretsure.oracle.core import check_playability
from fretsure.oracle.diagnostics import Verdict
from fretsure.oracle.input import ensure_oracle_input
from fretsure.tab import Tab

DIFFICULTY_CHECKER_VERSION = "difficulty@0.1.0"


@dataclass(frozen=True)
class TierResult:
    meets: bool
    playable: Verdict
    tier_violations: tuple[str, ...]


def check_tier(
    tab: Tab,
    tier: Tier,
    *,
    tempo_bpm: float = 90.0,
    beats_per_bar: int = 4,
) -> TierResult:
    tier = snapshot_tier(tier)
    tab, profile, tempo_bpm, beats_per_bar = ensure_oracle_input(
        tab,
        tier.profile,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
    )
    tier = snapshot_tier(tier, profile=profile)
    oracle = check_playability(
        tab,
        profile,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
    )
    violations = tuple(tier_violations(tab, tier, beats_per_bar=beats_per_bar))
    meets = oracle.verdict == "GREEN" and not violations
    return TierResult(meets, oracle.verdict, violations)


__all__ = ["DIFFICULTY_CHECKER_VERSION", "TierResult", "check_tier"]
