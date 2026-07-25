from __future__ import annotations

import subprocess
import wave
from fractions import Fraction
from io import BytesIO
from pathlib import Path

import pytest

from fretsure.audio.fluidsynth import (
    AUDIO_SAMPLE_RATE,
    AudioExportCode,
    AudioExportError,
    fluidsynth_available,
    fluidsynth_version,
    render_wav,
)
from fretsure.tab import Tab, TabNote


def _tab() -> Tab:
    return Tab(
        (TabNote(Fraction(0), Fraction(1), 1, 3, 1, "i"),),
        (40, 45, 50, 55, 59, 64),
        0,
    )


def _wav() -> bytes:
    return b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"\x00" * 36


def test_render_wav_invokes_fluidsynth_with_canonical_midi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.extend(command)
        output = Path(command[command.index("-F") + 1])
        midi = Path(command[-1]).read_bytes()
        assert midi.startswith(b"MThd")
        output.write_bytes(_wav())
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert render_wav(_tab(), executable="/usr/local/bin/fluidsynth") == _wav()
    assert captured[:3] == ["/usr/local/bin/fluidsynth", "-ni", "-q"]
    assert captured[captured.index("-r") + 1] == str(AUDIO_SAMPLE_RATE)


def test_render_wav_reports_missing_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fretsure.audio.fluidsynth.shutil.which", lambda _name: None)

    with pytest.raises(AudioExportError) as caught:
        render_wav(_tab())

    assert caught.value.code is AudioExportCode.SYNTHESIZER_UNAVAILABLE


def test_render_wav_hides_subprocess_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, b"", b"private stderr")

    monkeypatch.setattr(subprocess, "run", failed)

    with pytest.raises(AudioExportError) as caught:
        render_wav(_tab(), executable="/usr/local/bin/fluidsynth")

    assert caught.value.code is AudioExportCode.SYNTHESIS_FAILED
    assert "private stderr" not in str(caught.value)


def test_fluidsynth_version_returns_only_the_numeric_runtime_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "private build path /tmp/secret"

    def version(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            f"FluidSynth runtime version 2.5.6\n{secret}",
            "",
        )

    monkeypatch.setattr(subprocess, "run", version)

    assert fluidsynth_version(executable="/usr/local/bin/fluidsynth") == "2.5.6"


def test_fluidsynth_version_is_none_when_runtime_cannot_be_identified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            "unknown synthesizer",
            "",
        ),
    )

    assert fluidsynth_version(executable="/usr/local/bin/fluidsynth") is None


@pytest.mark.skipif(not fluidsynth_available(), reason="FluidSynth runtime unavailable")
def test_real_fluidsynth_renders_playable_wav() -> None:
    wav = render_wav(_tab(), tempo_bpm=120)

    with wave.open(BytesIO(wav), "rb") as reader:
        assert reader.getframerate() == AUDIO_SAMPLE_RATE
        assert reader.getnchannels() == 2
        assert reader.getnframes() > 0
