from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.oracle.validation.mutation import MUTANTS, kill_rate, run_mutation_suite


def test_kill_rate_meets_gate() -> None:
    r = run_mutation_suite()
    assert r.total >= 10
    assert kill_rate(r) >= 0.9, f"survived: {r.survived}"


def test_no_mutant_survives() -> None:
    assert run_mutation_suite().survived == ()


def test_real_predicates_flag_their_triggers() -> None:
    # sanity: every trigger tab really does violate its target predicate
    for name, real_fn, _mutant, triggers in MUTANTS:
        for tab in triggers:
            assert real_fn(tab, MEDIAN_HAND), name


def test_kill_rate_bounds() -> None:
    r = run_mutation_suite()
    assert 0.0 <= kill_rate(r) <= 1.0
    assert r.killed <= r.total
