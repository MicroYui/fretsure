from dataclasses import FrozenInstanceError

import pytest

import fretsure.bench.execution_provenance as provenance_module
from fretsure.bench.execution_provenance import (
    ExecutionBinding,
    ExecutionMode,
    ExecutionProvenance,
    ExecutionProvenanceError,
    declare_installed_wheel_replay,
    declare_live_execution,
)


def test_live_declaration_is_a_pure_path_free_receipt() -> None:
    sha = "1" * 40

    receipt = declare_live_execution(sha)

    assert receipt.public_snapshot() == {
        "mode": "live_collection",
        "execution_git_sha": sha,
        "binding": "external_git_gate",
    }
    assert not hasattr(provenance_module, "subprocess")


@pytest.mark.parametrize("value", [None, True, "", "A" * 40, "0" * 39, "0" * 41])
def test_live_declaration_rejects_noncanonical_sha(value: object) -> None:
    with pytest.raises(ExecutionProvenanceError) as caught:
        declare_live_execution(value)

    assert caught.value.field == "execution_git_sha"
    assert repr(value) not in str(caught.value)


def test_sha256_git_object_format_is_supported() -> None:
    assert declare_live_execution("2" * 64).execution_git_sha == "2" * 64


def test_receipt_is_frozen_and_validates_cross_field_state() -> None:
    receipt = declare_live_execution("3" * 40)
    with pytest.raises(FrozenInstanceError):
        receipt.mode = ExecutionMode.INSTALLED_WHEEL_REPLAY  # type: ignore[misc]

    with pytest.raises(ExecutionProvenanceError):
        ExecutionProvenance(
            ExecutionMode.LIVE_COLLECTION,
            "3" * 40,
            ExecutionBinding.INSTALLED_WHEEL_RECORD,
        )


def test_installed_wheel_replay_has_no_checkout_claim() -> None:
    receipt = declare_installed_wheel_replay()

    assert receipt.public_snapshot() == {
        "mode": "installed_wheel_replay",
        "execution_git_sha": None,
        "binding": "installed_wheel_record",
    }
