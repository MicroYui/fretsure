"""Render canonical Tab MIDI through the FluidSynth command-line synthesizer."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from enum import StrEnum
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path

from fretsure.render.midi import MidiExportError, render_midi
from fretsure.tab import Tab

AUDIO_EXPORT_VERSION = "tab-audio@0.1.0"
AUDIO_SAMPLE_RATE = 44_100
_FLUIDSYNTH_VERSION = re.compile(
    r"FluidSynth (?:runtime|executable) version ([0-9]+(?:\.[0-9]+){1,3})"
)


class AudioExportCode(StrEnum):
    """Stable failures from the optional FluidSynth runtime boundary."""

    INVALID_TAB = "INVALID_TAB"
    SYNTHESIZER_UNAVAILABLE = "SYNTHESIZER_UNAVAILABLE"
    SYNTHESIS_FAILED = "SYNTHESIS_FAILED"


class AudioExportError(RuntimeError):
    """A safe audio-export failure without subprocess output."""

    def __init__(self, code: AudioExportCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


def _soundfont_resource() -> Traversable:
    return files("fretsure.audio_data").joinpath("sonivox.sf2")


def fluidsynth_available(*, executable: str | None = None) -> bool:
    """Return whether both the synthesizer binary and bundled SoundFont are present."""

    binary = executable or shutil.which("fluidsynth")
    resource = _soundfont_resource()
    return binary is not None and resource.is_file()


def fluidsynth_version(*, executable: str | None = None) -> str | None:
    """Return the detected FluidSynth version without exposing process output."""

    binary = executable or shutil.which("fluidsynth")
    if binary is None:
        return None
    try:
        completed = subprocess.run(
            [binary, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=5,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    match = _FLUIDSYNTH_VERSION.search(completed.stdout)
    return None if match is None else match.group(1)


def render_wav(
    tab: Tab,
    *,
    tempo_bpm: float = 90.0,
    executable: str | None = None,
) -> bytes:
    """Return a 44.1 kHz WAV synthesized from the canonical Tab MIDI."""

    try:
        midi = render_midi(tab, tempo_bpm=tempo_bpm)
    except MidiExportError:
        raise AudioExportError(
            AudioExportCode.INVALID_TAB,
            "Tab or tempo cannot be represented by the canonical audio profile",
        ) from None

    binary = executable or shutil.which("fluidsynth")
    if binary is None:
        raise AudioExportError(
            AudioExportCode.SYNTHESIZER_UNAVAILABLE,
            "FluidSynth is not installed or is not on PATH",
        )

    resource = _soundfont_resource()
    if not resource.is_file():
        raise AudioExportError(
            AudioExportCode.SYNTHESIZER_UNAVAILABLE,
            "the bundled SONiVOX SoundFont is unavailable",
        )

    with as_file(resource) as soundfont, tempfile.TemporaryDirectory(
        prefix="fretsure-audio-"
    ) as directory:
        root = Path(directory)
        midi_path = root / "arrangement.mid"
        wav_path = root / "arrangement.wav"
        midi_path.write_bytes(midi)
        command = [
            binary,
            "-ni",
            "-q",
            "-r",
            str(AUDIO_SAMPLE_RATE),
            "-T",
            "wav",
            "-F",
            str(wav_path),
            str(soundfont),
            str(midi_path),
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise AudioExportError(
                AudioExportCode.SYNTHESIS_FAILED,
                "FluidSynth did not complete the audio render",
            ) from None
        if completed.returncode != 0 or not wav_path.is_file():
            raise AudioExportError(
                AudioExportCode.SYNTHESIS_FAILED,
                "FluidSynth rejected the canonical MIDI render",
            )
        wav = wav_path.read_bytes()

    if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise AudioExportError(
            AudioExportCode.SYNTHESIS_FAILED,
            "FluidSynth returned an invalid WAV file",
        )
    return wav


__all__ = [
    "AUDIO_EXPORT_VERSION",
    "AUDIO_SAMPLE_RATE",
    "AudioExportCode",
    "AudioExportError",
    "fluidsynth_available",
    "fluidsynth_version",
    "render_wav",
]
