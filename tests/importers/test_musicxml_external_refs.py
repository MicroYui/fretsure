from __future__ import annotations

from pathlib import Path

import pytest

import fretsure.importers.musicxml as musicxml_module
from fretsure.importers import ImportCode, ImportFailure, ImportSuccess, import_musicxml

FIXTURES = Path(__file__).parents[1] / "fixtures" / "musicxml"
BASIC = FIXTURES / "supported_basic.musicxml"
XLINK = "http://www.w3.org/1999/xlink"


def _write_case(tmp_path: Path, xml: str, name: str) -> Path:
    path = tmp_path / f"{name}.musicxml"
    path.write_text(xml, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "needle", "replacement", "expected_element", "expected_part", "expected_measure"),
    [
        (
            "credit_image",
            "  <part-list>",
            (
                '  <credit page="1"><credit-image source="file:///etc/passwd" '
                'type="image/png"/></credit>\n  <part-list>'
            ),
            "credit-image",
            None,
            None,
        ),
        (
            "opus",
            "</work>",
            f'<opus xmlns:xlink="{XLINK}" xlink:href="https://example.invalid/opus.xml"/>'
            "</work>",
            "opus",
            None,
            None,
        ),
        (
            "measure_link",
            '    <measure number="1">',
            (
                f'    <measure number="1"><link xmlns:xlink="{XLINK}" '
                'xlink:href="file:///etc/passwd"/>'
            ),
            "link",
            "P1",
            "1",
        ),
        (
            "metadata_xlink_href",
            "  <identification>",
            (
                f'  <identification><source xmlns:xlink="{XLINK}" '
                'xlink:href="https://example.invalid/catalog">Catalog</source>'
            ),
            "source",
            None,
            None,
        ),
    ],
)
def test_external_resource_references_fail_before_music21_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    needle: str,
    replacement: str,
    expected_element: str,
    expected_part: str | None,
    expected_measure: str | None,
) -> None:
    raw = BASIC.read_text(encoding="utf-8")
    assert needle in raw
    path = _write_case(tmp_path, raw.replace(needle, replacement, 1), name)

    def adapter_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external resource reference reached music21 adapter")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_must_not_run)
    result = import_musicxml(path)

    assert isinstance(result, ImportFailure)
    unsafe = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code is ImportCode.UNSAFE_XML
    ]
    assert len(unsafe) == 1
    location = unsafe[0].location
    assert location is not None
    assert location.element == expected_element
    assert location.part_id == expected_part
    assert location.measure == expected_measure


def test_plain_source_metadata_text_is_not_treated_as_external_reference(tmp_path: Path) -> None:
    raw = BASIC.read_text(encoding="utf-8")
    xml = raw.replace(
        "  <identification>",
        "  <identification><source>https://example.invalid/catalog</source>",
        1,
    )

    assert isinstance(import_musicxml(_write_case(tmp_path, xml, "plain_source")), ImportSuccess)
