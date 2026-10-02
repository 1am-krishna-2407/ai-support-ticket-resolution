"""Authentication and authorization against a real database: sign-in, sessions, roles."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import select, update

from alembic import command
from app.core.config import Settings
from app.db.models import User, UserSession
from app.main import create_app
from app.scripts.export_openapi import build_spec
from tests.auth_helpers import PASSWORD, create_user, login
from tests.integration.conftest import alembic_config, test_database_url  # noqa: F401

pytestmark = pytest.mark.integration

PUBLIC = {
    ("get", "/api/v1/health"),
    ("get", "/api/v1/version"),
    ("get", "/api/v1/ready"),
    ("post", "/api/v1/auth/login"),
    ("post", "/api/v1/auth/logout"),
}


@pytest.fixture
async def stack(
    alembic_config: Config,  # noqa: F811
    make_settings: Callable[..., Settings],
) -> AsyncIterator[tuple[Any, Callable[..., httpx.AsyncClient]]]:
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    settings = make_settings(
        database_url=str(alembic_config.attributes["database_url"]),
        rate_limit_login_per_minute=5,
        password_min_length=12,
    )
    app = create_app(settings)
    clients: list[httpx.AsyncClient] = []

    def new_client(host: str = "203.0.113.20") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(host, 5000))
        client = httpx.AsyncClient(transport=transport, base_url="http://t")
        clients.append(client)
        return client

    async with app.router.lifespan_context(app):
        try:
            yield app, new_client
        finally:
            for client in clients:
                await client.aclose()


async def test_every_non_public_route_rejects_anonymous_requests(stack: Any) -> None:
    _, new_client = stack
    c = new_client()
    spec = build_spec()
    checked = 0
    for path, operations in spec["paths"].items():
        for method in operations:
            if (method, path) in PUBLIC:
                continue
            url = path.replace("{document_id}", "doc").replace("{job_id}", "0" * 32)
            url = url.replace("{evaluation_id}", "0" * 32).replace("{user_id}", "0" * 32)
            url = url.replace("{analysis_id}", "0" * 32)
            r = await c.request(method.upper(), url, json={})
            assert r.status_code == 401, (method, path, r.status_code)
            assert r.json()["error"]["code"] == "UNAUTHORIZED"
            checked += 1
    assert checked >= 20


async def test_login_sets_a_hardened_cookie_and_me_works(stack: Any) -> None:
    app, new_client = stack
    user = await create_user(app, role="agent", email="Agent.One@Example.test")
    c = new_client()
    r = await login(c, "  agent.one@example.TEST ")  # emails are case- and space-insensitive
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "agent.one@example.test"
    assert r.json()["stats"]["analyses"] == 0
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api" in cookie
    me = await c.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["user"]["id"] == str(user.id)
    assert "password" not in me.text.lower()


async def test_session_token_is_stored_only_as_a_hash(stack: Any) -> None:
    app, new_client = stack
    await create_user(app, email="hash@example.test")
    c = new_client()
    await login(c, "hash@example.test")
    token = c.cookies.get("srp_session")
    assert token
    async with app.state.session_factory() as s:
        stored = (await s.execute(select(UserSession.token_hash))).scalars().all()
    assert token not in stored and hashlib.sha256(token.encode()).hexdigest() in stored


async def test_wrong_password_unknown_email_and_disabled_account_look_the_same(
    stack: Any,
) -> None:
    app, new_client = stack
    disabled = await create_user(app, email="disabled@example.test")
    async with app.state.session_factory() as s:
        await s.execute(update(User).where(User.id == disabled.id).values(is_active=False))
        await s.commit()
    await create_user(app, email="real@example.test")
    c = new_client()
    answers = [
        await login(c, "real@example.test", "wrong password here"),
        await login(c, "nobody@example.test"),
        await login(c, "disabled@example.test"),
    ]
    assert {r.status_code for r in answers} == {401}
    assert len({r.json()["error"]["message"] for r in answers}) == 1
    assert "srp_session" not in c.cookies


async def test_login_is_rate_limited(stack: Any) -> None:
    _, new_client = stack
    c = new_client("203.0.113.77")
    codes = [(await login(c, "x@example.test", "nope nope nope")).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


async def test_logout_revokes_the_session_server_side(stack: Any) -> None:
    app, new_client = stack
    await create_user(app, email="out@example.test")
    c = new_client()
    await login(c, "out@example.test")
    stolen = c.cookies.get("srp_session")
    assert (await c.post("/api/v1/auth/logout")).status_code == 204
    replay = new_client()
    replay.cookies.set("srp_session", stolen or "", domain="t", path="/api")
    assert (await replay.get("/api/v1/auth/me")).status_code == 401


async def test_expired_session_is_rejected(stack: Any) -> None:
    app, new_client = stack
    await create_user(app, email="old@example.test")
    c = new_client()
    await login(c, "old@example.test")
    async with app.state.session_factory() as s:
        await s.execute(update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(1)))
        await s.commit()
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_agents_cannot_use_admin_routes(stack: Any) -> None:
    app, new_client = stack
    await create_user(app, email="agent@example.test")
    c = new_client()
    await login(c, "agent@example.test")
    for method, url in [
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("POST", "/api/v1/ingestion/run"),
        ("POST", "/api/v1/evaluations/run"),
        ("GET", "/api/v1/tickets/history?scope=all"),
    ]:
        r = await c.request(method, url, json={})
        assert r.status_code == 403, (method, url, r.status_code)


async def test_admin_manages_users(stack: Any) -> None:
    app, new_client = stack
    admin = await create_user(app, role="admin", email="boss@example.test")
    c = new_client()
    await login(c, "boss@example.test")

    weak = await c.post(
        "/api/v1/users", json={"email": "n@example.test", "name": "N", "password": "short"}
    )
    assert weak.status_code == 400
    created = await c.post(
        "/api/v1/users",
        json={"email": "New@Example.test", "name": "New Agent", "password": PASSWORD},
    )
    assert created.status_code == 201 and created.json()["role"] == "agent"
    duplicate = await c.post(
        "/api/v1/users",
        json={"email": "new@example.test", "name": "Again", "password": PASSWORD},
    )
    assert duplicate.status_code == 409

    agent_client = new_client()
    assert (await login(agent_client, "new@example.test")).status_code == 200
    user_id = created.json()["id"]
    r = await c.patch(f"/api/v1/users/{user_id}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    # Deactivation signs the user out immediately and blocks new sign-ins.
    assert (await agent_client.get("/api/v1/auth/me")).status_code == 401
    assert (await login(new_client(), "new@example.test")).status_code == 401

    listing = (await c.get("/api/v1/users")).json()
    assert listing["total"] == 2 and {u["email"] for u in listing["items"]} >= {"boss@example.test"}

    own = await c.patch(f"/api/v1/users/{admin.id}", json={"role": "agent"})
    assert own.status_code == 400  # no self-demotion (and never zero admins)


async def test_admin_password_reset_signs_the_user_out(stack: Any) -> None:
    app, new_client = stack
    await create_user(app, role="admin", email="root@example.test")
    target = await create_user(app, email="reset@example.test")
    admin, agent = new_client(), new_client()
    await login(admin, "root@example.test")
    await login(agent, "reset@example.test")
    new_password = "a brand new passphrase"
    r = await admin.patch(f"/api/v1/users/{target.id}", json={"password": new_password})
    assert r.status_code == 200
    assert (await agent.get("/api/v1/auth/me")).status_code == 401
    assert (await login(new_client(), "reset@example.test", new_password)).status_code == 200


async def test_password_change_keeps_this_browser_and_signs_out_others(stack: Any) -> None:
    app, new_client = stack
    await create_user(app, email="me@example.test")
    here, there = new_client(), new_client()
    await login(here, "me@example.test")
    await login(there, "me@example.test")
    bad = await here.post(
        "/api/v1/auth/password",
        json={"current_password": "not it at all", "new_password": "another long passphrase"},
    )
    assert bad.status_code == 401
    ok = await here.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "another long passphrase"},
    )
    assert ok.status_code == 204
    assert (await here.get("/api/v1/auth/me")).status_code == 200
    assert (await there.get("/api/v1/auth/me")).status_code == 401


async def test_cross_site_writes_are_rejected(stack: Any) -> None:
    app, new_client = stack
    await create_user(app, email="csrf@example.test")
    c = new_client()
    await login(c, "csrf@example.test")
    r = await c.post("/api/v1/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert (await c.get("/api/v1/auth/me")).status_code == 200  # still signed in
