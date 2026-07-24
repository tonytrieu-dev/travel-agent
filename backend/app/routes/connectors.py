"""Connectors routes: a live, DB-backed toggle for the Slack HITL connector — no separate
repository module, this is a single row with two simple queries."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import ConnectorSetting
from app.schemas import (
    ConnectorsOut,
    ConnectorStatusOut,
    ConnectorToggleUpdate,
    ErrorCode,
    ProblemDetail,
)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])

_NOT_CONFIGURED: dict[int | str, dict[str, Any]] = {409: {"model": ProblemDetail}}


class ConnectorError(Exception):
    def __init__(self, code: ErrorCode, status_code: int, detail: str) -> None:
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _slack_configured(settings: Settings) -> bool:
    return bool(
        settings.slack_bot_token
        and settings.slack_signing_secret
        and settings.slack_approvals_channel_id
    )


async def _get_or_create_row(session: AsyncSession) -> ConnectorSetting:
    row = await session.scalar(select(ConnectorSetting))
    if row is None:
        row = ConnectorSetting()
        session.add(row)
        await session.commit()
    return row


@router.get("", response_model=ConnectorsOut)
async def get_connectors(session: AsyncSession = Depends(get_session)) -> ConnectorsOut:
    settings = get_settings()
    row = await _get_or_create_row(session)
    return ConnectorsOut(
        slack=ConnectorStatusOut(configured=_slack_configured(settings), enabled=row.slack_enabled)
    )


@router.patch("/slack", response_model=ConnectorsOut, responses=_NOT_CONFIGURED)
async def set_slack_connector(
    body: ConnectorToggleUpdate, session: AsyncSession = Depends(get_session)
) -> ConnectorsOut:
    settings = get_settings()
    if body.enabled and not _slack_configured(settings):
        raise ConnectorError(
            ErrorCode.CONNECTOR_NOT_CONFIGURED,
            409,
            "Slack is not configured on this deployment (missing bot token, signing secret, "
            "or channel id).",
        )
    row = await _get_or_create_row(session)
    row.slack_enabled = body.enabled
    session.add(row)
    await session.commit()
    return ConnectorsOut(
        slack=ConnectorStatusOut(configured=_slack_configured(settings), enabled=row.slack_enabled)
    )
