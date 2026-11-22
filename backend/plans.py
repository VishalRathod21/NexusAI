"""Subscription plans and their limits.

A plan is a set of limits enforced by the backend. Billing is deliberately not
part of this module: which plan an account is on is stored on the user, and
changing it is an admin action. Connecting a payment provider would mean
updating `user.plan` from that provider's webhook; nothing else changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    description: str
    reports_per_month: int
    concurrent_runs: int
    submissions_per_minute: int
    api_keys: int


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        description="Try the platform on a few research questions.",
        reports_per_month=5,
        concurrent_runs=1,
        submissions_per_minute=3,
        api_keys=1,
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        description="For individuals who research regularly.",
        reports_per_month=100,
        concurrent_runs=3,
        submissions_per_minute=10,
        api_keys=5,
    ),
    "team": Plan(
        id="team",
        name="Team",
        description="Higher limits for heavy and automated use.",
        reports_per_month=500,
        concurrent_runs=6,
        submissions_per_minute=30,
        api_keys=20,
    ),
}
DEFAULT_PLAN = "free"


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN])


def plans_as_dicts() -> list[dict[str, Any]]:
    return [asdict(plan) for plan in PLANS.values()]
