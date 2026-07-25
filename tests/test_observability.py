from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import fretsure.application.service as service_module
from fretsure.application import ArrangeOptions, arrange_score_bytes
from fretsure.importers import ImportSuccess, import_musicxml_bytes
from fretsure.llm.client import FakeLLM
from fretsure.observability import ProductTelemetry

_BASIC = Path("tests/fixtures/musicxml/supported_basic.musicxml")


class _RecordingTracer:
    def __init__(self) -> None:
        self.started: list[tuple[str, dict[str, object]]] = []

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: dict[str, object],
    ) -> Iterator[object]:
        self.started.append((name, dict(attributes)))
        yield object()


def test_application_and_model_calls_share_one_opentelemetry_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = import_musicxml_bytes(_BASIC.read_bytes(), _BASIC.name)
    assert isinstance(imported, ImportSuccess)
    reply = json.dumps(
        {
            "notes": [
                {
                    "onset": str(note.onset),
                    "duration": str(note.duration),
                    "pitch": note.pitch,
                    "voice": note.voice,
                }
                for note in imported.ir.notes
            ]
        },
        separators=(",", ":"),
    )
    tracer = _RecordingTracer()
    telemetry = ProductTelemetry(tracer)
    monkeypatch.setattr(service_module, "product_telemetry", lambda: telemetry)

    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=FakeLLM([reply]),
    )

    assert outcome.status == "tab_produced"
    assert [name for name, _ in tracer.started] == [
        "fretsure.arrangement",
        "fretsure.model_call",
    ]
    arrangement_attributes = tracer.started[0][1]
    assert arrangement_attributes["fretsure.arrangement.candidate_count"] == 1
    assert arrangement_attributes["fretsure.arrangement.critic_enabled"] is False
    model_attributes = tracer.started[1][1]
    assert model_attributes == {
        "fretsure.model.stage": "proposal",
        "fretsure.model.candidate_index": 0,
        "fretsure.model.stage_ordinal": 0,
    }
