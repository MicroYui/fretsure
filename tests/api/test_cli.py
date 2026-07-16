from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

import fretsure.api.cli as cli_module
from fretsure.api import create_app
from fretsure.cli import main as arrange_cli_main


def test_server_cli_is_fixed_to_loopback_and_forwards_safe_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[bool] = []
    runs: list[tuple[object, dict[str, object]]] = []
    sentinel = FastAPI()

    def fake_create_app(*, allow_proxy: bool) -> FastAPI:
        created.append(allow_proxy)
        return sentinel

    def fake_run(app: object, **kwargs: object) -> None:
        runs.append((app, kwargs))

    monkeypatch.setattr(cli_module, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    assert cli_module.main(["--port", "9876", "--allow-proxy", "--log-level", "warning"]) == 0
    assert created == [True]
    assert runs == [
        (
            sentinel,
            {
                "host": "127.0.0.1",
                "port": 9876,
                "log_level": "warning",
                "proxy_headers": False,
                "server_header": False,
                "date_header": False,
            },
        )
    ]


def test_server_cli_defaults_are_offline_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def fake_create_app(*, allow_proxy: bool) -> FastAPI:
        observed["allow_proxy"] = allow_proxy
        return FastAPI()

    def fake_run(_app: object, **kwargs: object) -> None:
        observed.update(kwargs)

    monkeypatch.setattr(cli_module, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert cli_module.main([]) == 0
    assert observed["allow_proxy"] is False
    assert observed["host"] == cli_module.LOOPBACK_HOST == "127.0.0.1"
    assert observed["port"] == cli_module.DEFAULT_PORT == 8000


@pytest.mark.parametrize("argv", [["--port", "0"], ["--port", "65536"], ["--host", "0.0.0.0"]])
def test_server_cli_rejects_invalid_port_and_non_loopback_host(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli_module.main(argv)
    assert caught.value.code == 2


def test_http_and_file_cli_report_the_same_arrangement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # ``tmp_path`` is intentionally unused by the file CLI; it supplies an
    # absent static root so this parity check does not depend on a frontend build.
    static_root = tmp_path / "no-web-build"
    score = Path("tests/fixtures/musicxml/supported_basic.musicxml")
    app = create_app(static_root=static_root)
    with TestClient(
        app, base_url="http://127.0.0.1", raise_server_exceptions=False
    ) as client:
        response = client.post(
            f"/api/v1/arrangements?filename={score.name}&n=1&max_iters=0&use_critic=false",
            content=score.read_bytes(),
            headers={"content-type": "application/vnd.recordare.musicxml+xml"},
        )
    assert response.status_code == 200, response.text
    assert arrange_cli_main(
        [str(score), "--n", "1", "--max-iters", "0", "--no-critic"]
    ) == 0
    output = capsys.readouterr().out
    wire = response.json()
    assert f"SOURCE SHA-256    : {wire['source']['raw_sha256']}" in output
    assert f"  {wire['playability']['verdict']}" in output
    assert "ConstantLLM (constant-stub" in output
    for line in wire["ascii"].splitlines():
        assert f"  {line}" in output
