"""Server-only attribution inherited by model callbacks inside a delegated graph."""

from contextvars import ContextVar

active_member: ContextVar[dict | None] = ContextVar("active_team_member", default=None)
