"""Password hashing with Argon2id (argon2-cffi defaults: RFC 9106 low-memory profile)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Verified against when the email is unknown, so a failed sign-in takes the same time
# whether or not the account exists (no user enumeration by timing).
_DUMMY_HASH = _hasher.hash("not-a-real-password-used-for-timing-only")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problem(password: str, *, email: str, min_length: int) -> str | None:
    """A reason the password is unacceptable, or None. Length is the main rule (NIST 800-63B)."""
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters."
    if len(password) > 256:
        return "Password must be at most 256 characters."
    if password.strip().lower() == email.strip().lower():
        return "Password must not be the email address."
    if len(set(password)) < 4:
        return "Password is too repetitive."
    return None
