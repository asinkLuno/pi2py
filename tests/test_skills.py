"""Skill loading: forced injection of SKILL.md bodies into the prompt."""

from pathlib import Path

import pytest

from pi2py import Agent, load_skill


def test_load_skill_strips_frontmatter(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "---\nname: humanizer\ndescription: Remove AI-sounding writing.\n---\n\nRewrite the text naturally.\n",
        encoding="utf-8",
    )
    assert load_skill(skill) == "Rewrite the text naturally.\n"


def test_load_skill_accepts_skill_directory(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("Body.", encoding="utf-8")
    assert load_skill(tmp_path) == "Body."


def test_load_skill_plain_markdown_passthrough(tmp_path: Path) -> None:
    skill = tmp_path / "notes.md"
    skill.write_text("# Notes\n\nBody.\n", encoding="utf-8")
    assert load_skill(skill) == "# Notes\n\nBody.\n"


def test_load_skill_keeps_body_horizontal_rules(tmp_path: Path) -> None:
    # First \n--- closes the frontmatter; later rules are body.
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: s\n---\n\nIntro.\n\n---\n\nOutro.", encoding="utf-8")
    assert load_skill(skill) == "Intro.\n\n---\n\nOutro."


def test_load_skill_missing_path_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no-such-skill"):
        load_skill(tmp_path / "no-such-skill")


def test_agent_concatenates_skills_into_instructions(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text(
        "---\nname: s\n---\n\nSkill body.", encoding="utf-8"
    )
    agent = Agent(
        model="m",
        provider={"id": "p"},
        instructions="Base.",
        skills=[tmp_path],
    )
    assert agent._instructions == "Base.\n\nSkill body."


def test_run_skill_injects_for_one_run_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "SKILL.md").write_text(
        "---\nname: s\n---\n\nSkill body.", encoding="utf-8"
    )
    agent = Agent(model="m", provider={"id": "p"}, instructions="Base.")

    captured: list[dict] = []

    class FakeProcess:
        def request(self, command: str, payload: dict) -> dict:
            captured.append(payload)
            return {"content": "ok"}

    monkeypatch.setattr(agent, "_process", FakeProcess())

    assert agent.run_skill(tmp_path, "the args").content == "ok"
    assert captured[0]["instructions"] == "Base.\n\nSkill body."
    assert captured[0]["input"] == "the args"

    agent.run("next question")  # plain run is unaffected
    assert captured[1]["instructions"] == "Base."
