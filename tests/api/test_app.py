from __future__ import annotations

import asyncio
import hashlib
import json
import stat
import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from starlette.types import Message

import fretsure.api.app as api_module
from fretsure.api import API_VERSION, create_app
from fretsure.application import (
    ApplicationCode,
    ApplicationDiagnostic,
    ApplicationError,
    ArrangeOptions,
    CheckOptions,
    SolveOptions,
    arrange_outcome_to_wire,
    arrange_score_bytes,
    check_outcome_to_wire,
    check_tab_json,
    solve_target_json,
)
from fretsure.importers import IMPORTER_VERSION
from fretsure.llm.client import CONSTANT_LLM_MODEL_ID, ConstantLLM
from fretsure.tab import MAX_TAB_JSON_BYTES, tab_to_json

FIXTURES = Path(__file__).parents[1] / "fixtures" / "musicxml"
PRODUCER_FIXTURES = Path(__file__).parents[1] / "fixtures" / "producers"
BASIC = FIXTURES / "supported_basic.musicxml"
LONG = FIXTURES / "metamorphic_long.musicxml"
MUSESCORE_XML = PRODUCER_FIXTURES / "musescore-4.7.4.musicxml"
MUSESCORE_MXL = PRODUCER_FIXTURES / "musescore-4.7.4-roundtrip-supported_basic.mxl"
UNPROVIDED_KEY = "key-signature:fifths=0;mode=unprovided"
XML_MEDIA_TYPE = "application/vnd.recordare.musicxml+xml"
TEST_BASE_URL = "http://127.0.0.1"


