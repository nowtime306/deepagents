"""Finite declarative Agent evaluation metadata; no evaluation runtime import or executable fields."""

import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictInt, field_validator, model_validator


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Profile(Strict):
    id: str = Field(min_length=1, max_length=80)
    version: StrictInt = Field(ge=1)


class Parameters(Strict):
    pass


class Count(Parameters):
    expected: StrictInt = Field(ge=0)


class Terminal(Parameters):
    expected: Literal["SUCCEEDED", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"]


class Artifact(Parameters):
    path: str = Field(min_length=2, max_length=240)

    @field_validator("path")
    @classmethod
    def virtual(cls, value):
        if (
            not value.startswith("/")
            or "\\" in value
            or "\x00" in value
            or ":" in value
            or any(part in {"", ".", ".."} for part in value[1:].split("/"))
        ):
            raise ValueError("Only canonical virtual artifact paths are allowed")
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
    compare_parameters: StrictBool = True
    compare_output: StrictBool = True
    exact: StrictBool = True
    ordered: StrictBool = False
    threshold: float = Field(default=1, ge=0, le=1, strict=True)


PROFILE_CHECKS = {
    "automation_lifecycle",
    "citation_matches_source",
    "no_fabricated_citation",
    "forbidden_effect_count",
    "protected_tests",
    "exactly_one_receipt",
    "child_failure_propagates",
    "budget_no_later_effect",
    "unknown_no_replay",
    "export_boundaries",
    "business_version_matches",
    "branch_and_variables",
    "schema_rejects_no_effect",
    "approved_code_once",
    "all_branches_join",
    "timeout_error_branch",
}
PARAMETERS = {
    "operation_count": Count,
    "user_message_count": Count,
    "terminal_status": Terminal,
    "artifact_exists": Artifact,
    "artifact_sha256": ArtifactDigest,
    "output_equals": Output,
    "tool_correctness": ToolMetric,
    "task_completion": Parameters,
    **{name: Parameters for name in PROFILE_CHECKS},
}


class Check(Strict):
    registered_id: str
    version: StrictInt = Field(default=1, ge=1, le=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    required: StrictBool = True

    @model_validator(mode="after")
    def registered(self):
        schema = PARAMETERS.get(self.registered_id)
        if schema is None:
            raise ValueError("Unregistered declarative check")
        self.parameters = schema.model_validate(self.parameters).model_dump(exclude_unset=True)
        json.dumps(self.parameters, allow_nan=False)
        return self


class CaseBudget(Strict):
    timeout_seconds: StrictInt = Field(default=120, ge=1, le=3600)
    max_model_calls: StrictInt = Field(default=10, ge=0, le=100)
    max_tokens: StrictInt = Field(default=20000, ge=0, le=1000000)


class Case(Strict):
    case_id: str = Field(pattern=r"^AET-[AWF][0-9]{2}$")
    name: str = Field(min_length=1, max_length=240)
    mode: Literal["ask", "work", "workflow"]
    requirements: list[str] = Field(max_length=100)
    tags: list[str] = Field(max_length=100)
    input: str = Field(min_length=1, max_length=16000)
    business_version: StrictInt | None = Field(default=None, ge=1)
    fixture_profile: Profile
    fixture_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    validators: list[Check] = Field(min_length=1, max_length=30)
    metrics: list[Check] = Field(default_factory=list, max_length=30)
    budgets: CaseBudget = Field(default_factory=CaseBudget)
    repeats: StrictInt = Field(default=1, ge=1, le=3)
    approval_policy: Literal["none", "reject", "approve"] = "none"
    dependencies: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def mode_and_payload(self):
        if self.case_id[4] != {"ask": "A", "work": "W", "workflow": "F"}[self.mode]:
            raise ValueError("Case identity and business mode differ")
        if len(json.dumps(self.fixture_parameters, allow_nan=False).encode()) > 64000:
            raise ValueError("Fixture data exceeds its bound")
        return self


class Definition(Strict):
    schema_version: StrictInt = Field(default=1, ge=1, le=1)
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_version: StrictInt = Field(ge=1)
    cases: list[Case] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def closed(self):
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case identity")
        encoded = json.dumps(self.model_dump(mode="json"), allow_nan=False)
        if len(encoded.encode()) > 2 * 1024 * 1024:
            raise ValueError("Dataset exceeds the import bound")
        if re.search(r"\bsk-[a-zA-Z0-9_-]{20,}", encoded):
            raise ValueError("Provider credentials cannot be imported")
        return self


class DatasetInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    revision: StrictInt = Field(ge=0)
    definition: Definition


class PublishInput(Strict):
    revision: StrictInt = Field(ge=1)
    notes: str = Field(default="", max_length=2000)


class RevisionInput(Strict):
    revision: StrictInt = Field(ge=1)


class RunBudget(Strict):
    max_seconds: StrictInt = Field(ge=1, le=86400)
    max_model_calls: StrictInt = Field(ge=0, le=10000)
    max_tokens: StrictInt = Field(ge=0, le=100000000)
    max_sandboxes: StrictInt = Field(ge=0, le=600)


class RunInput(Strict):
    dataset_id: UUID
    version: StrictInt = Field(ge=1)
    repeats: StrictInt = Field(default=1, ge=1, le=3)
    request_id: UUID
    budgets: RunBudget | None = None
    execution_mode: Literal["engineering", "effect"] = "engineering"
    acceptance_target: Literal["engineering", "b100"] = "engineering"
    version_set_id: UUID | None = None

    @model_validator(mode="after")
    def repetitions(self):
        if self.repeats != (1 if self.execution_mode == "engineering" else 3):
            raise ValueError("Engineering is explicit single execution; effect mode requires three independent repeats")
        return self


class ObservedModel(Strict):
    model_id: str = Field(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f]+$")
    effect_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    protocol: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")

    @field_validator("model_id")
    @classmethod
    def nonempty_model_id(cls, value):
        if not value.strip():
            raise ValueError("Actual model identity cannot be empty")
        return value


class ModelIdentity(Strict):
    complete: StrictBool
    models: list[ObservedModel] = Field(max_length=100)

    @model_validator(mode="after")
    def complete_models(self):
        if self.complete is not True or len({row.model_id for row in self.models}) != len(self.models):
            raise ValueError("Actual model observations are incomplete or repeated")
        return self


class ObservedBusiness(Strict):
    kind: Literal["agent", "workflow"]
    resource_id: UUID
    version: StrictInt = Field(ge=1)
    definition_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class BusinessIdentity(Strict):
    complete: StrictBool
    resources: list[ObservedBusiness] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def complete_resources(self):
        keys = {(row.kind, row.resource_id, row.version) for row in self.resources}
        if self.complete is not True or len(keys) != len(self.resources):
            raise ValueError("Actual business versions are incomplete or repeated")
        return self


class PlatformIdentity(Strict):
    complete: StrictBool
    platform_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_lock_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def complete_source(self):
        if self.complete is not True:
            raise ValueError("Actual platform source is incomplete")
        return self


class ComparedSample(Strict):
    case_id: str = Field(pattern=r"^AET-[AWF][0-9]{2}$")
    repeat_index: StrictInt = Field(ge=1, le=3)
    task_outcome: Literal["PASS", "FAIL", "NEEDS_REVIEW", "NOT_RUN"]


class ComparisonMaterial(Strict):
    dataset_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    rules_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    material_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    repeats: StrictInt = Field(ge=1, le=3)
    execution_mode: Literal["engineering", "effect"]
    model_identity: ModelIdentity
    business_identity: BusinessIdentity
    platform_identity: PlatformIdentity
    comparison_ready: StrictBool
    evidence_complete: StrictBool
    samples: list[ComparedSample] = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def exact_independent_repeats(self):
        if self.repeats != (1 if self.execution_mode == "engineering" else 3):
            raise ValueError("Mode and repeat count differ")
        pairs = {(row.case_id, row.repeat_index) for row in self.samples}
        expected = {(row.case_id, repeat) for row in self.samples for repeat in range(1, self.repeats + 1)}
        if len(pairs) != len(self.samples) or pairs != expected:
            raise ValueError("Every task requires its exact independent repeat set")
        return self


class ComparisonInput(Strict):
    baseline_run_id: UUID
    candidate_run_id: UUID
    changed_axis: Literal["auto", "model", "business_version", "platform"] = "auto"
    notes: str = Field(default="", max_length=2000)


class ReviewInput(Strict):
    revision: StrictInt = Field(ge=1)
    decision: Literal["CONFIRMED", "DISPUTED", "NEEDS_MORE_EVIDENCE"]
    reason: str = Field(min_length=1, max_length=2000)
    rules_version: str = Field(min_length=1, max_length=120)


class Material(Strict):
    input: str = Field(min_length=1, max_length=16000)
    expected_behavior: str = Field(min_length=1, max_length=16000)


class CandidateInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    source_sample_id: UUID | None = None
    authorization_note: str = Field(min_length=1, max_length=2000)
    material: Material
    redaction_confirmed: StrictBool = False


class CandidateReview(Strict):
    revision: StrictInt = Field(ge=1)
    decision: Literal["APPROVED", "REJECTED"]
    reason: str = Field(min_length=1, max_length=2000)


def coverage(definition: Definition) -> dict:
    counts = {mode: sum(case.mode == mode for case in definition.cases) for mode in ("ask", "work", "workflow")}
    return {
        "unique_case_ids": len(definition.cases),
        "mode_counts": counts,
        "a20_count_complete": counts == {"ask": 5, "work": 8, "workflow": 7},
        "b100_count_complete": counts == {"ask": 25, "work": 40, "workflow": 35},
        "semantic_review_status": "PENDING",
        "acceptance_complete": False,
    }


def duplicates(definition: Definition) -> list[list[str]]:
    groups = {}
    for case in definition.cases:
        content = case.model_dump(exclude={"case_id", "name", "requirements", "tags", "budgets", "repeats"})
        groups.setdefault(digest(content), []).append(case.case_id)
    return [group for group in groups.values() if len(group) > 1]


def sandbox_bound(case: Case) -> int:
    """Only the reviewed fixed F05 v2 material raises its reservation; admission still validates full parameters."""
    return 10 if (case.case_id == "AET-F05" and case.fixture_profile.id == "fixed-code-approval"
                  and case.fixture_profile.version == 2) else 2


def default_budget(definition: Definition, repeats: int) -> dict:
    return RunBudget(
        max_seconds=sum(c.budgets.timeout_seconds for c in definition.cases) * repeats,
        max_model_calls=sum(c.budgets.max_model_calls for c in definition.cases) * repeats,
        max_tokens=sum(c.budgets.max_tokens for c in definition.cases) * repeats,
        max_sandboxes=sum(sandbox_bound(case) for case in definition.cases) * repeats,
    ).model_dump()


def redact(text: str) -> tuple[str, list[str]]:
    removed = []
    patterns = [
        (
            "authentication_header",
            r"(?im)(?<![A-Za-z0-9_-])(?:proxy-authorization|authorization|set-cookie|cookie)\s*:\s*[^\r\n]*",
        ),
        ("credential", r"(?i)(?:bearer\s+|\bsk-)[A-Za-z0-9._~+/-]{8,}"),
        (
            "secret_assignment",
            "(?im)(?<![A-Za-z0-9_])(?:password|passwd|api[_-]?key|authorization|access[_-]?to"
            "ken|refresh[_-]?token|cookie)\\s*[:=]\\s*[^\\s,;]+",
        ),
        ("url_credentials", r"(?i)(https?://)[^/@\s]+:[^/@\s]+@"),
        ("query_secret", r"(?i)(?:[?&])(?:token|key|api_key|password|secret)=[^&\s]+"),
        ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ]
    for label, pattern in patterns:
        text, n = re.subn(pattern, "[已脱敏]", text)
        if n:
            removed.append(label)
    return text, removed


def candidate_preview(body: CandidateInput) -> dict:
    material = {}
    removed = []
    for key, text in body.material.model_dump().items():
        material[key], items = redact(text)
        removed.extend(key + ":" + item for item in items)
    return {
        "redacted_material": material,
        "material_digest": digest(material),
        "redaction_summary": {
            "removed_fields": sorted(set(removed)),
            "warnings": ["自动脱敏后仍须人工确认授权范围及剩余敏感内容。"],
        },
    }
