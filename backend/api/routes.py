"""HTTP API of the NexusAI backend.

Authentication: a session cookie (browser) or `Authorization: Bearer <API key>`
(scripts). Every route below except sign-up, sign-in and the plan list requires
one of them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from backend.api.schemas import (
    AccountDelete,
    AdminUserUpdate,
    ApiKeyCreate,
    LoginRequest,
    PasswordChange,
    ResearchCreate,
    ResearchResponse,
    SignupRequest,
)
from backend.auth import SESSION_COOKIE, AuthError, AuthService, User
from backend.plans import plans_as_dicts
from backend.service import InvalidState, LimitExceeded, NotFound, ResearchService
from security.input_validation import InputValidationError

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- dependencies
def service(request: Request) -> ResearchService:
    return request.app.state.service


def auth(request: Request) -> AuthService:
    return request.app.state.auth


def error(status: int, code: str, message: str, retry_after: float = 0) -> HTTPException:
    headers = {"Retry-After": str(int(retry_after) + 1)} if retry_after else None
    return HTTPException(status_code=status, detail={"code": code, "message": message}, headers=headers)


def auth_error(exc: AuthError) -> HTTPException:
    return error(exc.status, exc.code, exc.message, exc.retry_after)


def request_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(SESSION_COOKIE)


async def current_user(request: Request) -> User:
    user = await auth(request).authenticate(request_token(request))
    if user is None:
        raise error(401, "not_authenticated", "Sign in to continue.")
    return user


async def session_user(user: User = Depends(current_user)) -> User:
    """Account management must come from a signed-in browser, not an API key:
    a leaked key should not be able to change the password or mint more keys."""
    if user.via_api_key:
        raise error(403, "session_required", "This action is not available to API keys.")
    return user


async def admin_user(user: User = Depends(session_user)) -> User:
    if not user.is_admin:
        raise error(403, "forbidden", "Administrator access is required.")
    return user


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,  # not readable by page scripts
        samesite="lax",  # not sent on cross-site POSTs
        secure=settings.cookie_secure,
        path="/",
    )


async def profile(request: Request, user: User) -> dict[str, Any]:
    return {"user": user.public(), "usage": await service(request).usage(user)}


# --------------------------------------------------------------------- auth
@router.get("/plans")
async def list_plans() -> list[dict[str, Any]]:
    return plans_as_dicts()


@router.post("/auth/signup", status_code=201)
async def signup(body: SignupRequest, request: Request, response: Response) -> dict[str, Any]:
    try:
        user, token = await auth(request).signup(body.email, body.name, body.password)
    except AuthError as exc:
        raise auth_error(exc)
    set_session_cookie(request, response, token)
    return await profile(request, user)


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    client = request.client.host if request.client else ""
    try:
        user, token = await auth(request).login(body.email, body.password, client)
    except AuthError as exc:
        raise auth_error(exc)
    set_session_cookie(request, response, token)
    return await profile(request, user)


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    await auth(request).logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    return await profile(request, user)


@router.post("/auth/password")
async def change_password(
    body: PasswordChange, request: Request, response: Response, user: User = Depends(session_user)
) -> dict[str, bool]:
    try:
        token = await auth(request).change_password(user, body.current_password, body.new_password)
    except AuthError as exc:
        raise auth_error(exc)
    set_session_cookie(request, response, token)
    return {"ok": True}


@router.delete("/account")
async def delete_account(
    body: AccountDelete, request: Request, response: Response, user: User = Depends(session_user)
) -> dict[str, bool]:
    try:
        await auth(request).delete_account(user, body.password)
    except AuthError as exc:
        raise auth_error(exc)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


# ----------------------------------------------------------------- API keys
@router.get("/keys")
async def list_keys(request: Request, user: User = Depends(session_user)) -> list[dict[str, Any]]:
    return await auth(request).list_keys(user)


@router.post("/keys", status_code=201)
async def create_key(body: ApiKeyCreate, request: Request, user: User = Depends(session_user)) -> dict[str, Any]:
    try:
        return await auth(request).create_key(user, body.name)
    except AuthError as exc:
        raise auth_error(exc)


@router.delete("/keys/{key_id}")
async def delete_key(key_id: int, request: Request, user: User = Depends(session_user)) -> dict[str, bool]:
    if not await auth(request).delete_key(user, key_id):
        raise error(404, "not_found", "Unknown API key.")
    return {"ok": True}


# ----------------------------------------------------------------- research
@router.post("/research", response_model=ResearchResponse, status_code=202)
async def create_research(
    body: ResearchCreate, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    """Submit a research request. Returns immediately; poll GET /api/research/{id}."""
    try:
        created, duplicate = await service(request).submit(
            user, body.query, require_approval=body.require_approval, force_new=body.force_new
        )
    except InputValidationError as exc:
        raise error(400, exc.code, exc.message)
    except LimitExceeded as exc:
        raise error(exc.status, exc.code, exc.message, exc.retry_after)
    return {**created, "duplicate": duplicate}


@router.get("/research")
async def list_research(
    request: Request, limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user)
) -> list[dict[str, Any]]:
    return await service(request).list(user, limit)


@router.get("/research/{request_id}", response_model=ResearchResponse)
async def get_research(request_id: str, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    try:
        return await service(request).get(user, request_id)
    except NotFound:
        raise error(404, "not_found", "Unknown request id.")


@router.post("/research/{request_id}/approve", response_model=ResearchResponse)
async def approve_research(
    request_id: str, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    """Human approval: start a request that is waiting on its plan."""
    try:
        return await service(request).approve(user, request_id)
    except NotFound:
        raise error(404, "not_found", "Unknown request id.")
    except InvalidState as exc:
        raise error(409, "invalid_state", str(exc))


@router.get("/agents")
async def list_agents(request: Request, _user: User = Depends(current_user)) -> dict[str, Any]:
    """Agents discovered through their Agent Cards, plus any that are unreachable."""
    return await service(request).orchestrator.discover()


@router.get("/security/events")
async def security_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    scope: str = Query("mine", pattern="^(mine|all)$"),
    user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    """Your own events. Administrators may pass scope=all."""
    return await service(request).security_events(user, limit, everyone=scope == "all")


@router.get("/metrics")
async def metrics(
    request: Request,
    scope: str = Query("mine", pattern="^(mine|all)$"),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await service(request).metrics(user, everyone=scope == "all")


# -------------------------------------------------------------------- admin
@router.get("/admin/users")
async def admin_users(request: Request, _admin: User = Depends(admin_user)) -> list[dict[str, Any]]:
    return await auth(request).list_users()


@router.patch("/admin/users/{user_id}")
async def admin_update_user(
    user_id: str, body: AdminUserUpdate, request: Request, admin: User = Depends(admin_user)
) -> dict[str, Any]:
    try:
        return await auth(request).admin_update(admin, user_id, body.model_dump())
    except AuthError as exc:
        raise auth_error(exc)
