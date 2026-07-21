"""Auth boundary. Today every request resolves to one fixed demo user; when real auth lands,
only this function changes — no route handler reaches into app.config or app.models directly.
"""

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.config import DEMO_USER_EMAIL
from app.db import get_session
from app.models import User


async def get_current_user(session: AsyncSession = Depends(get_session)) -> User:
    user = await session.scalar(select(User).where(col(User.email) == DEMO_USER_EMAIL))
    if user is not None:
        return user
    user = User(email=DEMO_USER_EMAIL)
    session.add(user)
    await session.commit()
    return user