class _NamedConstantLLM:
    def __init__(self, model_id: str, *, failure: str | None = None) -> None:
        self._model_id = model_id
        self._failure = failure

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        if self._failure is not None:
            raise RuntimeError(self._failure)
        return "noop"


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><div id="root">Fretsure UI</div>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("globalThis.FRETSURE_TEST = true;", encoding="utf-8")
    (assets / "index-abcdefgh.js").write_text(
        "globalThis.FRETSURE_HASHED_TEST = true;",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def client(static_root: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(static_root=static_root),
        base_url=TEST_BASE_URL,
        raise_server_exceptions=False,
    ) as value:
        yield value


def _problem(response: Response, status: int, code: str) -> dict[str, object]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["api_version"] == API_VERSION
    assert body["status"] == status
    assert body["code"] == code
    assert "traceback" not in response.text.lower()
    return cast(dict[str, object], body)


def _arrangement_request(client: TestClient) -> Response:
    return cast(
        Response,
        client.post(
            "/api/v1/arrangements",
            params={
                "filename": BASIC.name,
                "engine": "offline",
                "n": "1",
                "max_iters": "0",
                "use_critic": "false",
            },
            content=BASIC.read_bytes(),
            headers={"content-type": XML_MEDIA_TYPE},
        ),
    )


def _valid_tab_json() -> str:
    solved = solve_target_json(
        '{"notes":[{"onset":"0/1","duration":"1/1",'
        '"pitch":60,"voice":"melody"}]}',
        options=SolveOptions(),
    )
    assert solved.tab is not None
    return tab_to_json(solved.tab)


def _valid_mxl() -> bytes:
    container = (
        b'<?xml version="1.0"?><container><rootfiles>'
        b'<rootfile full-path="score.musicxml" '
        b'media-type="application/vnd.recordare.musicxml+xml"/>'
        b"</rootfiles></container>"
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        for name, data, compression in (
            ("mimetype", b"application/vnd.recordare.musicxml", zipfile.ZIP_STORED),
            ("META-INF/container.xml", container, zipfile.ZIP_DEFLATED),
            ("score.musicxml", BASIC.read_bytes(), zipfile.ZIP_DEFLATED),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    return output.getvalue()


def test_health_is_liveness_only_and_has_security_headers(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "api_version": API_VERSION,
        "status": "alive",
        "scope": "process_liveness_only",
    }
    assert "ready" not in response.text.lower()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "object-src 'none'" in response.headers["content-security-policy"]


def test_local_service_rejects_dns_rebinding_host_and_cross_origin_write(
    client: TestClient,
) -> None:
    rebound = client.get(
        "/healthz",
        headers={"host": "evil.example:8765", "origin": "http://evil.example:8765"},
    )
    _problem(rebound, 400, "UNTRUSTED_HOST")

    cross_origin = client.post(
        f"/api/v1/arrangements?filename={BASIC.name}",
        content=BASIC.read_bytes(),
        headers={
            "content-type": XML_MEDIA_TYPE,
            "origin": "http://evil.example",
        },
    )
    _problem(cross_origin, 403, "ORIGIN_REJECTED")


def test_same_loopback_origin_is_accepted(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/arrangements?filename={BASIC.name}&n=1&max_iters=0&use_critic=false",
        content=BASIC.read_bytes(),
        headers={
            "content-type": XML_MEDIA_TYPE,
            "origin": TEST_BASE_URL,
        },
    )
    assert response.status_code == 200, response.text


def test_capabilities_are_the_api_configuration_truth(client: TestClient) -> None:
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["api_version"] == API_VERSION
    assert body["service_version"] == "fretsure-service@0.1.0"
    assert body["trace_schema_version"] == "agent-trace@0.1.0"
    assert body["package_version"] == "0.4.0"
    assert body["stamps"]["importer_version"] == IMPORTER_VERSION == "musicxml@0.3.0"
    assert body["controls"]["arrange"]["n"] == {"min": 1, "max": 8}
    assert body["controls"]["arrange"]["max_iters"] == {"min": 0, "max": 16}
    assert body["controls"]["arrange"]["tempo_bpm"] == {
        "min": 1.0,
        "max": 1000.0,
        "nullable": True,
    }
    assert body["controls"]["arrange"]["defaults"]["engine"] == "offline"
    assert body["score_inputs"]["musicxml"]["max_body_bytes"] == 10 * 1024 * 1024
    assert body["score_inputs"]["mxl"]["max_body_bytes"] == 20 * 1024 * 1024
    assert body["engines"][0] == {
        "id": "offline",
        "available": True,
        "model_id": CONSTANT_LLM_MODEL_ID,
        "requires_startup_permission": False,
    }
    assert body["engines"][1]["available"] is False
    assert "render_audio" in body["deferred"]
    assert "render_audio" not in body["implemented"]
    assert body["http"]["multipart_uploads"] is False


def test_arrangement_endpoint_matches_application_service(client: TestClient) -> None:
    response = _arrangement_request(client)
    assert response.status_code == 200, response.text

    outcome = arrange_score_bytes(
        BASIC.read_bytes(),
        filename=BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    expected = {"api_version": API_VERSION, **arrange_outcome_to_wire(outcome)}
    model = expected["model"]
    assert isinstance(model, dict)
    model["engine"] = "offline"
    assert response.json() == expected
    assert response.json()["playability"] is not None
    assert response.json()["faithfulness"] is not None
    assert response.json()["source"]["importer_version"] == IMPORTER_VERSION
    assert response.json()["stamps"]["importer_version"] == IMPORTER_VERSION


def test_arrangement_response_is_byte_deterministic(client: TestClient) -> None:
    first = _arrangement_request(client)
    second = _arrangement_request(client)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content


@pytest.mark.parametrize(
    ("score", "media_type", "source_format", "warning_codes", "root_member"),
    [
        (
            MUSESCORE_XML,
            XML_MEDIA_TYPE,
            "musicxml",
            ["KEY_MODE_UNPROVIDED"],
            None,
        ),
        (
            MUSESCORE_MXL,
            "application/vnd.recordare.musicxml",
            "mxl",
            ["MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED", "KEY_MODE_UNPROVIDED"],
            "score.xml",
        ),
    ],
)
def test_frozen_musescore_raw_uploads_preserve_unknown_mode_and_provenance(
    client: TestClient,
    score: Path,
    media_type: str,
    source_format: str,
    warning_codes: list[str],
    root_member: str | None,
) -> None:
    def request() -> Response:
        return cast(
            Response,
            client.post(
                "/api/v1/arrangements",
                params={
                    "filename": score.name,
                    "engine": "offline",
                    "n": "1",
                    "max_iters": "0",
                    "use_critic": "false",
                },
                content=score.read_bytes(),
                headers={"content-type": media_type},
            ),
        )

    first = request()
    second = request()
    assert first.status_code == second.status_code == 200
    assert first.content == second.content

    body = first.json()
    raw = score.read_bytes()
    expected_root = raw
    if root_member is not None:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            expected_root = archive.read(root_member)
    assert body["score"]["key"] == UNPROVIDED_KEY
    assert body["score"]["key"] not in {"C", "C major", "Am", "A minor"}
    assert body["source"]["filename"] == score.name
    assert body["source"]["format"] == source_format
    assert body["source"]["root_member"] == root_member
    assert body["source"]["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert body["source"]["root_sha256"] == hashlib.sha256(expected_root).hexdigest()
    assert body["source"]["container_version"] == (
        "mxl-container@0.1.0" if source_format == "mxl" else None
    )
    if source_format == "mxl":
        assert body["source"]["raw_sha256"] != body["source"]["root_sha256"]
    assert [item["code"] for item in body["source"]["warnings"]] == warning_codes
    assert body["source"]["importer_version"] == IMPORTER_VERSION
    assert body["stamps"]["importer_version"] == IMPORTER_VERSION


def test_arrangement_endpoint_accepts_safe_mxl_bytes(client: TestClient) -> None:
    response = client.post(
        "/api/v1/arrangements?filename=score.mxl&n=1&max_iters=0&use_critic=false",
        content=_valid_mxl(),
        headers={"content-type": "application/vnd.recordare.musicxml"},
    )
    assert response.status_code == 200, response.text
    source = response.json()["source"]
    assert source["format"] == "mxl"
    assert source["root_member"] == "score.musicxml"
    assert source["container_version"] == "mxl-container@0.1.0"


def test_oracle_endpoint_matches_application_service(client: TestClient) -> None:
    payload = _valid_tab_json()
    response = client.post(
        "/api/v1/oracle/check?profile=median&tempo_bpm=90.0&beats_per_bar=4",
        content=payload,
        headers={"content-type": "application/json; charset=utf-8"},
    )
    expected = {
        "api_version": API_VERSION,
        **check_outcome_to_wire(check_tab_json(payload, options=CheckOptions())),
    }
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert response.json()["playability"]["meaning"] == (
        "versioned_model_relative_not_a_real_player_guarantee"
    )


@pytest.mark.parametrize(
    ("url", "content_type", "status", "code"),
    [
        (
            "/api/v1/arrangements?filename=score.mid",
            XML_MEDIA_TYPE,
            415,
            "UNSUPPORTED_SCORE_SUFFIX",
        ),
        (
            "/api/v1/arrangements?filename=score.mxl",
            XML_MEDIA_TYPE,
            415,
            "UNSUPPORTED_MEDIA_TYPE",
        ),
        (
            "/api/v1/arrangements?filename=score.xml",
            "multipart/form-data; boundary=x",
            415,
            "UNSUPPORTED_MEDIA_TYPE",
        ),
        (
            "/api/v1/oracle/check",
            "application/json; charset=latin-1",
            415,
            "UNSUPPORTED_MEDIA_TYPE_PARAMETER",
        ),
    ],
)
def test_suffix_and_mime_pairing_is_strict(
    client: TestClient,
    url: str,
    content_type: str,
    status: int,
    code: str,
) -> None:
    _problem(
        client.post(url, content=b"irrelevant", headers={"content-type": content_type}),
        status,
        code,
    )


def test_missing_content_type_is_415(client: TestClient) -> None:
    response = client.build_request(
        "POST",
        f"/api/v1/arrangements?filename={BASIC.name}",
        content=BASIC.read_bytes(),
    )
    response.headers.pop("content-type", None)
    _problem(client.send(response), 415, "CONTENT_TYPE_REQUIRED")


@pytest.mark.parametrize(
    "query",
    [
        "filename=score.xml&unknown=x",
        "filename=score.xml&filename=other.xml",
        "filename=score.xml&use_critic=1",
        "filename=score.xml&n=09",
        "filename=score.xml&n=9",
        "filename=score.xml&max_iters=17",
        "filename=score.xml&tempo_bpm=NaN",
        "filename=score.xml&engine=custom-model",
    ],
)
def test_invalid_query_controls_fail_before_body_semantics(
    client: TestClient, query: str
) -> None:
    response = client.post(
        f"/api/v1/arrangements?{query}",
        content=b"not XML",
        headers={"content-type": XML_MEDIA_TYPE},
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")


def test_huge_numeric_query_token_is_typed_without_integer_conversion(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/arrangements?filename=score.xml&n=" + "9" * 5_000,
        content=b"not XML",
        headers={"content-type": XML_MEDIA_TYPE},
    )
    _problem(response, 400, "QUERY_VALUE_OUT_OF_RANGE")


def test_hostile_filename_is_a_redacted_semantic_failure(client: TestClient) -> None:
    secret = "private/secret.xml"
    response = client.post(
        "/api/v1/arrangements?filename=private%2Fsecret.xml&n=1&max_iters=0",
        content=BASIC.read_bytes(),
        headers={"content-type": XML_MEDIA_TYPE},
    )
    body = _problem(response, 422, "INVALID_FILENAME")
    assert secret not in response.text
    assert "inert basename" in json.dumps(body)


def test_malformed_score_and_tab_are_422_not_checker_verdicts(client: TestClient) -> None:
    score = client.post(
        "/api/v1/arrangements?filename=broken.xml&n=1&max_iters=0",
        content=b"<score-partwise>",
        headers={"content-type": XML_MEDIA_TYPE},
    )
    score_body = _problem(score, 422, "IMPORT_REJECTED")
    assert "MALFORMED_XML" in json.dumps(score_body)
    assert "verdict" not in score.text

    tab = client.post(
        "/api/v1/oracle/check",
        content='{"tuning":[],"capo":0,"capo":1,"notes":[]}',
        headers={"content-type": "application/json"},
    )
    tab_body = _problem(tab, 422, "TAB_INPUT_REJECTED")
    assert "DUPLICATE_KEY" in json.dumps(tab_body)
    assert "verdict" not in tab.text


def test_non_utf8_tab_json_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/oracle/check",
        content=b"\xff",
        headers={"content-type": "application/json"},
    )
    _problem(response, 422, "BODY_NOT_UTF8")


def test_proxy_permission_is_frozen_and_factory_not_called(static_root: Path) -> None:
    calls = 0

    def proxy_factory() -> _NamedConstantLLM:
        nonlocal calls
        calls += 1
        return _NamedConstantLLM("test-proxy")

    app = create_app(
        allow_proxy=False,
        proxy_factory=proxy_factory,
        proxy_model_id="test-proxy",
        static_root=static_root,
    )
    with TestClient(app, base_url=TEST_BASE_URL, raise_server_exceptions=False) as local:
        response = local.post(
            f"/api/v1/arrangements?filename={BASIC.name}&engine=proxy",
            content=BASIC.read_bytes(),
            headers={"content-type": XML_MEDIA_TYPE},
        )
    _problem(response, 403, "PROXY_DISABLED")
    assert calls == 0


def test_unconfigured_proxy_is_not_advertised_or_allowed_before_body_read(
    static_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    app = create_app(allow_proxy=True, static_root=static_root)

    async def forbidden_body_read(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("unavailable proxy request reached body reader")

    monkeypatch.setattr(api_module, "read_bounded_body", forbidden_body_read)
    with TestClient(app, base_url=TEST_BASE_URL, raise_server_exceptions=False) as local:
        capabilities_response = local.get("/api/v1/capabilities")
        response = local.post(
            f"/api/v1/arrangements?filename={BASIC.name}&engine=proxy",
            content=BASIC.read_bytes(),
            headers={"content-type": XML_MEDIA_TYPE},
        )
    proxy = next(
        engine
        for engine in capabilities_response.json()["engines"]
        if engine["id"] == "proxy"
    )
    assert proxy["enabled"] is True
    assert proxy["available"] is False
    _problem(response, 503, "PROXY_UNAVAILABLE")


def test_hostile_filename_is_rejected_before_body_read(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_body_read(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("hostile filename reached body reader")

    monkeypatch.setattr(api_module, "read_bounded_body", forbidden_body_read)
    response = client.post(
        "/api/v1/arrangements?filename=private%2Fsecret.xml",
        content=BASIC.read_bytes(),
        headers={"content-type": XML_MEDIA_TYPE},
    )
    _problem(response, 422, "INVALID_FILENAME")


def test_enabled_proxy_uses_only_startup_factory(static_root: Path) -> None:
    app = create_app(
        allow_proxy=True,
        proxy_factory=lambda: _NamedConstantLLM("test-proxy"),
        proxy_model_id="test-proxy",
        static_root=static_root,
    )
    with TestClient(app, base_url=TEST_BASE_URL, raise_server_exceptions=False) as local:
        response = local.post(
            f"/api/v1/arrangements?filename={BASIC.name}&engine=proxy&n=1&max_iters=0"
            "&use_critic=false",
            content=BASIC.read_bytes(),
            headers={"content-type": XML_MEDIA_TYPE},
        )
    assert response.status_code == 200, response.text
    assert response.json()["model"] == {
        "model_id": "test-proxy",
        "engine": "proxy",
    }


def test_engine_initialization_and_transport_failures_are_redacted(
    static_root: Path,
) -> None:
    def broken_factory() -> NoReturn:
        raise RuntimeError("API_KEY=/private/engine-secret")

    init_app = create_app(
        allow_proxy=True,
        proxy_factory=broken_factory,
        static_root=static_root,
    )
    with TestClient(
        init_app, base_url=TEST_BASE_URL, raise_server_exceptions=False
    ) as local:
        init_response = local.post(
            f"/api/v1/arrangements?filename={BASIC.name}&engine=proxy",
            content=BASIC.read_bytes(),
            headers={"content-type": XML_MEDIA_TYPE},
        )
    _problem(init_response, 503, "LLM_CONFIGURATION_REJECTED")
    assert "engine-secret" not in init_response.text
    assert "RuntimeError" not in init_response.text

    transport_app = create_app(
        allow_proxy=True,
        proxy_factory=lambda: _NamedConstantLLM(
            "test-proxy",
            failure="Bearer secret-transport-token",
        ),
        proxy_model_id="test-proxy",
        static_root=static_root,
    )
    with TestClient(
        transport_app, base_url=TEST_BASE_URL, raise_server_exceptions=False
    ) as local:
        transport_response = local.post(
            f"/api/v1/arrangements?filename={BASIC.name}&engine=proxy&n=1&max_iters=0",
            content=BASIC.read_bytes(),
            headers={"content-type": XML_MEDIA_TYPE},
        )
    _problem(transport_response, 502, "ARRANGEMENT_FAILED")
    assert "secret-transport-token" not in transport_response.text
    assert "RuntimeError" not in transport_response.text


def test_import_rejection_never_initializes_engine_factory(static_root: Path) -> None:
    calls = 0

    def counted_factory() -> _NamedConstantLLM:
        nonlocal calls
        calls += 1
        return _NamedConstantLLM("test-proxy")

    app = create_app(
        allow_proxy=True,
        proxy_factory=counted_factory,
        proxy_model_id="test-proxy",
        static_root=static_root,
    )
    with TestClient(app, base_url=TEST_BASE_URL, raise_server_exceptions=False) as local:
        response = local.post(
            "/api/v1/arrangements?filename=broken.xml&engine=proxy&n=1&max_iters=0",
            content=b"<not-musicxml>",
            headers={"content-type": XML_MEDIA_TYPE},
        )
    _problem(response, 422, "IMPORT_REJECTED")
    assert calls == 0


def test_offline_constant_identity_keeps_large_score_direct_fallback(
    static_root: Path,
) -> None:
    completions = 0

    class ConstantIdentityBomb:
        @property
        def model_id(self) -> str:
            return CONSTANT_LLM_MODEL_ID

        def complete(
            self,
            *,
            system: str,
            user: str,
            max_tokens: int = 1024,
            temperature: float = 0.0,
        ) -> str:
            nonlocal completions
            del system, user, max_tokens, temperature
            completions += 1
            raise AssertionError("constant identity should take the direct fallback")

    app = create_app(
        offline_factory=ConstantIdentityBomb,
        static_root=static_root,
    )
    with TestClient(app, base_url=TEST_BASE_URL, raise_server_exceptions=False) as local:
        response = local.post(
            f"/api/v1/arrangements?filename={LONG.name}&n=1&max_iters=0"
            "&use_critic=false",
            content=LONG.read_bytes(),
            headers={"content-type": XML_MEDIA_TYPE},
        )
    assert response.status_code == 200, response.text
    assert response.json()["model"]["model_id"] == CONSTANT_LLM_MODEL_ID
    assert completions == 0


def test_unexpected_internal_exception_is_redacted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_check(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("/private/tmp/traceback SECRET_INTERNAL")

    monkeypatch.setattr(api_module, "check_tab_json", broken_check)
    response = client.post(
        "/api/v1/oracle/check",
        content=_valid_tab_json(),
        headers={"content-type": "application/json"},
    )
    _problem(response, 500, "INTERNAL_ERROR")
    assert "SECRET_INTERNAL" not in response.text
    assert "RuntimeError" not in response.text


def test_dependency_unavailable_maps_to_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> NoReturn:
        raise ApplicationError(
            ApplicationCode.DEPENDENCY_UNAVAILABLE,
            "score",
            "required score parser dependency is unavailable",
            (
                ApplicationDiagnostic(
                    "MISSING_DEPENDENCY",
                    "score",
                    "required score parser dependency is unavailable",
                ),
            ),
        )

    monkeypatch.setattr(api_module, "arrange_score_bytes", unavailable)
    response = _arrangement_request(client)
    _problem(response, 503, "DEPENDENCY_UNAVAILABLE")


def _asgi_response(
    app: FastAPI,
    *,
    path: str,
    query: bytes,
    headers: list[tuple[bytes, bytes]],
    chunks: list[bytes],
) -> tuple[int, bytes]:
    incoming = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    if not incoming:
        incoming.append({"type": "http.request", "body": b"", "more_body": False})
    outgoing: list[Message] = []

    async def receive() -> Message:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        outgoing.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": [(b"host", b"127.0.0.1"), *headers],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 80),
        "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    start = next(item for item in outgoing if item["type"] == "http.response.start")
    body = b"".join(
        cast(bytes, item.get("body", b""))
        for item in outgoing
        if item["type"] == "http.response.body"
    )
    return cast(int, start["status"]), body


def test_endpoint_rejects_declared_and_chunked_oversize(static_root: Path) -> None:
    app = create_app(static_root=static_root)
    common = [(b"content-type", b"application/json")]
    status, body = _asgi_response(
        app,
        path="/api/v1/oracle/check",
        query=b"",
        headers=[*common, (b"content-length", str(MAX_TAB_JSON_BYTES + 1).encode())],
        chunks=[],
    )
    assert status == 413
    assert json.loads(body)["code"] == "BODY_TOO_LARGE"

    status, body = _asgi_response(
        app,
        path="/api/v1/oracle/check",
        query=b"",
        headers=[*common, (b"transfer-encoding", b"chunked")],
        chunks=[b"x" * MAX_TAB_JSON_BYTES, b"x"],
    )
    assert status == 413
    assert json.loads(body)["code"] == "BODY_TOO_LARGE"


def test_openapi_documents_only_real_raw_body_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    assert "/api/v1/arrangements" in document["paths"]
    assert "/api/v1/oracle/check" in document["paths"]
    operation = document["paths"]["/api/v1/arrangements"]["post"]
    assert "multipart/form-data" not in operation["requestBody"]["content"]
    n_parameter = next(item for item in operation["parameters"] if item["name"] == "n")
    assert n_parameter["schema"]["maximum"] == 8
    arrangement_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert arrangement_schema["properties"]["status"]["enum"] == [
        "tab_produced",
        "no_fingering_within_budget",
    ]
    assert set(arrangement_schema["required"]) >= {
        "source",
        "playability",
        "faithfulness",
        "trace",
        "stamps",
    }
    check_schema = document["paths"]["/api/v1/oracle/check"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert check_schema["properties"]["status"]["const"] == "checked"
    assert "render_audio" not in response.text
    assert "/api/v1/render_audio" not in document["paths"]


def test_static_files_and_spa_fallback_do_not_swallow_reserved_paths(
    client: TestClient,
) -> None:
    root = client.get("/")
    route = client.get("/arrangements/latest")
    asset = client.get("/assets/app.js")
    hashed_asset = client.get("/assets/index-abcdefgh.js")
    assert root.status_code == route.status_code == asset.status_code == 200
    assert hashed_asset.status_code == 200
    assert root.content == route.content
    assert "Fretsure UI" in root.text
    assert "FRETSURE_TEST" in asset.text
    assert root.headers["cache-control"] == "no-store"
    assert asset.headers["cache-control"] == "no-cache"
    assert hashed_asset.headers["cache-control"] == (
        "public, max-age=31536000, immutable"
    )

    missing_asset = client.get("/assets/missing-abcdefgh.js")
    _problem(missing_asset, 404, "NOT_FOUND")
    assert missing_asset.headers["cache-control"] == "no-cache"
    _problem(client.get("/missing.css"), 404, "NOT_FOUND")
    _problem(client.get("/api/v1/not-real"), 404, "NOT_FOUND")
    _problem(client.get("/mcp/not-real"), 404, "NOT_FOUND")
    _problem(client.get("/openapi.json/not-real"), 404, "NOT_FOUND")
    _problem(client.get("/unknown.svg/route"), 404, "NOT_FOUND")


def test_packaged_web_build_serves_real_assets_examples_fonts_and_licenses() -> None:
    app = create_app()
    static_root = Path(api_module.__file__).resolve().parents[1] / "web_static"
    asset_names = {path.name for path in (static_root / "assets").iterdir()}
    javascript = next(name for name in asset_names if name.endswith(".js"))
    stylesheet = next(name for name in asset_names if name.endswith(".css"))
    font = next(name for name in asset_names if name.endswith(".woff2"))

    with TestClient(app, base_url=TEST_BASE_URL, raise_server_exceptions=False) as local:
        index = local.get("/")
        js = local.get(f"/assets/{javascript}")
        css = local.get(f"/assets/{stylesheet}")
        woff = local.get(f"/assets/{font}")
        example = local.get("/examples/fretsure-etude.musicxml")
        favicon = local.get("/favicon.svg")
        license_text = local.get("/licenses/OFL-1.1.txt")

    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert index.headers["cache-control"] == "no-store"
    for response, media_prefix in (
        (js, "text/javascript"),
        (css, "text/css"),
        (woff, "font/woff2"),
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(media_prefix)
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert example.headers["content-type"].startswith(
        "application/vnd.recordare.musicxml+xml"
    )
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert license_text.headers["content-type"].startswith("text/plain")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text.text


def test_missing_web_build_is_typed(tmp_path: Path) -> None:
    app = create_app(static_root=tmp_path / "absent")
    with TestClient(app, base_url=TEST_BASE_URL, raise_server_exceptions=False) as local:
        response = local.get("/")
    _problem(response, 404, "WEB_BUILD_UNAVAILABLE")
