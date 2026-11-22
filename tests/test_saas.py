"""Multi-user behaviour: accounts, sessions, tenant isolation, plan limits,
API keys, admin, and service-to-service authentication."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.auth import SESSION_COOKIE, hash_password, verify_password
from backend.main import create_app
from backend.plans import PLANS
from backend.rate_limit import RateLimiter
from database.connection import Database
from tests.conftest import QUERY, RouterTransport

PASSWORD = "correct horse battery"
HEADERS = {"X-Requested-With": "nexusai"}  # what the web app sends on every call
OTHER_QUERY = "We plan to build payroll and compliance software for small businesses in India."


@pytest.fixture
def app(settings, local_orchestrator):
    return create_app(settings, orchestrator=local_orchestrator)


def new_client(app) -> TestClient:
    return TestClient(app, headers=HEADERS)


def signup(client: TestClient, email: str, password: str = PASSWORD) -> dict:
    response = client.post("/api/auth/signup", json={"email": email, "name": "Test", "password": password})
    assert response.status_code == 201, response.text
    return response.json()


def wait_done(client: TestClient, request_id: str) -> dict:
    for _ in range(200):
        body = client.get(f"/api/research/{request_id}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("request did not finish")


# ------------------------------------------------------------------ passwords
def test_password_hash_is_salted_and_verifiable():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and PASSWORD not in first  # salted, never stored in clear
    assert verify_password(PASSWORD, first) and not verify_password("wrong password!", first)
    assert not verify_password(PASSWORD, "not-a-hash")


# ------------------------------------------------------------------- sessions
def test_signup_login_logout(app):
    with new_client(app) as client:
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/research").status_code == 401

        profile = signup(client, "Ada@Example.com")
        assert profile["user"]["email"] == "ada@example.com"  # normalised
        assert profile["user"]["plan"] == "free" and profile["usage"]["reports_limit"] == 5
        assert "password" not in str(profile)

        cookie_header = client.post("/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}).headers["set-cookie"]
        assert "HttpOnly" in cookie_header and "samesite=lax" in cookie_header.lower()
        assert client.get("/api/auth/me").status_code == 200

        token = client.cookies.get(SESSION_COOKIE)
        assert client.post("/api/auth/logout").status_code == 200
        # The session is revoked on the server, not just removed from the browser.
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"email": "not-an-email", "password": PASSWORD}, "invalid_email"),
        ({"email": "a@example.com", "password": "short"}, "weak_password"),
        ({"email": "a@example.com", "password": "password123"}, "weak_password"),
    ],
)
def test_signup_validation(app, payload, code):
    with new_client(app) as client:
        response = client.post("/api/auth/signup", json=payload)
        assert response.status_code == 400 and response.json()["detail"]["code"] == code


def test_duplicate_email_and_wrong_password(app):
    with new_client(app) as client:
        signup(client, "ada@example.com")
        again = client.post("/api/auth/signup", json={"email": "ADA@example.com", "password": PASSWORD})
        assert again.status_code == 409

    with new_client(app) as client:
        wrong = client.post("/api/auth/login", json={"email": "ada@example.com", "password": "wrong password!"})
        unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
        # Same answer either way: the API does not reveal which emails exist.
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()


def test_login_is_rate_limited(app):
    with new_client(app) as client:
        signup(client, "ada@example.com")
    with new_client(app) as client:
        for _ in range(5):
            assert client.post("/api/auth/login", json={"email": "ada@example.com", "password": "bad password!"}).status_code == 401
        blocked = client.post("/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
        assert blocked.status_code == 429 and "retry-after" in blocked.headers


def test_password_change_signs_out_other_sessions(app):
    with new_client(app) as laptop, new_client(app) as phone:
        signup(laptop, "ada@example.com")
        assert phone.post("/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}).status_code == 200

        new_password = "a brand new passphrase"
        bad = laptop.post("/api/auth/password", json={"current_password": "nope nope nope", "new_password": new_password})
        assert bad.status_code == 401
        assert laptop.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": new_password}).status_code == 200

        assert laptop.get("/api/auth/me").status_code == 200  # this session was renewed
        assert phone.get("/api/auth/me").status_code == 401  # the other one was revoked
        assert phone.post("/api/auth/login", json={"email": "ada@example.com", "password": new_password}).status_code == 200


def test_state_changing_calls_need_the_csrf_header(app):
    with TestClient(app) as client:  # no X-Requested-With: what a forged cross-site form would send
        response = client.post("/api/auth/signup", json={"email": "a@example.com", "password": PASSWORD})
        assert response.status_code == 403 and response.json()["detail"]["code"] == "csrf"
        assert client.get("/health").headers["x-frame-options"] == "DENY"


# ----------------------------------------------------------- tenant isolation
def test_users_cannot_see_or_act_on_each_others_data(app):
    with new_client(app) as alice, new_client(app) as bob:
        signup(alice, "alice@example.com")
        signup(bob, "bob@example.com")

        created = alice.post("/api/research", json={"query": QUERY, "require_approval": True}).json()
        request_id = created["id"]

        assert bob.get(f"/api/research/{request_id}").status_code == 404  # not 403: no hint it exists
        assert bob.post(f"/api/research/{request_id}/approve").status_code == 404
        assert bob.get("/api/research").json() == []

        assert alice.post(f"/api/research/{request_id}/approve").status_code == 200
        assert wait_done(alice, request_id)["status"] == "completed"

        # Same question from Bob is his own request, not a "duplicate" of Alice's.
        mine = bob.post("/api/research", json={"query": QUERY}).json()
        assert mine["duplicate"] is False and mine["id"] != request_id
        wait_done(bob, mine["id"])

        assert len(alice.get("/api/research").json()) == len(bob.get("/api/research").json()) == 1
        assert alice.get("/api/metrics").json()["reports"] == 1
        alice_events = alice.get("/api/security/events").json()
        assert alice_events and all(e["request_id"] == request_id for e in alice_events)
        # A normal user asking for everyone's events still only gets their own.
        assert bob.get("/api/security/events?scope=all").json() == bob.get("/api/security/events").json()


# ---------------------------------------------------------------- plan limits
def test_monthly_quota_is_enforced(app, monkeypatch):
    monkeypatch.setitem(PLANS, "free", PLANS["free"].__class__(**{**PLANS["free"].__dict__, "reports_per_month": 1, "submissions_per_minute": 50}))
    with new_client(app) as client:
        signup(client, "ada@example.com")
        first = client.post("/api/research", json={"query": QUERY})
        assert first.status_code == 202
        wait_done(client, first.json()["id"])
        assert client.get("/api/auth/me").json()["usage"]["reports_remaining"] == 0

        # Re-submitting the same question is free: it returns the existing report.
        assert client.post("/api/research", json={"query": QUERY}).json()["duplicate"] is True

        over = client.post("/api/research", json={"query": OTHER_QUERY})
        assert over.status_code == 402 and over.json()["detail"]["code"] == "quota_exceeded"


def test_submission_rate_limit(app, monkeypatch):
    monkeypatch.setitem(PLANS, "free", PLANS["free"].__class__(**{**PLANS["free"].__dict__, "submissions_per_minute": 1}))
    with new_client(app) as client:
        signup(client, "ada@example.com")
        assert client.post("/api/research", json={"query": QUERY, "require_approval": True}).status_code == 202
        limited = client.post("/api/research", json={"query": OTHER_QUERY, "require_approval": True})
        assert limited.status_code == 429 and limited.json()["detail"]["code"] == "rate_limited"
        assert int(limited.headers["retry-after"]) >= 1


def test_rate_limiter_window():
    limiter = RateLimiter()
    assert limiter.allow("k", 2, 60) == 0 and limiter.allow("k", 2, 60) == 0
    assert limiter.allow("k", 2, 60) > 0  # third call inside the window must wait
    assert limiter.allow("other", 2, 60) == 0  # keys are independent
    limiter.reset("k")
    assert limiter.allow("k", 2, 60) == 0


# ------------------------------------------------------------------- API keys
def test_api_key_lifecycle(app):
    with new_client(app) as browser:
        signup(browser, "ada@example.com")
        created = browser.post("/api/keys", json={"name": "CI script"})
        assert created.status_code == 201
        key = created.json()["token"]
        assert key.startswith("nxs_")

        listed = browser.get("/api/keys").json()
        assert listed[0]["name"] == "CI script" and "token" not in listed[0]  # shown only once
        assert browser.post("/api/keys", json={"name": "second"}).status_code == 402  # free plan: 1 key

        # A script uses the key with no cookie and no CSRF header.
        with TestClient(app, headers={"Authorization": f"Bearer {key}"}) as script:
            assert script.get("/api/auth/me").json()["user"]["email"] == "ada@example.com"
            submitted = script.post("/api/research", json={"query": QUERY})
            assert submitted.status_code == 202
            wait_done(script, submitted.json()["id"])
            # ...but a key cannot manage the account it belongs to.
            assert script.post("/api/keys", json={"name": "x"}).status_code == 403
            assert script.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "whatever long one"}).status_code == 403

        assert browser.delete(f"/api/keys/{listed[0]['id']}").status_code == 200
        with TestClient(app, headers={"Authorization": f"Bearer {key}"}) as script:
            assert script.get("/api/auth/me").status_code == 401  # revoked

        with TestClient(app, headers={"Authorization": "Bearer nxs_made_up_key"}) as stranger:
            assert stranger.get("/api/research").status_code == 401


# ---------------------------------------------------------------------- admin
def test_admin_can_manage_accounts_and_others_cannot(app):
    with new_client(app) as admin, new_client(app) as member:
        assert signup(admin, "admin@example.com")["user"]["is_admin"] is True  # first account
        member_id = signup(member, "member@example.com")["user"]["id"]
        assert member.get("/api/auth/me").json()["user"]["is_admin"] is False

        assert member.get("/api/admin/users").status_code == 403
        assert member.patch(f"/api/admin/users/{member_id}", json={"plan": "team"}).status_code == 403

        users = admin.get("/api/admin/users").json()
        assert [u["email"] for u in users] == ["admin@example.com", "member@example.com"]
        assert all("password_hash" not in u for u in users)

        assert admin.patch(f"/api/admin/users/{member_id}", json={"plan": "nonsense"}).status_code == 400
        assert admin.patch(f"/api/admin/users/{member_id}", json={"plan": "pro"}).json()["plan"] == "pro"
        assert member.get("/api/auth/me").json()["usage"]["reports_limit"] == PLANS["pro"].reports_per_month

        # Disabling an account signs it out everywhere and blocks sign-in.
        assert admin.patch(f"/api/admin/users/{member_id}", json={"is_active": False}).status_code == 200
        assert member.get("/api/auth/me").status_code == 401
        assert member.post("/api/auth/login", json={"email": "member@example.com", "password": PASSWORD}).status_code == 401

        admin_id = users[0]["id"]
        locked_out = admin.patch(f"/api/admin/users/{admin_id}", json={"is_admin": False})
        assert locked_out.status_code == 409  # the last admin cannot remove themselves


def test_account_deletion_removes_everything(app):
    with new_client(app) as admin, new_client(app) as member:
        signup(admin, "admin@example.com")
        signup(member, "member@example.com")
        request_id = member.post("/api/research", json={"query": QUERY}).json()["id"]
        wait_done(member, request_id)

        assert member.request("DELETE", "/api/account", json={"password": "wrong password!"}).status_code == 401
        assert member.request("DELETE", "/api/account", json={"password": PASSWORD}).status_code == 200
        assert member.get("/api/auth/me").status_code == 401

        assert [u["email"] for u in admin.get("/api/admin/users").json()] == ["admin@example.com"]
        assert admin.get("/api/security/events?scope=all").json() == []
        assert admin.request("DELETE", "/api/account", json={"password": PASSWORD}).status_code == 409


# ------------------------------------------------------- internal service auth
async def test_agents_reject_calls_without_the_service_token(settings, servers):
    from agents.a2a_server import build_agent_app
    from agents.market_research.agent import MarketResearchAgent
    from protocols.a2a.client import A2AClient, A2AError
    from security import audit

    secured = settings.model_copy(update={"internal_api_token": SecretStr("test-internal-token-123456")})
    url = secured.market_agent_url
    apps = {url: build_agent_app(MarketResearchAgent(servers=servers, settings=secured), url)}

    def client(token):
        return A2AClient(http_client=httpx.AsyncClient(transport=RouterTransport(apps), timeout=30), token=token)

    # The card is public and announces that calls need a Bearer token.
    card = await client(None).fetch_card(url)
    assert "internal" in card.security_schemes

    for wrong in (None, "not-the-token"):
        with pytest.raises(A2AError):
            await client(wrong).send_task(card, {"query": QUERY})
    assert any(e.event_type == "unauthenticated_internal_call_rejected" for e in audit.recent())

    outcome = await client("test-internal-token-123456").send_task(card, {"query": QUERY})
    assert outcome.state == "completed"


# ------------------------------------------------------------ schema upgrades
async def test_database_from_an_older_version_is_backed_up_not_broken(tmp_path):
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as old, old:  # the pre-accounts schema: no user_id column
        old.execute("CREATE TABLE research_requests (id TEXT PRIMARY KEY, query TEXT, status TEXT)")
        old.execute("INSERT INTO research_requests VALUES ('r1', 'old question', 'completed')")

    db = Database(f"sqlite+aiosqlite:///{path.as_posix()}")
    await db.init()
    await db.close()

    backups = list(tmp_path.glob("old.backup-*.db"))
    assert len(backups) == 1  # the old data is kept
    with closing(sqlite3.connect(backups[0])) as kept:
        assert kept.execute("SELECT query FROM research_requests").fetchone() == ("old question",)
    with closing(sqlite3.connect(path)) as fresh:
        columns = {row[1] for row in fresh.execute("PRAGMA table_info(research_requests)")}
        assert "user_id" in columns
