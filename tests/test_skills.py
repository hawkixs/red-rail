"""Skills are facades: they guide, then call the CLI. They carry no rule of their own."""

import re
from pathlib import Path

from rail.policy import REQUIRED_SPEC_SECTIONS

SKILLS = Path(__file__).resolve().parents[1] / "skills"
EXPECTED = {
    "rail-design",
    "rail-plan",
    "rail-check",
    "rail-attest",
    "rail-audit",
    "rail-review",
    "rail-reviewer",
}
FRONTMATTER = re.compile(
    r"^---\nname: (?P<name>[\w-]+)\ndescription: (?P<description>.+?)\n---\n", re.DOTALL
)


def _skills() -> dict[str, str]:
    return {d.name: (d / "SKILL.md").read_text() for d in SKILLS.iterdir() if d.is_dir()}


def test_the_seven_skills_exist() -> None:
    assert set(_skills()) == EXPECTED


def test_frontmatter_name_matches_the_directory() -> None:
    for name, text in _skills().items():
        match = FRONTMATTER.match(text)
        assert match, f"{name}: missing frontmatter"
        assert match["name"] == name
        assert len(match["description"]) > 40


def test_every_skill_calls_the_cli_and_says_the_cli_is_right() -> None:
    for name, text in _skills().items():
        assert re.search(r"`rail (check|attest|audit|contract|reviewer|brain)\b", text), (
            f"{name}: no CLI call"
        )
        assert "the CLI is right" in text, f"{name}: must defer to the CLI"


def test_skills_do_not_restate_the_design_rule() -> None:
    body = _skills()["rail-design"].lower()
    for alias in ("non-goal", "success criteri"):
        assert alias not in body, (
            f"rail-design lists the mandatory sections ({alias}); the gate owns that rule"
        )
    assert all(alias not in body for alias in REQUIRED_SPEC_SECTIONS["non-goals"])


def test_review_skills_defer_to_the_reviewer_and_never_to_themselves() -> None:
    review = _skills()["rail-review"].lower()
    assert "pre-review" in review and "never satisfies" in review
    assert "workflows/pre-review.js" in review
    reviewer = _skills()["rail-reviewer"].lower()
    assert "rail reviewer once" in reviewer and "red-rail-reviewer" in reviewer
