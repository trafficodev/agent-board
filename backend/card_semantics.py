from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


CardKind = Literal[
    "product_area",
    "feature",
    "requirement",
    "task",
    "bug",
    "test",
    "decision",
    "finding",
]
CatalogLifecycle = Literal["proposed", "active", "deprecated"]
EvidenceState = Literal["uncovered", "implemented", "verified", "partial"]
EvidenceKind = Literal["source", "test", "commit", "screenshot", "validation"]
DecisionState = Literal["proposed", "accepted", "rejected", "superseded"]
RelationshipType = Literal[
    "defines",
    "implements",
    "verifies",
    "depends_on",
    "fixes",
    "supersedes",
]

CATALOG_PARENT_KIND: dict[str, str | None] = {
    "product_area": None,
    "feature": "product_area",
    "requirement": "feature",
}


def sparse_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        compact = {
            key: sparse_value(item)
            for key, item in value.items()
        }
        return {
            key: item
            for key, item in compact.items()
            if item not in (None, "", [], {})
        }
    if isinstance(value, (list, tuple)):
        compact = [sparse_value(item) for item in value]
        return [item for item in compact if item not in (None, "", [], {})]
    return value


class CardOwnership(BaseModel):
    team: str = ""
    agent: str = ""
    repository: str = ""
    component: str = ""


class CardEvidence(BaseModel):
    kind: EvidenceKind
    locator: str
    revision: str = ""
    verified_at: str = ""
    state: EvidenceState | None = None

    @field_validator("locator")
    @classmethod
    def require_locator(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("evidence locator must not be empty")
        return value


class CardDecision(BaseModel):
    id: str
    text: str
    state: DecisionState = "proposed"
    rationale: str = ""
    decided_by: str = ""
    decided_at: str = ""
    supersedes: str = ""

    @field_validator("id", "text")
    @classmethod
    def require_identity_and_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("decision id and text must not be empty")
        return value


class CardSemantics(BaseModel):
    kind: CardKind | None = None
    catalog_lifecycle: CatalogLifecycle | None = None
    outcome: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    owning_surface: str = ""
    evidence_state: EvidenceState | None = None
    ownership: CardOwnership | None = None
    evidence: list[CardEvidence] = Field(default_factory=list)
    decisions: list[CardDecision] = Field(default_factory=list)

    @field_validator("acceptance_criteria", "exclusions")
    @classmethod
    def remove_empty_statements(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @model_validator(mode="after")
    def validate_kind_contract(self):
        catalog_kinds = {"product_area", "feature", "requirement"}
        if self.catalog_lifecycle and self.kind not in catalog_kinds:
            raise ValueError("catalog_lifecycle is only valid for catalog card kinds")
        if self.kind == "requirement":
            if not self.outcome.strip():
                raise ValueError("requirement semantics require an outcome")
            if not [item for item in self.acceptance_criteria if item.strip()]:
                raise ValueError("requirement semantics require acceptance criteria")
        return self

    def sparse_dump(self) -> dict[str, Any]:
        return sparse_value(self)


def valid_semantic_parent(
    semantics: CardSemantics,
    parent_semantics: CardSemantics | None,
) -> bool:
    kind = semantics.kind
    if kind not in CATALOG_PARENT_KIND:
        return parent_semantics is None
    expected = CATALOG_PARENT_KIND[kind]
    return (
        parent_semantics is None
        if expected is None
        else parent_semantics is not None and parent_semantics.kind == expected
    )
