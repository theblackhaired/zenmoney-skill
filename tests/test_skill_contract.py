from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_profile_path_is_unambiguous_in_skill_instructions():
    skill_text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "read `skill/PROFILE.md`" in skill_text
    assert "read `PROFILE.md` in skill directory" not in skill_text
