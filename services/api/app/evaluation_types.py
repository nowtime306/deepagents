"""Bounded private retrieval datasets and explicit paid-experiment admission."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    revision: int = Field(ge=1)
    chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=1000)
    category: Literal["fact", "no_answer", "outdated", "conflict", "scanned", "table"] = "fact"
    unanswerable: bool = False
    expected: list[Evidence] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def evidence(self):
        if self.unanswerable == bool(self.expected) or (self.category == "no_answer" and not self.unanswerable):
            raise ValueError("Answerable questions require evidence; unanswerable questions require none")
        if len({item.document_id for item in self.expected}) != len(self.expected):
            raise ValueError("Repeated evidence documents")
        return self


class DatasetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    dataset_type: Literal["user", "synthetic"] = "user"
    questions: list[Question] = Field(min_length=1, max_length=50)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def identifiers(self):
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("Question identifiers must be unique")
        return self


class ExperimentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: UUID
    dataset_revision: int = Field(ge=1)
    request_id: UUID
    acknowledge_model_costs: bool = False
