#!/usr/bin/env python3
"""Clean-install smoke matrix for the current release wheel and optional extras."""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import tomllib
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(
    *arguments: str,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True)  # noqa: S603


def _install_environment(
    root: Path,
    name: str,
    wheel: Path,
    extras: str,
) -> tuple[Path, Path]:
    work = root / f"{name}-work"
    work.mkdir()
    environment = root / f"{name}-venv"
    _run("uv", "venv", str(environment), "--python", "3.11", "--quiet", cwd=work)
    python = environment / "bin" / "python"
    requirement = f"{wheel}[{extras}]" if extras else str(wheel)
    _run(
        "uv",
        "pip",
        "install",
        "--quiet",
        "--python",
        str(python),
        requirement,
        cwd=work,
    )
    return python, work


def _run_python(
    python: Path,
    work: Path,
    code: str,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    _run(str(python), "-I", "-c", textwrap.dedent(code), cwd=work, env=env)


def _environment(
    root: Path,
    name: str,
    wheel: Path,
    extras: str,
    code: str,
) -> None:
    python, work = _install_environment(root, name, wheel, extras)
    _run_python(python, work, code)


def _core_benchmark_smoke(root: Path, wheel: Path, version: str) -> None:
    python, work = _install_environment(root, "core", wheel, "")
    _run_python(
        python,
        work,
        f"""
        import importlib.util
        import sys
        from pathlib import Path

        import fretsure
        from fretsure.bench.runner import (
            BenchmarkV2Config,
            collect_benchmark_v2,
            replay_benchmark_v2,
        )

        here = Path.cwd().resolve()
        assert not any((parent / ".git").exists() for parent in (here, *here.parents))
        assert Path(fretsure.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
        assert fretsure.__version__ == {version!r}
        for dependency in (
            "anthropic",
            "defusedxml",
            "fastapi",
            "httpx",
            "mcp",
            "music21",
            "uvicorn",
        ):
            assert importlib.util.find_spec(dependency) is None, dependency

        source = here / "collection"
        replay = here / "replay"
        collected = collect_benchmark_v2(
            config=BenchmarkV2Config(
                family_count=1,
                bars=1,
                bootstrap_repetitions=11,
                sign_flip_draws=11,
                stub=True,
                run_id="clean-install-core",
            ),
            output_dir=source,
        )
        replayed = replay_benchmark_v2(
            config_path=source / "canonical" / "config.json",
            receipt_path=source / "canonical" / "receipt.json",
            rows_path=source / "canonical" / "rows.jsonl",
            blobs_path=source / "canonical" / "blobs.jsonl",
            observations_path=source / "canonical" / "observations.json",
            output_dir=replay,
        )
        assert replayed.report == collected.report
        for name in ("report.json", "report.md"):
            assert (replay / "canonical" / name).read_bytes() == (
                source / "canonical" / name
            ).read_bytes()
        """,
    )


def _benchmark_extra_smoke(root: Path, wheel: Path) -> None:
    python, work = _install_environment(root, "benchmark", wheel, "benchmark")
    environment = dict(os.environ)
    environment.pop("ANTHROPIC_AUTH_TOKEN", None)
    environment.pop("ANTHROPIC_BASE_URL", None)
    _run_python(
        python,
        work,
        """
        import importlib.util
        from importlib.resources import files
        from pathlib import Path

        import anthropic
        import defusedxml
        import httpx
        import music21
        from fretsure.bench import runner
        from fretsure.bench.precall import (
            PreCallConfigError,
            build_pre_call_config,
            current_runtime_identity,
        )
        from fretsure.bench.preregistration import preregistration_from_bytes
        from fretsure.bench.public_adapters import arrangement_source_from_pinned_bytes

        del anthropic, defusedxml, httpx
        assert music21.__version__ == "10.5.0"
        package_data = files("fretsure.bench").joinpath("data")
        assert package_data.joinpath("source-census.json").is_file()
        preregistration = preregistration_from_bytes(
            package_data.joinpath("benchmark-v2-prereg.json").read_bytes()
        )
        frozen = runner.build_benchmark_v2_preregistered_context(preregistration)
        assert len(frozen.plan.items) == 503
        assert len(frozen.plan.collection_schedule) == 10_060
        assert len(frozen.manifest.expected_rows) == 10_563
        assert {
            item.item_id for item in frozen.plan.items if item.layer != "procedural"
        } == {
            "public-classical-beethoven-op48-5",
            "public-midi-bwv774",
            "public-midi-bwv775",
        }
        midi = package_data.joinpath(
            "sources", "mutopia-bach-bwv774.mid"
        ).read_bytes()
        adapted = arrangement_source_from_pinned_bytes(
            midi,
            source_format="midi",
            source_identity="clean-install-mutopia-bwv774",
            license_expression="CC-PDDC",
        )
        assert len(adapted.streams) == 2

        stub_output = Path.cwd() / "benchmark-stub"
        runner.collect_benchmark_v2(
            config=runner.BenchmarkV2Config(
                family_count=1,
                bars=1,
                bootstrap_repetitions=7,
                sign_flip_draws=7,
                stub=True,
                run_id="clean-install-benchmark",
            ),
            output_dir=stub_output,
        )
        assert (stub_output / "canonical" / "report.json").is_file()

        live_output = Path.cwd() / "live-must-not-exist"
        try:
            runner.collect_benchmark_v2(
                config=runner.BenchmarkV2Config(
                    family_count=1,
                    bars=1,
                    bootstrap_repetitions=7,
                    sign_flip_draws=7,
                    stub=False,
                    run_id="clean-install-live-fail-closed",
                ),
                output_dir=live_output,
            )
        except runner.BenchmarkInputError as error:
            assert error.field == "pre_call_config"
        else:
            raise AssertionError("live collection accepted missing pre-call configuration")
        assert not live_output.exists()

        unpriced_output = Path.cwd() / "unpriced-live-must-not-exist"
        unpriced = build_pre_call_config(
            preregistration,
            collection_attempt=1,
            execution_git_sha="0" * 40,
            uv_lock_sha256="0" * 64,
            analysis_binding_kind="wheel_record_sha256",
            analysis_code_sha256="0" * 64,
            runtime_identity=current_runtime_identity(),
        )
        try:
            runner.collect_benchmark_v2(
                pre_call_config=unpriced,
                output_dir=unpriced_output,
            )
        except PreCallConfigError as error:
            assert error.field == "budget.cost"
        else:
            raise AssertionError("live collection accepted an unpriced pre-call declaration")
        assert not unpriced_output.exists()
        assert importlib.util.find_spec("anthropic") is not None
        assert importlib.util.find_spec("defusedxml") is not None
        assert importlib.util.find_spec("httpx") is not None
        assert importlib.util.find_spec("music21") is not None
        """,
        env=environment,
    )


def main() -> int:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    wheels = list((ROOT / "dist").glob(f"fretsure_oracle-{version}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("build exactly one current wheel before running smoke tests")
    wheel = wheels[0].resolve()
    producer_xml = ROOT / "tests" / "fixtures" / "producers" / "musescore-4.7.4.musicxml"
    producer_midi = (
        ROOT
        / "tests"
        / "fixtures"
        / "midi"
        / "producers"
        / "music21-10.5.0-melody_only.mid"
    )
    with tempfile.TemporaryDirectory(prefix="fretsure-wheel-smoke-") as temporary:
        root = Path(temporary)
        _core_benchmark_smoke(root, wheel, version)
        _benchmark_extra_smoke(root, wheel)
        _environment(
            root,
            "musicxml",
            wheel,
            "musicxml",
            f"""
            from pathlib import Path
            from fretsure.importers import IMPORTER_VERSION, ImportSuccess, import_musicxml

            score = Path({str(producer_xml)!r})
            result = import_musicxml(score)
            assert isinstance(result, ImportSuccess)
            assert result.importer_version == IMPORTER_VERSION
            assert result.ir.meta.key == "key-signature:fifths=0;mode=unprovided"
            """,
        )
        _environment(
            root,
            "service",
            wheel,
            "service,score,agent",
            f"""
            from pathlib import Path
            from fretsure.application import (
                ArrangeOptions,
                arrange_outcome_to_wire,
                arrange_score_bytes,
            )
            from fretsure.llm.client import ConstantLLM

            score = Path({str(producer_midi)!r})
            outcome = arrange_score_bytes(
                score.read_bytes(),
                filename=score.name,
                options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
                llm=ConstantLLM("noop"),
            )
            wire = arrange_outcome_to_wire(outcome)
            assert outcome.status == "tab_produced"
            assert wire["source"]["importer_version"] == "midi@0.1.0"
            assert wire["stamps"]["score_input_version"] == "score-input@0.1.0"
            assert wire["stamps"]["importer_version"] == "midi@0.1.0"
            assert wire["faithfulness"]["bass_root_accuracy"] is None
            assert wire["faithfulness"]["harmony_jaccard"] is None
            """,
        )
        _environment(
            root,
            "midi",
            wheel,
            "midi",
            f"""
            from pathlib import Path
            from fretsure.importers import ImportSuccess, MIDI_IMPORTER_VERSION, import_midi

            score = Path({str(producer_midi)!r})
            result = import_midi(score)
            assert isinstance(result, ImportSuccess)
            assert result.importer_version == MIDI_IMPORTER_VERSION == "midi@0.1.0"
            assert result.ir.chords == ()
            assert result.ir.meta.duration_beats.numerator == 8
            """,
        )
        _environment(
            root,
            "score",
            wheel,
            "score",
            f"""
            from pathlib import Path
            from fretsure.importers import ImportSuccess, SCORE_INPUT_VERSION, import_score

            xml = import_score(Path({str(producer_xml)!r}))
            midi = import_score(Path({str(producer_midi)!r}))
            assert isinstance(xml, ImportSuccess) and isinstance(midi, ImportSuccess)
            assert xml.importer_version == "musicxml@0.3.0"
            assert midi.importer_version == "midi@0.1.0"
            assert SCORE_INPUT_VERSION == "score-input@0.1.0"
            """,
        )
        _environment(
            root,
            "mcp",
            wheel,
            "mcp",
            """
            from fretsure.mcp.server import MCP_VERSION, create_server

            assert create_server()._mcp_server.version == MCP_VERSION
            """,
        )
    print(
        "Clean wheel install matrix OK "
        "(core replay, benchmark, musicxml, midi, score, service, mcp)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
