from pathlib import Path

from zenmoney_mcp.auth import AuthSettings


def test_budget_deployment_trusts_shared_owner_without_changing_resource(monkeypatch):
    template = (
        Path(__file__).resolve().parents[1] / "deploy/server.env.template"
    ).read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1)
        for line in template.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ZENMONEY_OAUTH_SUBJECT", "shared-owner-subject")

    settings = AuthSettings.from_env()
    assert settings.issuer == "https://auth.theblackhaired.ru/auth/realms/master"
    assert settings.jwks_url == (
        settings.issuer + "/protocol/openid-connect/certs"
    )
    assert settings.resource == "https://budget.theblackhaired.ru/mcp"
    assert settings.allowed_subject == "shared-owner-subject"
