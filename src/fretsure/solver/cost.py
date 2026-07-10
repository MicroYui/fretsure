"""Cost model for the DP solver: hand position, transition, and comfort base."""

from fretsure.geometry import press_x
from fretsure.oracle.profiles import Profile
from fretsure.solver.frames import FrameConfig


def config_hand_center(config: FrameConfig, capo: int, profile: Profile) -> float | None:
    """Mean absolute press-x of the fretted placements (mm), or None if the frame
    is all-open (the hand is then free to sit anywhere)."""
    xs: list[float] = []
    for p in config.placements:
        if p.fret > 0:
            px = press_x(capo + p.fret, profile.string_length_mm)
            assert px is not None  # fret > 0 => fretted
            xs.append(px)
    if not xs:
        return None
    return sum(xs) / len(xs)


def config_base_cost(config: FrameConfig) -> float:
    """Comfort tiebreak: prefer lower positions and fewer fingers."""
    fret_sum = float(sum(p.fret for p in config.placements))
    fingers_used = len({p.left_finger for p in config.placements if p.left_finger > 0})
    return fret_sum + 2.0 * fingers_used


def transition_cost(
    prev: FrameConfig, curr: FrameConfig, capo: int, profile: Profile
) -> float:
    """Hand-centre displacement (mm) between two frames. An open-only frame on
    either side contributes 0 (the hand is unconstrained there)."""
    a = config_hand_center(prev, capo, profile)
    b = config_hand_center(curr, capo, profile)
    if a is None or b is None:
        return 0.0
    return abs(b - a)
