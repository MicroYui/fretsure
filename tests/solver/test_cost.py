from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.cost import config_base_cost, config_hand_center, transition_cost
from fretsure.solver.frames import FrameConfig, Placement

_LOW = FrameConfig((Placement(50, 2, 1, 1, "p"),))
_HIGH = FrameConfig((Placement(62, 2, 13, 1, "p"),))
_OPEN = FrameConfig((Placement(64, 5, 0, 0, "a"),))


def test_hand_center_fretted_is_a_number() -> None:
    assert config_hand_center(_LOW, 0, MEDIAN_HAND) is not None


def test_hand_center_open_is_none() -> None:
    assert config_hand_center(_OPEN, 0, MEDIAN_HAND) is None


def test_transition_same_position_zero() -> None:
    assert transition_cost(_LOW, _LOW, 0, MEDIAN_HAND) == 0.0


def test_transition_big_jump_positive() -> None:
    assert transition_cost(_LOW, _HIGH, 0, MEDIAN_HAND) > 0.0


def test_transition_involving_open_frame_is_zero() -> None:
    assert transition_cost(_OPEN, _LOW, 0, MEDIAN_HAND) == 0.0
    assert transition_cost(_LOW, _OPEN, 0, MEDIAN_HAND) == 0.0


def test_base_cost_low_cheaper_than_high() -> None:
    assert config_base_cost(_LOW) < config_base_cost(_HIGH)
