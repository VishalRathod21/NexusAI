"""Request/response models of the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(default="", max_length=120)
    password: str = Field(max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class AccountDelete(BaseModel):
    password: str = Field(max_length=200)


class ApiKeyCreate(BaseModel):
    name: str = Field(default="API key", max_length=80)


class AdminUserUpdate(BaseModel):
    plan: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class ResearchCreate(BaseModel):
    query: str = Field(
        max_length=4000,
        examples=[
            "I want to launch an AI-powered HR SaaS platform in India. Analyze the "
            "market, identify competitors, understand customer demand, and recommend "
            "a business strategy."
        ],
    )
    require_approval: bool = Field(
        default=False, description="Show the plan and wait for approval before running."
    )
    force_new: bool = Field(
        default=False, description="Run again even if the same request already exists."
    )


class ResearchResponse(BaseModel):
    id: str
    query: str
    status: str
    duplicate: bool = False
    plan: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
