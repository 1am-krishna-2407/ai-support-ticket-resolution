"""Sign-in, sign-out, the current user (with profile stats), and password change."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import SessionDep, SettingsDep
from app.api.responses import BODY, errors
from app.core.auth import (
    CurrentUser,
    clear_session_cookie,
    hash_token,
    session_token,
    start_session,
)
from app.core.errors import InvalidRequestError, UnauthorizedError
from app.core.passwords import hash_password, needs_rehash, password_problem, verify_password
from app.core.rate_limit import rate_limit
from app.db.models import User
from app.repositories import ticket_history
from app.repositories import users as users_repo
from app.schemas.auth import LoginRequest, MeResponse, PasswordChange, UserStats, UserView

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_LOGIN = "The email or password is incorrect, or the account is disabled."


async def user_stats(session: SessionDep, user: User) -> UserStats:
    raw = await ticket_history.stats_for_user(session, user.id)
    by_status: dict[str, int] = raw["by_status"]
    return UserStats(
        analyses=sum(by_status.values()),
        drafts=by_status.get("draft", 0),
        withheld=by_status.get("withheld", 0),
        insufficient_evidence=by_status.get("insufficient_evidence", 0),
        unavailable=by_status.get("unavailable", 0),
        last_analysis_at=raw["last_analysis_at"],
    )


async def me_response(session: SessionDep, user: User) -> MeResponse:
    return MeResponse(user=UserView.model_validate(user), stats=await user_stats(session, user))


@router.post(
    "/login",
    response_model=MeResponse,
    responses=BODY | errors(401, 429),
    dependencies=[Depends(rate_limit("login"))],
    summary="Sign in with email and password (sets an HttpOnly session cookie)",
)
async def login(
    body: LoginRequest, response: Response, session: SessionDep, settings: SettingsDep
) -> MeResponse:
    user = await users_repo.get_by_email(session, body.email)
    # verify_password runs a full Argon2 check even for unknown emails (constant-ish time).
    valid = verify_password(user.password_hash if user else None, body.password)
    if user is None or not valid or not user.is_active:
        raise UnauthorizedError(INVALID_LOGIN)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)
    await start_session(session, response, user, settings)
    await session.commit()
    return await me_response(session, user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out this browser (the session is deleted server-side)",
)
async def logout(request: Request, session: SessionDep, settings: SettingsDep) -> Response:
    token = session_token(request, settings)
    if token is not None:
        await users_repo.delete_session(session, hash_token(token))
        await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(response, settings)
    return response


@router.get(
    "/me",
    response_model=MeResponse,
    responses=errors(401),
    summary="The signed-in user and their analysis stats",
)
async def me(user: CurrentUser, session: SessionDep) -> MeResponse:
    return await me_response(session, user)


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=BODY | errors(400, 401),
    summary="Change your password (signs out your other browsers)",
)
async def change_password(
    body: PasswordChange,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    account = await users_repo.get_by_id(session, user.id)  # attached copy to update
    if account is None or not verify_password(account.password_hash, body.current_password):
        raise UnauthorizedError("The current password is incorrect.")
    problem = password_problem(
        body.new_password, email=user.email, min_length=settings.password_min_length
    )
    if problem:
        raise InvalidRequestError(problem)
    account.password_hash = hash_password(body.new_password)
    account.password_changed_at = datetime.now(UTC)
    token = session_token(request, settings)
    await users_repo.delete_user_sessions(
        session, user.id, keep_token_hash=hash_token(token) if token else None
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
