"""Teach python-ly to carry string numbers into the MusicXML it emits.

The engravers wrote 619 of them -- `<g-1\\2>` is *G, first finger, second
string* -- and 169 sit on notes that also carry a finger, which makes them
complete placements with nothing left to infer. None reach this corpus, and
without them every geometric question about the verifier has to guess which
string a printed fingering meant. That guessing produced three wrong results in
a single week.

The loss is not in this repository and not in the pinned wrapper. `python-ly`
already *lexes* the indication -- `ly.lex.lilypond.StringNumber`, regex
``\\\\\\d+`` -- and its own mediator docstring says it handles "an articulation,
fingering, string number, or other symbol". The string-number branch was never
written, so the token falls through to the articulation lookup, fails to match,
and is dropped.

Three additions restore it, mirroring what fingering already does:

* the note object gains a ``string_number`` field and a setter,
* the mediator routes a ``StringNumber`` token to it,
* the writer emits ``<string>`` inside the ``<technical>`` element it is already
  building for the fingering.

This patches an installed dependency at import time rather than vendoring a
fork. That is the smaller commitment, but it is not free: the manifest pins the
converter by digest, and a monkeypatched converter is no longer the thing that
digest describes. So the patch is applied explicitly by the caller, has its own
digest, and is recorded in the manifest beside the converter's -- a conversion
run that does not say it was patched is a different conversion.

MusicXML numbers strings 1 (highest) to 6 (lowest), which is what LilyPond means
by ``\\1``..``\\6`` and what `score_corpus.py` already expects, so no
renumbering happens here.

**Chords lose one indication.** Inside ``<g-1\\2 c-3\\5>`` the mediator points at
one ``current_note`` for both members, so the second string number overwrites the
first. That is not introduced here: python-ly's existing fingering support loses
one the same way, three tokens becoming two ``<fingering>`` elements. The patch
mirrors the fingering path exactly, limitation included, rather than diverging
from it -- fixing chord identity is a change to python-ly's note model and a
much larger thing than restoring a dropped branch.
"""

from __future__ import annotations

from typing import Any, Final

PATCH_VERSION: Final = "python-ly-string-numbers@0.1.0"


def apply(ly_module: Any) -> None:
    """Add string-number support to an imported ``ly`` package, in place.

    Idempotent: applying twice is harmless, because each step checks for its own
    marker first. That matters because the corpus build imports the converter
    once per manifest and there is no reason for the caller to track whether it
    has already run.
    """

    from ly.lex import lilypond as lex
    from ly.musicxml import create_musicxml, ly2xml_mediator, xml_objs

    if getattr(xml_objs.BarNote, "_fretsure_string_numbers", False):
        return

    # 1. The note carries it.
    original_init = xml_objs.BarNote.__init__

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.string_number = None

    def add_string_number(self: Any, number: int) -> None:
        self.string_number = number

    xml_objs.BarNote.__init__ = __init__
    xml_objs.BarNote.add_string_number = add_string_number
    xml_objs.BarNote._fretsure_string_numbers = True

    # 2. The mediator routes the token, which it already receives and discards.
    original_articulation = ly2xml_mediator.Mediator.new_articulation

    def new_articulation(self: Any, art_token: Any) -> None:
        if isinstance(art_token, lex.StringNumber):
            note = getattr(self, "current_note", None)
            if note is not None:
                digits = str(art_token).lstrip("\\")
                if digits.isdigit():
                    note.add_string_number(int(digits))
            return
        original_articulation(self, art_token)

    ly2xml_mediator.Mediator.new_articulation = new_articulation

    # 3. The writer emits it, into the <technical> node fingering already opens.
    def add_string_number_xml(self: Any, number: int) -> None:
        self.add_technical()
        node = create_musicxml.etree.SubElement(self.current_tech, "string")
        node.text = str(number)

    create_musicxml.CreateMusicXML.add_string_number = add_string_number_xml

    original_new_xml_note = xml_objs.IterateXmlObjs.new_xml_note

    def new_xml_note(self: Any, obj: Any) -> None:
        original_new_xml_note(self, obj)
        number = getattr(obj, "string_number", None)
        if number is not None:
            self.musxml.add_string_number(number)

    xml_objs.IterateXmlObjs.new_xml_note = new_xml_note


__all__ = ["PATCH_VERSION", "apply"]
