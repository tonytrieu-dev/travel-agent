"""Connectors routes: the live, no-restart toggle for the Slack HITL connector. Only two
behaviors are worth a dedicated test here — the toggle actually persists (the entire reason
this is a DB row instead of an env var), and it can't be enabled without credentials (the
guard that stops a demo from silently no-oping)."""

import pytest


def test_patch_slack_connector_persists_across_get(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import config

    settings = config.Settings(
        cerebras_api_key="x", searchapi_api_key="x", tavily_api_key="x",
        database_url=config.get_settings().database_url,
        slack_bot_token="xoxb-test", slack_signing_secret="secret",
        slack_approvals_channel_id="C123",
    )
    monkeypatch.setattr("app.routes.connectors.get_settings", lambda: settings)

    enable_response = client.patch("/api/connectors/slack", json={"enabled": True})
    assert enable_response.status_code == 200, enable_response.text
    assert enable_response.json()["slack"] == {"configured": True, "enabled": True}

    get_response = client.get("/api/connectors")
    assert get_response.json()["slack"] == {"configured": True, "enabled": True}, (
        "the toggle must survive a separate GET, not just echo back the PATCH body — that's "
        "the entire point of persisting it in connector_setting instead of in-memory"
    )


def test_patch_slack_connector_without_credentials_is_409(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forces the unconfigured state explicitly rather than relying on the ambient environment
    lacking Slack env vars — a developer who follows docs/SLACK_SETUP.md locally would otherwise
    silently flip this test from 409 to 200 the moment their own .env picks up real credentials."""
    from app import config

    unconfigured_settings = config.Settings(
        cerebras_api_key="x", searchapi_api_key="x", tavily_api_key="x",
        database_url=config.get_settings().database_url,
    )
    monkeypatch.setattr("app.routes.connectors.get_settings", lambda: unconfigured_settings)

    response = client.patch("/api/connectors/slack", json={"enabled": True})

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "connector_not_configured"
