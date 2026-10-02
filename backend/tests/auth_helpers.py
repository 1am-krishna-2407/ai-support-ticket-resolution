"""Create real users in the test database and sign in through the API."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.passwords import hash_password
from app.db.models import User, UserRole
from app.repositories import users as users_repo

PASSWORD = "correct horse battery staple"


async def create_user(
    app: Any, *, role: str = "agent", email: str | None = None, password: str = PASSWORD
) -> User:
    async with app.state.session_factory() as session:
        user = await users_repo.create_user(
            session,
            email=email or f"{role}@example.test",
            name=role.title(),
            password_hash=hash_password(password),
            role=UserRole(role),
        )
        await session.commit()
        return user


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def login_as(
    app: Any, client: httpx.AsyncClient, *, role: str = "agent", email: str | None = None
) -> User:
    user = await create_user(app, role=role, email=email)
    response = await login(client, user.email)
    assert response.status_code == 200, response.text
    return user
