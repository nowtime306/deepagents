"""Closed declarative validator/metric parameter schemas; no executable configuration."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Count(Parameters):
    expected: int = Field(ge=0)


class Terminal(Parameters):
    expected: Literal["SUCCEEDED", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"]


class Artifact(Parameters):
    path: str = Field(min_length=2, max_length=240)

    @field_validator("path")
    @classmethod
    def virtual_path(cls, value):
        if (not value.startswith("/") or "\\" in value or "\x00" in value or ":" in value
                or any(part in {"", ".", ".."} for part in value[1:].split("/"))):
            raise ValueError("A canonical virtual artifact path is required")
        return value


class ArtifactDigest(Artifact):
    expected: str = Field(pattern=r"^[a-f0-9]{64}$")


class Output(Parameters):
    expected: JsonValue


class Tool(Parameters):
    name: str = Field(min_length=1, max_length=128)
    input_parameters: dict[str, JsonValue] | None = None
    output: JsonValue = None


class ToolMetric(Parameters):
    expected_tools: list[Tool] = Field(default_factory=list, max_length=100)
    compare_parameters: bool = True
    compare_output: bool = True
    exact: bool = True
    ordered: bool = False
    threshold: float = Field(default=1.0, ge=0, le=1)


# Forward-declared, reviewed A20 fixed-profile checks have no user-configurable code/parameters.
# Their absence from facts.REGISTERED is an explicit readiness blocker before any dispatch.
PENDING_PROFILE_CHECKS = {
    "automation_lifecycle", "citation_matches_source", "no_fabricated_citation", "forbidden_effect_count",
    "protected_tests", "exactly_one_receipt", "child_failure_propagates", "budget_no_later_effect",
    "unknown_no_replay", "export_boundaries", "business_version_matches", "branch_and_variables",
    "schema_rejects_no_effect", "approved_code_once", "all_branches_join", "timeout_error_branch",
}
SCHEMAS = {
    "operation_count": Count, "user_message_count": Count, "terminal_status": Terminal,
    "artifact_exists": Artifact, "artifact_sha256": ArtifactDigest, "output_equals": Output,
    "tool_correctness": ToolMetric, "task_completion": Parameters,
    **{name: Parameters for name in PENDING_PROFILE_CHECKS},
}


def validate_parameters(name: str, parameters: dict) -> dict:
    if name not in SCHEMAS:
        raise ValueError("Unregistered validator/metric ID")
    normalized = SCHEMAS[name].model_validate(parameters).model_dump(exclude_unset=True)
    json.dumps(normalized, allow_nan=False)  # Reject non-finite JSON at any nesting depth.
    return normalized
