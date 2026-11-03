"""Versioned, declarative execution and evidence contracts."""

import ipaddress
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @field_validator("schema_version", "version", mode="before", check_fields=False)
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Version must be an integer, not a boolean/coerced number")
        return value


class Profile(StrictModel):
    id: str
    version: int = Field(ge=1)


class Target(StrictModel):
    schema_version: Literal[1] = 1
    environment_id: str
    api_base_url: str
    platform_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    owner_id: str
    required_migrations: list[str]
    fixture_profile: Profile
    resource_bindings: dict[str, str] = Field(default_factory=dict)

    @field_validator("api_base_url")
    @classmethod
    def local_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "http" or parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise ValueError("An explicit loopback HTTP origin is required")
        if parsed.query or parsed.fragment or not parsed.port:
            raise ValueError("Origin must have an explicit port and no query")
        if parsed.hostname != "localhost" and not ipaddress.ip_address(parsed.hostname or "").is_loopback:
            raise ValueError("External evaluation targets are disabled")
        return value.rstrip("/")

    @field_validator("environment_id", "owner_id")
    @classmethod
    def identifier(cls, value: str) -> str:
        UUID(value)
        return value


class Check(StrictModel):
    registered_id: str
    version: Literal[1] = 1
    parameters: dict[str, Any] = Field(default_factory=dict)
    required: bool = True

    @model_validator(mode="after")
    def registered_parameters(self):
        from .check_schemas import validate_parameters

        self.parameters = validate_parameters(self.registered_id, self.parameters)
        return self


class Budget(StrictModel):
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_model_calls: int = Field(default=10, ge=0, le=100)
    max_tokens: int = Field(default=20000, ge=0, le=1000000)


class Case(StrictModel):
    case_id: str = Field(pattern=r"^AET-[AWF][0-9]{2}$")
    name: str
    mode: Literal["ask", "work", "workflow"]
    requirements: list[str]
    tags: list[str]
    input: str
    business_version: int | None = Field(default=None, ge=1)
    fixture_profile: Profile
    fixture_parameters: dict[str, Any] = Field(default_factory=dict)
    validators: list[Check] = Field(min_length=1)
    metrics: list[Check] = Field(default_factory=list)
    budgets: Budget = Field(default_factory=Budget)
    repeats: int = Field(default=1, ge=1, le=3)
    approval_policy: Literal["none", "reject", "approve"] = "none"
    dependencies: list[str] = Field(default_factory=list)


class Dataset(StrictModel):
    schema_version: Literal[1] = 1
    dataset_id: str
    dataset_version: int = Field(ge=1)
    cases: list[Case] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def unique_cases(cls, cases: list[Case]) -> list[Case]:
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("Duplicate case IDs cannot count as distinct tasks")
        return cases


class Result(StrictModel):
    registered_id: str
    status: Literal["PASS", "FAIL", "NOT_APPLICABLE", "ERROR", "NOT_SCORED", "BLOCKED"]
    required: bool = True
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    score: float | None = None


def outcome(facts: list[Result], metrics: list[Result], cleanup: str) -> str:
    """Required factual failure takes precedence over judge scores or missing evidence."""
    if any(row.required and row.status == "FAIL" for row in facts):
        return "FAIL"
    if not facts or any(row.required and row.status != "PASS" for row in facts + metrics):
        return "NEEDS_REVIEW"
    return "PASS" if cleanup == "CLEAN" else "NEEDS_REVIEW"
