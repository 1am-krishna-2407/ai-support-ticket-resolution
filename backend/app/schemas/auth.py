"""Sign-in, user management and saved-analysis contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import TicketAnalysisResponse

# Deliberately loose: one "@", no spaces. Deliverability is the admin's concern.
EMAIL_PATTERN = r"^[^@\s]{1,64}@[^@\s]{1,189}$"
Role = Literal["admin", "agent"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    """Self sign-up. There is no role field: self-made accounts are always agents."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class AuthConfig(BaseModel):
    registration: Literal["open", "approval", "closed"]
    password_min_length: int


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class UserStats(BaseModel):
    analyses: int
    drafts: int
    withheld: int
    insufficient_evidence: int
    unavailable: int
    last_analysis_at: datetime | None


class MeResponse(BaseModel):
    user: UserView
    stats: UserStats


class RegisterResponse(BaseModel):
    # active: signed in now (`me` is set); pending: an admin must activate the account.
    status: Literal["active", "pending"]
    me: MeResponse | None = None


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(pattern=EMAIL_PATTERN, max_length=254)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)
    role: Role = "agent"


class UserUpdate(BaseModel):
    """Admin changes to another account. A new password signs the user out everywhere."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=1, max_length=256)


class UserListItem(UserView):
    analyses: int


class UserPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[UserListItem]


class TicketHistoryItem(BaseModel):
    id: uuid.UUID
    created_at: datetime
    title: str
    status: str
    category: str | None
    priority: str | None
    grounded: bool | None
    latency_ms: float | None
    user_email: str


class TicketHistoryPage(BaseModel):
    total: int
    offset: int
    limit: int
    scope: Literal["mine", "all"]
    items: list[TicketHistoryItem]


class TicketHistoryDetail(BaseModel):
    id: uuid.UUID
    created_at: datetime
    user_email: str
    title: str
    description: str
    environment: dict[str, Any] | None
    result: TicketAnalysisResponse
