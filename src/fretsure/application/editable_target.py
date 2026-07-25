"""Small versioned wire contract for the arrangement target used by revisions."""

from __future__ import annotations

import json
from typing import cast

from fretsure.application.target import target_from_json, target_to_json
from fretsure.ir import Note

EDITABLE_TARGET_SCHEMA_VERSION = "editable-arrangement-target@0.1.0"


def editable_target_to_wire(notes: tuple[Note, ...]) -> dict[str, object]:
    target = json.loads(target_to_json(notes))
    if type(target) is not dict or set(target) != {"notes"}:
        raise ValueError("canonical target serializer returned an invalid document")
    return {
        "schema_version": EDITABLE_TARGET_SCHEMA_VERSION,
        "notes": target["notes"],
    }


def editable_target_from_wire(value: object) -> tuple[Note, ...]:
    if type(value) is not dict:
        raise ValueError("editable target must be an object")
    document = cast(dict[str, object], value)
    if set(document) != {"schema_version", "notes"}:
        raise ValueError("editable target fields do not match its schema")
    if document["schema_version"] != EDITABLE_TARGET_SCHEMA_VERSION:
        raise ValueError("editable target schema version is unsupported")
    try:
        payload = json.dumps(
            {"notes": document["notes"]},
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, OverflowError):
        raise ValueError("editable target is not canonical JSON data") from None
    return target_from_json(payload)


__all__ = [
    "EDITABLE_TARGET_SCHEMA_VERSION",
    "editable_target_from_wire",
    "editable_target_to_wire",
]
