import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { filterCardsForBoard, parseSearchQuery, requiresBackendSearch, sortCards } from "./src/boardSearch.js";

const columns = [
  { id: "features", name: "Features", position: 0 },
  { id: "bugs", name: "Bug Reports", position: 1 },
];

const card = (overrides) => ({
  id: overrides.id,
  title: overrides.title ?? "",
  body: overrides.body ?? "",
  column_id: overrides.column_id ?? "features",
  parent_id: overrides.parent_id ?? null,
  position: overrides.position ?? 0,
  priority: overrides.priority ?? "medium",
  labels: overrides.labels ?? [],
  session_history: overrides.session_history ?? [],
  created_at: overrides.created_at ?? "2026-01-01T00:00:00Z",
  updated_at: overrides.updated_at ?? "2026-01-01T00:00:00Z",
});

const search = (cards, query, priority = "all", label = "all", sort = "board") =>
  filterCardsForBoard(cards, columns, { query, priority, label, sort }).cards.map((item) => item.id);

describe("board search", () => {
  it("parses phrases, fields, and negation", () => {
    assert.deepEqual(parseSearchQuery('title:"session data" -label:bug has:file'), [
      { negative: false, field: "title", value: "session data" },
      { negative: true, field: "label", value: "bug" },
      { negative: false, field: "has", value: "file" },
    ]);
  });

  it("matches plain terms across title, body, labels, column, and sessions", () => {
    const cards = [
      card({ id: "a", title: "Render session data", labels: ["feature"] }),
      card({ id: "b", body: "diagnose disappearing rows" }),
      card({ id: "c", labels: ["bug_report"] }),
      card({ id: "d", column_id: "bugs" }),
      card({ id: "e", session_history: [{ session_id: "sid-123", system: "codex", action: "sync", outcome: "success", timestamp: "now" }] }),
    ];

    assert.deepEqual(search(cards, "session"), ["a", "e"]);
    assert.deepEqual(search(cards, "bug_report"), ["c"]);
    assert.deepEqual(search(cards, "bug reports"), ["d"]);
    assert.deepEqual(search(cards, "sid-123"), ["e"]);
  });

  it("matches scoped filters and has predicates", () => {
    const cards = [
      card({ id: "file", body: "edited_files: frontend/src/App.tsx" }),
      card({ id: "commit", body: "git_commits: abc1234 Fix display" }),
      card({ id: "high", priority: "high", labels: ["requirement"], title: "Robust display" }),
    ];

    assert.deepEqual(search(cards, "has:file"), ["file"]);
    assert.deepEqual(search(cards, "has:commit"), ["commit"]);
    assert.deepEqual(search(cards, "priority:high label:requirement title:display"), ["high"]);
  });

  it("matches specific files and commits", () => {
    const cards = [
      card({ id: "file", body: 'edited_files: ["frontend/src/App.tsx"]' }),
      card({ id: "commit", body: 'git_commits: ["abc1234 Fix search"]' }),
      card({ id: "other", body: 'edited_files: ["backend/main.py"]\ngit_commits: ["def5678 Other"]' }),
    ];

    assert.deepEqual(search(cards, 'file:"src/App.tsx"'), ["file"]);
    assert.deepEqual(search(cards, "commit:abc1234"), ["commit"]);
    assert.deepEqual(search(cards, "file:backend commit:def5678"), ["other"]);
  });

  it("marks contains queries as backend searches", () => {
    assert.equal(requiresBackendSearch("contains:renderSessionData"), true);
    assert.equal(requiresBackendSearch("content:renderSessionData"), true);
    assert.equal(requiresBackendSearch("metadata:assistant-turn-1"), true);
    assert.equal(requiresBackendSearch("file:App.tsx"), true);
    assert.equal(requiresBackendSearch("has:file"), true);
    assert.equal(requiresBackendSearch("has:commit"), true);
    assert.equal(requiresBackendSearch("has:worktree"), true);
    assert.equal(requiresBackendSearch("has:edge"), true);
    assert.equal(requiresBackendSearch("title:/urgent/i"), true);
    assert.equal(requiresBackendSearch("title:urgent"), false);
  });

  it("sorts by board order, update time, priority, title, and sessions", () => {
    const cards = [
      card({ id: "low", title: "Zulu", position: 0, priority: "low", updated_at: "2026-01-01T00:00:00Z", session_history: [] }),
      card({ id: "critical", title: "Alpha", position: 1, priority: "critical", updated_at: "2026-01-03T00:00:00Z", session_history: [{ session_id: "1" }, { session_id: "2" }] }),
      card({ id: "high", title: "Beta", position: 2, priority: "high", updated_at: "2026-01-02T00:00:00Z", session_history: [{ session_id: "1" }] }),
    ];

    assert.deepEqual(search(cards, ""), ["low", "critical", "high"]);
    assert.deepEqual(search(cards, "", "all", "all", "updated_desc"), ["critical", "high", "low"]);
    assert.deepEqual(search(cards, "", "all", "all", "priority_desc"), ["critical", "high", "low"]);
    assert.deepEqual(search(cards, "", "all", "all", "title_asc"), ["critical", "high", "low"]);
    assert.deepEqual(search(cards, "", "all", "all", "sessions_desc"), ["critical", "high", "low"]);
  });

  it("preserves AI relevance order when requested", () => {
    const cards = [
      card({ id: "third", position: 2 }),
      card({ id: "first", position: 0 }),
      card({ id: "second", position: 1 }),
    ];

    assert.deepEqual(sortCards(cards, "ai_relevance").map((item) => item.id), ["third", "first", "second"]);
  });

  it("excludes negative terms", () => {
    const cards = [
      card({ id: "a", title: "session data backend" }),
      card({ id: "b", title: "session data frontend" }),
    ];

    assert.deepEqual(search(cards, "session -frontend"), ["a"]);
  });

  it("keeps ancestors visible when a child matches", () => {
    const cards = [
      card({ id: "feature", title: "Session rendering", position: 0 }),
      card({ id: "requirement", title: "Collapse should show latest event", parent_id: "feature", position: 1 }),
    ];

    assert.deepEqual(search(cards, "latest event"), ["feature", "requirement"]);
  });

  it("keeps descendants visible when a parent matches", () => {
    const cards = [
      card({ id: "feature", title: "Session rendering", position: 0 }),
      card({ id: "requirement", title: "Collapse should show latest event", parent_id: "feature", position: 1 }),
    ];

    assert.deepEqual(search(cards, "session rendering"), ["feature", "requirement"]);
  });
});
