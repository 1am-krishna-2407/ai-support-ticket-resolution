"""Admin protection for operationally sensitive routes (decision D10)."""

from __future__ import annotations

import ipaddress
import secrets
from typing import Annotated

from fastapi import Depends, Header, Request

from app.core.auth import current_user_optional, is_admin
from app.core.config import AdminMode, Settings, get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.db.models import User


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def require_admin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User | None, Depends(current_user_optional)],
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    """Guards admin-only operations: a signed-in admin, or ADMIN_MODE rules for automation.

    The browser UI uses the admin's session. Scripts and CI can still use X-Admin-Token
    (`token` mode) or a loopback client (`local` mode) without a user account.
    """
    if settings.admin_mode is AdminMode.DEV:
        return
    if user is not None:
        if is_admin(user):
            return
        if not x_admin_token:
            raise ForbiddenError("This requires an admin account.")

    if settings.admin_mode is AdminMode.LOCAL:
        client_host = request.client.host if request.client else None
        if not _is_loopback(client_host):
            raise ForbiddenError("Admin operations are only allowed from the local machine.")
        return

    expected = settings.admin_token.get_secret_value() if settings.admin_token else ""
    if not x_admin_token:
        raise UnauthorizedError("An X-Admin-Token header is required for this operation.")
    if not expected or not secrets.compare_digest(x_admin_token.encode(), expected.encode()):
        raise ForbiddenError("The admin token is invalid.")
