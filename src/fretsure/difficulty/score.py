"""Measured-tier scorer: the easiest tier a tab qualifies for.

Feeds the benchmark's difficulty-accuracy metric (does simplification actually
hit the requested tier?). Returns the lowest tier whose ``check_tier`` the tab
passes, or "above_advanced" if it passes none.
"""

from fretsure.difficulty.checker import check_tier
from fretsure.difficulty.tiers import ADVANCED, BEGINNER, INTERMEDIATE
from fretsure.tab import Tab


def measured_tier(tab: Tab, *, tempo_bpm: float = 90.0) -> str:
    for tier in (BEGINNER, INTERMEDIATE, ADVANCED):
        if check_tier(tab, tier, tempo_bpm=tempo_bpm).meets:
            return tier.name
    return "above_advanced"
