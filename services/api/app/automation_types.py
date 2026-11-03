"""Strict inputs for published-agent schedules and durable batches."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schedule_clock import Calendar, instant, zone


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_calls: int = Field(default=12, ge=1, le=40)
    token_limit: int = Field(default=64000, ge=1024, le=80000)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    agent_version_id: UUID
    mode: Literal["ask", "work"] = "ask"
    budget: Budget = Field(default_factory=Budget)
    environment: Literal["coding", "browser"] = "coding"
    network_domains: list[str] = Field(default_factory=list, max_length=10)
    notify_channel_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def bounded(self):
        if self.mode == "ask" and (self.budget.model_calls > 12 or self.budget.token_limit > 64000):
            raise ValueError("Ask 单项上限为 12 次调用和 64000 Token")
        return self


class ScheduleInput(Target):
    kind: Literal["once", "interval", "cron", "webhook"] = "once"
    goal: str = Field(min_length=1, max_length=8000)
    timezone: str = Field(default="Asia/Shanghai", max_length=80)
    starts_at: datetime
    interval_seconds: int | None = Field(default=None, ge=60, le=31536000)
    cron: str = Field(default="", max_length=120)
    missed_policy: Literal["skip", "once"] = "skip"
    overlap_policy: Literal["skip", "queue"] = "skip"
    webhook_hourly_limit: int = Field(default=60, ge=1, le=1000)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def cadence(self):
        zone(self.timezone)
        self.starts_at = instant(self.starts_at, self.timezone)
        if self.kind == "interval" and self.interval_seconds is None:
            raise ValueError("固定间隔必须填写秒数")
        if self.kind == "cron":
            Calendar.parse(self.cron)
        return self


class BatchInput(Target):
    goals: list[str] = Field(min_length=1, max_length=20)
    max_parallel: int = Field(default=2, ge=1, le=2)
    on_failure: Literal["pause", "continue"] = "pause"
    model_call_limit: int = Field(default=60, ge=1, le=800)
    token_limit: int = Field(default=128000, ge=1024, le=1600000)

    @model_validator(mode="after")
    def goals_valid(self):
        self.goals = [goal.strip() for goal in self.goals]
        if any(not goal or len(goal) > 8000 for goal in self.goals):
            raise ValueError("每项目标必须为 1 至 8000 个字符")
        return self
