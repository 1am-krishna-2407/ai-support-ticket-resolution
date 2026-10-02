"""User management (admins only). Accounts are deactivated, never deleted, to keep history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep, SettingsDep
from app.api.responses import BODY, READ, errors
from app.core.auth import AdminUser
from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.core.passwords import hash_password, password_problem
from app.db.models import UserRole
from app.repositories import users as users_repo
from app.schemas.auth import UserCreate, UserListItem, UserPage, UserUpdate, UserView

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserPage, responses=READ | errors(401, 403), summary="List users")
async def list_users(
    _: AdminUser,
    session: SessionDep,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> UserPage:
    total, rows = await users_repo.list_users(session, offset=offset, limit=limit)
    items = [
        UserListItem(**UserView.model_validate(user).model_dump(), analyses=count)
        for user, count in rows
    ]
    return UserPage(total=total, offset=offset, limit=limit, items=items)


@router.post(
    "",
    response_model=UserView,
    status_code=status.HTTP_201_CREATED,
    responses=BODY | errors(400, 401, 403, 409),
    summary="Create a user with an initial password",
)
async def create_user(
    body: UserCreate, _: AdminUser, session: SessionDep, settings: SettingsDep
) -> UserView:
    problem = password_problem(
        body.password, email=body.email, min_length=settings.password_min_length
    )
    if problem:
        raise InvalidRequestError(problem)
    if await users_repo.get_by_email(session, body.email) is not None:
        raise ConflictError("A user with this email already exists.")
    user = await users_repo.create_user(
        session,
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=UserRole(body.role),
    )
    await session.commit()
    return UserView.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserView,
    responses=BODY | errors(400, 401, 403, 404),
    summary="Rename, change role, (de)activate, or reset a user's password",
)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
) -> UserView:
    user = await users_repo.get_by_id(session, user_id)
    if user is None:
        raise NotFoundError("User not found.", details={"user_id": str(user_id)})
    losing_admin = (body.role == "agent" or body.is_active is False) and (
        user.role == UserRole.ADMIN.value and user.is_active
    )
    if losing_admin and user.id == admin.id:
        raise InvalidRequestError("You cannot demote or deactivate your own account.")
    if losing_admin and await users_repo.count_active_admins(session) <= 1:
        raise InvalidRequestError("At least one active admin must remain.")

    revoke = False
    if body.name is not None:
        user.name = body.name.strip()
    if body.role is not None and body.role != user.role:
        user.role, revoke = body.role, True
    if body.is_active is not None and body.is_active != user.is_active:
        user.is_active, revoke = body.is_active, revoke or not body.is_active
    if body.password is not None:
        problem = password_problem(
            body.password, email=user.email, min_length=settings.password_min_length
        )
        if problem:
            raise InvalidRequestError(problem)
        user.password_hash = hash_password(body.password)
        user.password_changed_at = datetime.now(UTC)
        revoke = True
    if revoke:  # new permissions or credentials apply from the next sign-in
        await users_repo.delete_user_sessions(session, user.id)
    await session.commit()
    await session.refresh(user)
    return UserView.model_validate(user)
