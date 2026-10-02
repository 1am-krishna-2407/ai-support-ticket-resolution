"""Create a user, or reset an existing user's password. Used to create the first admin.

    python -m app.scripts.create_user --email you@example.com --name "Your Name" --role admin

The password is read without echo from the terminal, or from stdin with --password-stdin
(for automation). It is never accepted as a command-line argument, which would leave it in
shell history and process listings. With --reset, an existing account gets the new password
(and is reactivated); all its sessions are signed out.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.passwords import hash_password, password_problem
from app.db.models import UserRole
from app.db.session import create_engine, create_session_factory
from app.repositories import users as users_repo


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a user or reset a password.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", help="Display name (required when creating)")
    parser.add_argument("--role", choices=[r.value for r in UserRole], default=UserRole.AGENT.value)
    parser.add_argument(
        "--password-stdin", action="store_true", help="Read the password from stdin"
    )
    parser.add_argument("--reset", action="store_true", help="Reset an existing user's password")
    return parser.parse_args(argv)


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != first:
        raise SystemExit("Passwords do not match.")
    return first


async def _run(args: argparse.Namespace) -> dict[str, str]:
    settings = get_settings()
    password = _read_password(args.password_stdin)
    problem = password_problem(password, email=args.email, min_length=settings.password_min_length)
    if problem:
        raise SystemExit(problem)
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            existing = await users_repo.get_by_email(session, args.email)
            if args.reset:
                if existing is None:
                    raise SystemExit("No user with that email.")
                existing.password_hash = hash_password(password)
                existing.password_changed_at = datetime.now(UTC)
                existing.is_active = True
                await users_repo.delete_user_sessions(session, existing.id)
                await session.commit()
                return {"status": "password_reset", "email": existing.email}
            if existing is not None:
                raise SystemExit("A user with that email already exists (use --reset).")
            if not args.name:
                raise SystemExit("--name is required when creating a user.")
            user = await users_repo.create_user(
                session,
                email=args.email,
                name=args.name,
                password_hash=hash_password(password),
                role=UserRole(args.role),
            )
            await session.commit()
            return {"status": "created", "email": user.email, "role": user.role}
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    args = _parse(sys.argv[1:] if argv is None else argv)
    configure_logging("WARNING", sys.stderr)
    print(json.dumps(asyncio.run(_run(args))))


if __name__ == "__main__":
    main()
