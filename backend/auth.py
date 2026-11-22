"""Accounts, sessions and API keys.

Design choices:

* Passwords are hashed with scrypt (memory-hard, in the standard library) and a
  per-user random salt. The plain password is never stored or logged.
* Sessions are opaque random tokens, stored server-side as a SHA-256 hash.
  Unlike a self-contained JWT, a session can be revoked instantly (logout,
  password change, account disabled). The browser holds it in an HttpOnly
  cookie, so page scripts cannot read it.
* API keys are the same mechanism with no expiry, sent as a Bearer token, for
  scripts and integrations. They are shown once, at creation.
* Login failures give one generic message and are rate limited, so the API does
  not reveal which emails have accounts and guessing is slow.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.config.settings import Settings
from backend.plans import DEFAULT_PLAN, PLANS, get_plan
from backend.rate_limit import RateLimiter
from database.repository import DuplicateUser, Repository
from security import audit

SESSION_COOKIE = "nexusai_session"
API_KEY_PREFIX = "nxs_"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200
LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,185}\.[^@\s.]{2,}$")
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retry_after: float = 0):
        super().__init__(message)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


@dataclass
class User:
    id: str
    email: str
    name: str
    plan: str
    is_admin: bool
    is_active: bool
    created_at: str | None = None
    via_api_key: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "plan": self.plan,
            "is_admin": self.is_admin,
            "is_active": self.is_active,
            "created_at": self.created_at,
        }


# ------------------------------------------------------------------ hashing
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    encode = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt), dklen=len(expected),
            n=int(n), r=int(r), p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)  # constant-time comparison


# Verified against when the email is unknown, so a login attempt takes the
# same time whether or not the account exists.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def hash_token(token: str) -> str:
    # Tokens are 256 bits of randomness, so a fast hash is sufficient here
    # (unlike passwords, they cannot be guessed by trying likely values).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError("weak_password", f"Use a password of at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError("weak_password", "That password is too long.")
    if password.lower() in {"password123", "1234567890", "qwertyuiop", "passwordpassword"}:
        raise AuthError("weak_password", "That password is too common. Choose another.")


# ------------------------------------------------------------------ service
class AuthService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repo = repository
        self.login_limiter = RateLimiter()

    @staticmethod
    def _to_user(row: dict[str, Any], via_api_key: bool = False) -> User:
        return User(
            id=row["id"], email=row["email"], name=row["name"], plan=row["plan"],
            is_admin=row["is_admin"], is_active=row["is_active"],
            created_at=row.get("created_at"), via_api_key=via_api_key,
        )

    async def _new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=self.settings.session_days)
        await self.repo.add_token(user_id, "session", hash_token(token), expires_at=expires)
        return token

    # ------------------------------------------------------------ accounts
    async def signup(self, email: str, name: str, password: str) -> tuple[User, str]:
        if not self.settings.allow_signup:
            raise AuthError("signup_disabled", "Sign-up is currently closed.", status=403)
        email = normalize_email(email)
        if not _EMAIL.match(email):
            raise AuthError("invalid_email", "Enter a valid email address.")
        validate_password(password)
        password_hash = await asyncio.to_thread(hash_password, password)
        # The first account becomes the administrator (bootstrap).
        is_admin = await self.repo.count_users() == 0
        try:
            row = await self.repo.create_user(
                str(uuid.uuid4()), email, name.strip()[:120], password_hash, DEFAULT_PLAN, is_admin
            )
        except DuplicateUser:
            raise AuthError("email_taken", "An account with this email already exists.", status=409)
        return self._to_user(row), await self._new_session(row["id"])

    async def login(self, email: str, password: str, client: str = "") -> tuple[User, str]:
        email = normalize_email(email)
        key = f"{email}|{client}"
        wait = self.login_limiter.check(key, LOGIN_ATTEMPTS, LOGIN_WINDOW_SECONDS)
        if wait:
            raise AuthError(
                "too_many_attempts",
                "Too many failed sign-in attempts. Try again later.",
                status=429,
                retry_after=wait,
            )
        row = await self.repo.get_user_by_email(email)
        stored = row["password_hash"] if row else _DUMMY_HASH
        valid = await asyncio.to_thread(verify_password, password, stored)
        if not (row and valid and row["is_active"]):
            self.login_limiter.hit(key)
            audit.record("login_failed", severity="low", agent="api", detail={"email": email})
            raise AuthError("invalid_credentials", "Incorrect email or password.", status=401)
        self.login_limiter.reset(key)
        return self._to_user(row), await self._new_session(row["id"])

    async def logout(self, token: str | None) -> None:
        if token:
            await self.repo.delete_token_by_hash(hash_token(token))

    async def authenticate(self, token: str | None) -> User | None:
        """Resolve a session cookie or API key to a user, or None."""
        if not token:
            return None
        found = await self.repo.get_token(hash_token(token))
        if not found:
            return None
        expires = found["expires_at"]
        if expires is not None:
            if expires.tzinfo is None:  # SQLite returns naive datetimes
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                await self.repo.delete_token_by_hash(found["token_hash"])
                return None
        row = await self.repo.get_user(found["user_id"])
        if not row or not row["is_active"]:
            return None
        await self.repo.touch_token(found["id"])
        return self._to_user(row, via_api_key=found["kind"] == "api_key")

    async def change_password(self, user: User, current: str, new: str) -> str:
        """Returns a fresh session token. Every other session and key is revoked."""
        row = await self.repo.get_user(user.id)
        if not row or not await asyncio.to_thread(verify_password, current, row["password_hash"]):
            raise AuthError("invalid_credentials", "Your current password is incorrect.", status=401)
        validate_password(new)
        await self.repo.update_user(user.id, password_hash=await asyncio.to_thread(hash_password, new))
        await self.repo.delete_tokens(user.id, kind="session")
        return await self._new_session(user.id)

    async def delete_account(self, user: User, password: str) -> None:
        row = await self.repo.get_user(user.id)
        if not row or not await asyncio.to_thread(verify_password, password, row["password_hash"]):
            raise AuthError("invalid_credentials", "Your password is incorrect.", status=401)
        if row["is_admin"] and await self.repo.count_admins() <= 1:
            raise AuthError(
                "last_admin", "You are the only administrator. Make someone else an admin first.", status=409
            )
        await self.repo.delete_user_data(user.id)

    # ------------------------------------------------------------ API keys
    async def list_keys(self, user: User) -> list[dict[str, Any]]:
        return await self.repo.list_tokens(user.id, kind="api_key")

    async def create_key(self, user: User, name: str) -> dict[str, Any]:
        limit = get_plan(user.plan).api_keys
        if len(await self.list_keys(user)) >= limit:
            raise AuthError(
                "key_limit", f"Your plan allows {limit} API key(s). Delete one to create another.", status=402
            )
        token = API_KEY_PREFIX + secrets.token_urlsafe(32)
        created = await self.repo.add_token(
            user.id, "api_key", hash_token(token), name=name.strip()[:80] or "API key", prefix=token[:10]
        )
        return {**created, "token": token}  # the only time the key is returned

    async def delete_key(self, user: User, key_id: int) -> bool:
        return await self.repo.delete_token(user.id, key_id, kind="api_key")

    # --------------------------------------------------------------- admin
    async def list_users(self) -> list[dict[str, Any]]:
        return await self.repo.list_users()

    async def admin_update(self, admin: User, user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        target = await self.repo.get_user(user_id)
        if not target:
            raise AuthError("not_found", "Unknown user.", status=404)
        fields: dict[str, Any] = {}
        if changes.get("plan") is not None:
            if changes["plan"] not in PLANS:
                raise AuthError("invalid_plan", "Unknown plan.")
            fields["plan"] = changes["plan"]
        for flag in ("is_active", "is_admin"):
            if changes.get(flag) is not None:
                fields[flag] = bool(changes[flag])
        removing_admin = target["is_admin"] and (
            fields.get("is_admin") is False or fields.get("is_active") is False
        )
        if removing_admin and await self.repo.count_admins() <= 1:
            raise AuthError("last_admin", "The last administrator cannot be removed or disabled.", status=409)
        if fields:
            await self.repo.update_user(user_id, **fields)
            if fields.get("is_active") is False:
                await self.repo.delete_tokens(user_id)  # sign the account out everywhere
            audit.record(
                "admin_changed_account", severity="info", agent="api",
                detail={"admin": admin.email, "target": target["email"], "changes": fields},
            )
        updated = await self.repo.get_user(user_id)
        assert updated is not None
        updated.pop("password_hash", None)
        return updated
