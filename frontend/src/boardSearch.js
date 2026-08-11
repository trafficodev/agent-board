const FIELD_ALIASES = new Map([
  ["labels", "label"],
  ["type", "label"],
  ["files", "file"],
  ["edited_file", "file"],
  ["edited_files", "file"],
  ["commits", "commit"],
  ["git_commit", "commit"],
  ["git_commits", "commit"],
  ["worktrees", "worktree"],
  ["worktree_path", "worktree"],
  ["worktree_paths", "worktree"],
  ["content", "contains"],
  ["external", "external_id"],
  ["lifecycle", "catalog_lifecycle"],
  ["acceptance", "acceptance_criteria"],
  ["owner", "ownership"],
  ["decision", "decisions"],
]);

export const BOARD_SORTS = [
  { value: "board", label: "Board order" },
  { value: "updated_desc", label: "Recently updated" },
  { value: "created_desc", label: "Newest created" },
  { value: "priority_desc", label: "Priority" },
  { value: "title_asc", label: "Title" },
  { value: "sessions_desc", label: "Most sessions" },
];

const PRIORITY_RANK = new Map([
  ["critical", 0],
  ["high", 1],
  ["medium", 2],
  ["low", 3],
]);

// Card fields backed by a `<key>: [...]` line in the body, plus an optional
// freeform-text detector regex. Mirrors `_TRACKED_LIST_FIELDS` in
// backend/search_logic.py — keep both in sync when this changes.
const TRACKED_LIST_FIELDS = new Map([
  ["file", { bodyKey: "edited_files:", detector: /(^|\s|`)\/?[\w.-]+\/[\w./-]+/ }],
  ["commit", { bodyKey: "git_commits:", detector: /\b[0-9a-f]{7,40}\b/i }],
  ["worktree", { bodyKey: "worktrees:", detector: null }],
]);

export function filterCardsForBoard(cards, columns, filters) {
  const queryTerms = parseSearchQuery(filters.query ?? "");
  const columnNamesById = new Map(columns.map((column) => [column.id, column.name]));
  const cardsById = new Map(cards.map((card) => [card.id, card]));
  const childrenByParent = groupChildren(cards);

  const directMatches = new Set();
  for (const card of cards) {
    if (!matchesSelectFilters(card, filters)) continue;
    if (!matchesQuery(card, queryTerms, columnNamesById)) continue;
    directMatches.add(card.id);
  }

  if (queryTerms.length === 0 && filters.priority === "all" && filters.label === "all") {
    return {
      cards: sortCards(cards, filters.sort),
      directMatchIds: directMatches,
    };
  }

  const visibleIds = new Set(directMatches);
  for (const cardId of directMatches) {
    addAncestors(cardId, visibleIds, cardsById);
    addDescendants(cardId, visibleIds, childrenByParent);
  }

  return {
    cards: sortCards(cards.filter((card) => visibleIds.has(card.id)), filters.sort),
    directMatchIds: directMatches,
  };
}

export function sortCards(cards, sort = "board") {
  if (sort === "ai_relevance") return [...cards];
  return [...cards].sort((a, b) => compareCards(a, b, sort));
}

export function parseSearchQuery(query) {
  return tokenize(query).map((rawToken) => {
    let token = rawToken.trim();
    let negative = false;
    if (token.startsWith("-") && token.length > 1) {
      negative = true;
      token = token.slice(1);
    }

    const colonIndex = token.indexOf(":");
    if (colonIndex <= 0) {
      return { negative, field: null, value: normalize(token) };
    }

    const rawField = normalize(token.slice(0, colonIndex));
    const field = FIELD_ALIASES.get(rawField) ?? rawField;
    return { negative, field, value: normalize(token.slice(colonIndex + 1)) };
  }).filter((term) => term.value !== "");
}

function tokenize(query) {
  const tokens = [];
  let current = "";
  let quoted = false;

  for (const char of query.trim()) {
    if (char === "\"") {
      quoted = !quoted;
      continue;
    }
    if (/\s/.test(char) && !quoted) {
      if (current.trim()) tokens.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }

  if (current.trim()) tokens.push(current.trim());
  return tokens;
}

function matchesSelectFilters(card, filters) {
  if (filters.priority !== "all" && card.priority !== filters.priority) return false;
  if (filters.label !== "all" && !card.labels.includes(filters.label)) return false;
  return true;
}

function matchesQuery(card, terms, columnNamesById) {
  for (const term of terms) {
    const matched = matchesTerm(card, term, columnNamesById);
    if (term.negative && matched) return false;
    if (!term.negative && !matched) return false;
  }
  return true;
}

function matchesTerm(card, term, columnNamesById) {
  if (term.field === "has") return matchesHas(card, term.value);
  if (TRACKED_LIST_FIELDS.has(term.field)) return matchesTracked(card, term.field, term.value);
  if (term.field === "contains") return false;
  const values = searchValues(card, columnNamesById, term.field);
  return values.some((value) => normalize(value).includes(term.value));
}

export function requiresBackendSearch(query) {
  return parseSearchQuery(query).some((term) => {
    if (term.field === "contains" || term.field === "edge" || term.field === "edge_type") return true;
    if (term.field === "metadata" || TRACKED_LIST_FIELDS.has(term.field)) return true;
    if (term.field === "has") {
      const tracked = HAS_ALIASES.get(term.value);
      return Boolean(tracked || term.value === "edge" || term.value === "edges");
    }
    return /^\/.+\/[a-z]*$/i.test(term.value);
  });
}

function searchValues(card, columnNamesById, field) {
  const sessions = (card.session_history ?? []).flatMap((entry) => [
    "session",
    entry.session_id,
    entry.system,
    entry.action,
    entry.outcome ?? "",
    entry.timestamp,
  ]);

  const scoped = {
    external_id: [card.external_id ?? ""],
    title: [card.title],
    body: [card.body],
    label: card.labels,
    priority: [card.priority],
    column: [columnNamesById.get(card.column_id) ?? card.column_id],
    id: [card.id],
    session: sessions,
    semantics: semanticValues(card.semantics),
    kind: [card.semantics?.kind ?? ""],
    catalog_lifecycle: [card.semantics?.catalog_lifecycle ?? ""],
    outcome: [card.semantics?.outcome ?? ""],
    acceptance_criteria: card.semantics?.acceptance_criteria ?? [],
    exclusions: card.semantics?.exclusions ?? [],
    owning_surface: [card.semantics?.owning_surface ?? ""],
    evidence: semanticValues(card.semantics?.evidence),
    ownership: semanticValues(card.semantics?.ownership),
    decisions: semanticValues(card.semantics?.decisions),
  };

  if (field && scoped[field]) return scoped[field];

  return [
    card.id,
    card.title,
    card.body,
    card.priority,
    columnNamesById.get(card.column_id) ?? card.column_id,
    ...card.labels,
    ...sessions,
    ...semanticValues(card.semantics),
  ];
}

function semanticValues(value) {
  if (Array.isArray(value)) return value.flatMap(semanticValues);
  if (value && typeof value === "object") return Object.entries(value).flatMap(([key, item]) => [key, ...semanticValues(item)]);
  if (value === null || value === undefined) return [];
  return [String(value)];
}

const HAS_ALIASES = new Map([
  ["file", "file"], ["files", "file"], ["edited_file", "file"], ["edited_files", "file"],
  ["commit", "commit"], ["commits", "commit"], ["git_commit", "commit"], ["git_commits", "commit"],
  ["worktree", "worktree"], ["worktrees", "worktree"], ["worktree_path", "worktree"], ["worktree_paths", "worktree"],
]);

function matchesHas(card, value) {
  const field = HAS_ALIASES.get(value);
  if (field) return hasTracked(card, field);
  if (value === "session" || value === "sessions") return (card.session_history ?? []).length > 0;
  if (value === "kind") return Boolean(card.semantics?.kind);
  if (value === "lifecycle" || value === "catalog_lifecycle") return Boolean(card.semantics?.catalog_lifecycle);
  if (value === "outcome") return Boolean(card.semantics?.outcome);
  if (value === "exclusion" || value === "exclusions") return (card.semantics?.exclusions ?? []).length > 0;
  if (value === "surface" || value === "owning_surface") return Boolean(card.semantics?.owning_surface);
  if (value === "evidence") return (card.semantics?.evidence ?? []).length > 0;
  if (value === "decision" || value === "decisions") return (card.semantics?.decisions ?? []).length > 0;
  if (value === "ownership" || value === "owner") return semanticValues(card.semantics?.ownership).length > 0;
  if (value === "acceptance" || value === "acceptance_criteria") return (card.semantics?.acceptance_criteria ?? []).length > 0;
  return false;
}

function matchesTracked(card, field, value) {
  if (!hasTracked(card, field)) return false;
  return normalize(card.body).includes(value);
}

function hasTracked(card, field) {
  const { bodyKey, detector } = TRACKED_LIST_FIELDS.get(field);
  const body = normalize(card.body);
  if (body.includes(bodyKey)) return true;
  return Boolean(detector && detector.test(card.body));
}

function addAncestors(cardId, visibleIds, cardsById) {
  let current = cardsById.get(cardId);
  while (current?.parent_id) {
    const parent = cardsById.get(current.parent_id);
    if (!parent || visibleIds.has(parent.id)) return;
    visibleIds.add(parent.id);
    current = parent;
  }
}

function addDescendants(cardId, visibleIds, childrenByParent) {
  const children = childrenByParent.get(cardId) ?? [];
  for (const child of children) {
    if (visibleIds.has(child.id)) continue;
    visibleIds.add(child.id);
    addDescendants(child.id, visibleIds, childrenByParent);
  }
}

function groupChildren(cards) {
  const childrenByParent = new Map();
  for (const card of cards) {
    if (!card.parent_id) continue;
    const children = childrenByParent.get(card.parent_id) ?? [];
    children.push(card);
    childrenByParent.set(card.parent_id, children);
  }
  return childrenByParent;
}

function compareCards(a, b, sort = "board") {
  const tie = compareBoardOrder(a, b);
  if (sort === "updated_desc") return compareDateDesc(a.updated_at, b.updated_at) || tie;
  if (sort === "created_desc") return compareDateDesc(a.created_at, b.created_at) || tie;
  if (sort === "priority_desc") return comparePriority(a, b) || tie;
  if (sort === "title_asc") return normalize(a.title).localeCompare(normalize(b.title)) || tie;
  if (sort === "sessions_desc") return (b.session_history?.length ?? 0) - (a.session_history?.length ?? 0) || tie;
  return tie;
}

function compareBoardOrder(a, b) {
  if (a.column_id !== b.column_id) return a.column_id.localeCompare(b.column_id);
  return a.position - b.position;
}

function compareDateDesc(a, b) {
  const left = Date.parse(a ?? "");
  const right = Date.parse(b ?? "");
  if (Number.isNaN(left) && Number.isNaN(right)) return 0;
  if (Number.isNaN(left)) return 1;
  if (Number.isNaN(right)) return -1;
  return right - left;
}

function comparePriority(a, b) {
  return (PRIORITY_RANK.get(a.priority) ?? 99) - (PRIORITY_RANK.get(b.priority) ?? 99);
}

function normalize(value) {
  return String(value ?? "").toLowerCase();
}
