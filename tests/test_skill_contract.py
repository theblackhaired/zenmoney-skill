from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_private_profile_is_optional_and_never_committed():
    skill_text = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    gitignore_text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "`skill/PROFILE.md` необязателен" in skill_text
    assert "автоматически создавать или коммитить его нельзя" in skill_text
    assert "skill/" in gitignore_text.splitlines()
