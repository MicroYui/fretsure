"""What an arranging skill has to be in order to earn its place in the prompt.

The registry's whole claim is that it carries *measured* facts about this
oracle, not plausible advice — a language model already has plausible advice, and
shipping more of it under a registry version would be dressing a guess as a
finding. These tests hold the registry to that claim, and to the second rule
that made it necessary: a skill that has gone stale is worse than no skill,
because it speaks in a current voice.
"""

from __future__ import annotations

import re

import pytest

from fretsure.agent.skills import (
    ARRANGEMENT_SKILL_REGISTRY_VERSION,
    SKILL_IDS,
    SKILLS,
    ArrangementSkill,
    skill_guidance,
)

_CONTRACT_RE = re.compile(r"[a-z][a-z0-9-]*@\d+\.\d+\.\d+\Z")


def test_the_registry_is_versioned_like_its_siblings() -> None:
    assert ARRANGEMENT_SKILL_REGISTRY_VERSION.startswith("arrangement-skill-registry@")


def test_every_skill_cites_a_measurement() -> None:
    """A skill without evidence is a guess with extra authority."""

    for skill in SKILLS:
        assert skill.evidence.strip(), skill.id
        # Evidence has to point at something that happened, not restate the rule.
        assert skill.evidence.strip() != skill.guidance.strip(), skill.id


def test_every_skill_names_the_contract_it_was_measured_against() -> None:
    """This is what makes staleness visible instead of silent.

    The proposal prompt carried "keep at most 4 notes sounding at the same
    onset" after `oracle@0.4.0` began admitting six-string gestures. Nothing
    flagged it because nothing recorded what it had been true of.
    """

    for skill in SKILLS:
        assert _CONTRACT_RE.fullmatch(skill.measured_against), (
            skill.id,
            skill.measured_against,
        )


def test_skill_ids_are_unique_and_stable_shaped() -> None:
    assert len(set(SKILL_IDS)) == len(SKILL_IDS)
    for identifier in SKILL_IDS:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", identifier), identifier


def test_guidance_is_the_only_thing_the_model_sees() -> None:
    """Evidence is for whoever maintains the registry; sending it is noise."""

    block = skill_guidance()
    for skill in SKILLS:
        assert skill.guidance in block
        assert skill.evidence not in block
        assert skill.measured_against not in block


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"id": "Bad-Id"}, id="id-not-kebab"),
        pytest.param({"id": "9leading"}, id="id-leading-digit"),
        pytest.param({"guidance": "  "}, id="empty-guidance"),
        pytest.param({"evidence": ""}, id="empty-evidence"),
        pytest.param({"measured_against": " "}, id="empty-contract"),
    ],
)
def test_a_malformed_skill_cannot_be_constructed(kwargs: dict[str, str]) -> None:
    fields = {
        "id": "some-skill",
        "guidance": "Do the thing.",
        "evidence": "Measured on the corpus.",
        "measured_against": "oracle@0.4.0",
    }
    fields.update(kwargs)
    with pytest.raises(ValueError):
        ArrangementSkill(**fields)


def test_the_superseded_four_note_rule_is_gone() -> None:
    """The stale rule this registry replaced must not come back by hand.

    `oracle@0.4.0` admits five- and six-note chords as a single right-hand
    gesture. Telling the proposer otherwise costs arrangements silently.
    """

    block = skill_guidance().lower()
    assert "at most 4 notes" not in block
    assert "five and six note chords are playable" in block
