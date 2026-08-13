from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
CatalogLifecycle = Literal["proposed", "active", "accepted", "deprecated"]
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
    "validates",
    "supports",
    "documents",
    "tests",
    "relates_to",
    "follows_up",
    "duplicates",
    "parent_of",
]

CATALOG_PARENT_KIND: dict[str, str | None] = {
    "product_area": None,
    "feature": "product_area",
    "requirement": "feature",
}
DELIVERY_KINDS = {"task", "bug", "test", "decision", "finding"}


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


class StrictSemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CardOwnership(StrictSemanticModel):
    team: str = ""
    agent: str = ""
    repository: str = ""
    component: str = ""


class CardEvidence(StrictSemanticModel):
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


class CardDecision(StrictSemanticModel):
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


class CardSemantics(StrictSemanticModel):
    kind: CardKind | None = None
    catalog_lifecycle: CatalogLifecycle | None = None
    outcome: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    owning_surface: str = ""
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
        if self.kind in catalog_kinds and not self.catalog_lifecycle:
            raise ValueError("catalog card kinds require catalog_lifecycle")
        if self.kind == "requirement":
            if not self.outcome.strip():
                raise ValueError("requirement semantics require an outcome")
            if not [item for item in self.acceptance_criteria if item.strip()]:
                raise ValueError("requirement semantics require acceptance criteria")
        decision_ids = [decision.id for decision in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("decision ids must be unique within a card")
        known = set(decision_ids)
        supersedes = {
            decision.id: decision.supersedes
            for decision in self.decisions
            if decision.supersedes
        }
        if any(target not in known for target in supersedes.values()):
            raise ValueError("decision supersedes must reference a decision on the same card")
        for decision_id in supersedes:
            visited: set[str] = set()
            current = decision_id
            while current in supersedes:
                if current in visited:
                    raise ValueError("decision supersession must be acyclic")
                visited.add(current)
                current = supersedes[current]
        return self

    def sparse_dump(self) -> dict[str, Any]:
        return sparse_value(self)


def valid_semantic_parent(
    semantics: CardSemantics,
    parent_semantics: CardSemantics | None,
) -> bool:
    """Whether ``semantics`` may sit under ``parent_semantics``.

    Two rules, and only two:

    * A catalog kind (``product_area``/``feature``/``requirement``) has one
      exact parent kind, so its place in the catalog tree is fixed.
    * Every other kind -- the delivery kinds, and a card with no kind at all --
      is refused only under a *catalog* parent. An absent parent, an untyped
      (kind-less) parent, or a delivery-kind parent are all fine.

    That second rule is deliberately permissive about untyped parents: a
    grouping card that carries no semantic kind is a container, not a catalog
    node, so giving one of its children a ``kind`` must not depend on the
    parent first being typed. This is decoupled from edges and from any linked
    remote -- setting a ``kind`` never implies an edge and is never gated on
    remote state.
    """
    kind = semantics.kind
    if kind in CATALOG_PARENT_KIND:
        expected = CATALOG_PARENT_KIND[kind]
        return (
            parent_semantics is None
            if expected is None
            else parent_semantics is not None and parent_semantics.kind == expected
        )
    if parent_semantics is None:
        return True
    parent_kind = parent_semantics.kind
    return parent_kind is None or parent_kind in DELIVERY_KINDS


def semantic_parent_reason(
    semantics: CardSemantics,
    parent_semantics: CardSemantics | None,
) -> str:
    """A caller-facing sentence for why ``valid_semantic_parent`` refused.

    Only meaningful when the pair is actually invalid; it names the card's kind
    and what parent that kind requires, so the error points at the real
    constraint (the semantic hierarchy) instead of a guess.
    """
    kind = semantics.kind
    if parent_semantics is None:
        parent_desc = "no parent"
    elif parent_semantics.kind is None:
        parent_desc = "a kind-less parent"
    else:
        parent_desc = f"a {parent_semantics.kind!r} parent"
    if kind in CATALOG_PARENT_KIND:
        expected = CATALOG_PARENT_KIND[kind]
        if expected is None:
            return (
                f"semantic hierarchy: a {kind!r} card must be top-level, "
                f"but it was given {parent_desc}"
            )
        return (
            f"semantic hierarchy: a {kind!r} card must sit under a {expected!r} "
            f"parent, but it was given {parent_desc}"
        )
    return (
        f"semantic hierarchy: a {kind!r} card cannot sit under {parent_desc} "
        f"(a delivery kind may only sit under another delivery kind, a "
        f"kind-less parent, or no parent)"
    )
