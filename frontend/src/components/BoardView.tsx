import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Board, CanvasSyncStatus, Card, Column, Edge, Event } from "../types";
import * as api from "../api";
import { groupSameColumnChildren, isColumnRoot } from "../boardLogic.js";
import { BOARD_SORTS, filterCardsForBoard, requiresBackendSearch, sortCards } from "../boardSearch.js";
import CardComponent from "./CardComponent";

interface Props {
  board: Board;
  cards: Card[];
  edges: Edge[];
  onRefresh: () => void;
  onBoardUpdated: (board: Board) => void;
}

const PRIORITIES: Card["priority"][] = ["critical", "high", "medium", "low"];
const DEFAULT_NEW_CARD = { title: "", body: "", priority: "medium" as Card["priority"], labels: "" };
const SEARCH_FIELDS = ["title", "body", "label", "priority", "column", "metadata", "session", "edge", "file", "commit", "worktree", "contains"] as const;
const DEFAULT_ADVANCED_FILTERS = {
  field: "title",
  value: "",
  hasFiles: false,
  hasCommits: false,
  hasSessions: false,
  hasEdges: false,
  hideLowPriority: false,
};

function quoteSearchValue(value: string) {
  return value.replaceAll("\"", " ");
}

export default function BoardView({ board, cards, edges, onRefresh, onBoardUpdated }: Props) {
  const [dragCardId, setDragCardId] = useState<string | null>(null);
  const [newCardCol, setNewCardCol] = useState<string | null>(null);
  const [newCard, setNewCard] = useState(DEFAULT_NEW_CARD);
  const [syncStatus, setSyncStatus] = useState<CanvasSyncStatus | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [backendSearchCards, setBackendSearchCards] = useState<Card[] | null>(null);
  const [searchError, setSearchError] = useState("");
  const [sortMode, setSortMode] = useState("board");
  const [priorityFilter, setPriorityFilter] = useState<"all" | Card["priority"]>("all");
  const [labelFilter, setLabelFilter] = useState("all");
  const [showAdvancedSearch, setShowAdvancedSearch] = useState(false);
  const [advancedFilters, setAdvancedFilters] = useState(DEFAULT_ADVANCED_FILTERS);
  const [aiCards, setAiCards] = useState<Card[] | null>(null);
  const [aiReasoning, setAiReasoning] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState("");
  const aiAbortRef = useRef<AbortController | null>(null);
  const [expandedCards, setExpandedCards] = useState(false);
  const [showEvents, setShowEvents] = useState(false);
  const [events, setEvents] = useState<Event[]>([]);
  const [eventsBusy, setEventsBusy] = useState(false);
  const [editingBoard, setEditingBoard] = useState(false);
  const [boardName, setBoardName] = useState(board.name);
  const [boardDescription, setBoardDescription] = useState(board.description);
  const [newColumnName, setNewColumnName] = useState("");
  const [editingColumnId, setEditingColumnId] = useState<string | null>(null);
  const [editingColumnName, setEditingColumnName] = useState("");

  const refreshSyncStatus = useCallback(async () => {
    const status = await api.getCanvasSync(board.id);
    setSyncStatus(status);
  }, [board.id]);

  useEffect(() => {
    void api.getCanvasSync(board.id).then(setSyncStatus);
  }, [board.id]);

  const labels = useMemo(
    () => Array.from(new Set(cards.flatMap((card) => card.labels))).sort(),
    [cards],
  );

  const effectiveSearch = useMemo(() => {
    const terms = [search.trim()];
    if (advancedFilters.value.trim()) terms.push(`${advancedFilters.field}:"${quoteSearchValue(advancedFilters.value.trim())}"`);
    if (advancedFilters.hasFiles) terms.push("has:file");
    if (advancedFilters.hasCommits) terms.push("has:commit");
    if (advancedFilters.hasSessions) terms.push("has:session");
    if (advancedFilters.hasEdges) terms.push("has:edge");
    if (advancedFilters.hideLowPriority) terms.push("-priority:low");
    return terms.filter(Boolean).join(" ");
  }, [advancedFilters, search]);

  const useBackendSearch = requiresBackendSearch(effectiveSearch);
  const sortOptions = aiCards ? [{ value: "ai_relevance", label: "AI relevance" }, ...BOARD_SORTS] : BOARD_SORTS;

  useEffect(() => {
    if (!useBackendSearch) return;

    let cancelled = false;
    void api.searchCards(board.id, {
      query: effectiveSearch,
      priority: priorityFilter === "all" ? undefined : priorityFilter,
      label: labelFilter === "all" ? undefined : labelFilter,
      sort: sortMode,
    }).then((result) => {
      if (cancelled) return;
      setBackendSearchCards(result);
      setSearchError("");
    }).catch((error: Error) => {
      if (cancelled) return;
      setBackendSearchCards([]);
      setSearchError(error.message);
    });

    return () => {
      cancelled = true;
    };
  }, [board.id, effectiveSearch, labelFilter, priorityFilter, sortMode, useBackendSearch]);

  const searchResult = useMemo(() => {
    if (aiCards) {
      const ids = new Set(aiCards.map((card) => card.id));
      return { cards: sortCards(aiCards, sortMode), directMatchIds: ids };
    }
    if (useBackendSearch) {
      const resultCards = sortCards(backendSearchCards ?? [], sortMode);
      return { cards: resultCards, directMatchIds: new Set(resultCards.map((card) => card.id)) };
    }
    return filterCardsForBoard(cards, board.columns, { query: effectiveSearch, priority: priorityFilter, label: labelFilter, sort: sortMode });
  }, [aiCards, backendSearchCards, board.columns, cards, effectiveSearch, labelFilter, priorityFilter, sortMode, useBackendSearch]);
  const visibleSearchError = useBackendSearch ? searchError : "";

  const subTasksByParent = useMemo(() => {
    return groupSameColumnChildren(searchResult.cards) as Map<string, Card[]>;
  }, [searchResult.cards]);

  const stats = useMemo(() => {
    const topLevel = cards.filter((card) => !card.parent_id);
    const doneColumn = board.columns.find((column) => /done|complete|shipped/i.test(column.name));
    const doneCards = doneColumn ? topLevel.filter((card) => card.column_id === doneColumn.id).length : 0;
    return {
      total: topLevel.length,
      subtasks: cards.length - topLevel.length,
      critical: topLevel.filter((card) => card.priority === "critical").length,
      done: doneCards,
    };
  }, [board.columns, cards]);

  const typeStats = useMemo(
    () => labels.map((label) => ({
      label,
      count: cards.filter((card) => card.labels.includes(label)).length,
    })),
    [cards, labels],
  );

  const cardsForColumn = (columnId: string) =>
    sortCards(
      searchResult.cards
        .filter((card) => card.column_id === columnId)
        .filter((card) => isColumnRoot(card, new Map(searchResult.cards.map((item) => [item.id, item])))),
      sortMode,
    ) as Card[];

  const hasAdvancedFilter = Object.entries(advancedFilters).some(([key, value]) => DEFAULT_ADVANCED_FILTERS[key as keyof typeof DEFAULT_ADVANCED_FILTERS] !== value);
  const hasActiveCardFilter = effectiveSearch.trim() !== "" || priorityFilter !== "all" || labelFilter !== "all" || aiCards !== null;

  const resetNewCard = () => setNewCard(DEFAULT_NEW_CARD);

  const refreshBoard = async () => {
    onBoardUpdated(await api.getBoard(board.id));
  };

  const refreshEvents = useCallback(async () => {
    setEventsBusy(true);
    try {
      setEvents(await api.getEvents(board.id));
    } finally {
      setEventsBusy(false);
    }
  }, [board.id]);

  useEffect(() => {
    if (showEvents) void api.getEvents(board.id).then(setEvents);
  }, [showEvents, board.id]);

  const handleDrop = async (columnId: string) => {
    if (!dragCardId) return;
    await api.moveCard(board.id, dragCardId, columnId);
    setDragCardId(null);
    onRefresh();
    refreshSyncStatus();
  };

  const handleCreateCard = async (columnId: string) => {
    if (!newCard.title.trim()) return;
    await api.createCard(board.id, {
      title: newCard.title.trim(),
      body: newCard.body.trim(),
      column_id: columnId,
      priority: newCard.priority,
      labels: newCard.labels.split(",").map((label) => label.trim()).filter(Boolean),
    });
    resetNewCard();
    setNewCardCol(null);
    onRefresh();
    refreshSyncStatus();
  };

  const handleDeleteCard = async (cardId: string) => {
    await api.deleteCard(board.id, cardId);
    onRefresh();
    refreshSyncStatus();
  };

  const handleMoveCard = async (cardId: string, columnId: string) => {
    await api.moveCard(board.id, cardId, columnId);
    onRefresh();
    refreshSyncStatus();
  };

  const handleSaveBoard = async () => {
    if (!boardName.trim()) return;
    const updated = await api.updateBoard(board.id, {
      name: boardName.trim(),
      description: boardDescription.trim(),
    });
    setEditingBoard(false);
    onBoardUpdated(updated);
    refreshSyncStatus();
  };

  const handleAddColumn = async () => {
    if (!newColumnName.trim()) return;
    await api.addColumn(board.id, newColumnName.trim());
    setNewColumnName("");
    await refreshBoard();
    refreshSyncStatus();
  };

  const handleSaveColumn = async (columnId: string) => {
    if (!editingColumnName.trim()) return;
    await api.updateColumn(board.id, columnId, { name: editingColumnName.trim() });
    setEditingColumnId(null);
    await refreshBoard();
    refreshSyncStatus();
  };

  const handleDeleteColumn = async (column: Column) => {
    if (board.columns.length <= 1) return;
    if (cards.some((card) => card.column_id === column.id) && !window.confirm(`Delete "${column.name}" and its cards?`)) return;
    await api.deleteColumn(board.id, column.id);
    await refreshBoard();
    onRefresh();
    refreshSyncStatus();
  };

  const handleSyncAction = async (action: "enable" | "disable" | "sync") => {
    setSyncBusy(true);
    try {
      if (action === "enable") setSyncStatus(await api.enableCanvasSync(board.id));
      if (action === "disable") setSyncStatus(await api.disableCanvasSync(board.id));
      if (action === "sync") setSyncStatus(await api.syncCanvas(board.id));
    } finally {
      setSyncBusy(false);
    }
  };

  const clearAiSearch = () => {
    aiAbortRef.current?.abort();
    aiAbortRef.current = null;
    setAiCards(null);
    setAiReasoning("");
    setAiError("");
    setAiLoading(false);
    setSortMode((current) => current === "ai_relevance" ? "board" : current);
  };

  const updateSearchState = (update: () => void) => {
    update();
    clearAiSearch();
  };

  const runAiSearch = async () => {
    const query = effectiveSearch.trim();
    if (!query) return;
    aiAbortRef.current?.abort();
    const ctrl = new AbortController();
    aiAbortRef.current = ctrl;
    setAiLoading(true);
    setAiError("");
    try {
      const result = await api.aiSearchCards(board.id, {
        query,
        priority: priorityFilter === "all" ? undefined : priorityFilter,
        label: labelFilter === "all" ? undefined : labelFilter,
        signal: ctrl.signal,
      });
      if (ctrl.signal.aborted) return;
      setAiCards(result.results);
      setAiReasoning(result.reasoning);
      setAiError(result.error ?? "");
      setSortMode("ai_relevance");
    } catch (error) {
      if (ctrl.signal.aborted) return;
      setAiCards([]);
      setAiReasoning("");
      setAiError(error instanceof Error ? error.message : "AI search failed");
    } finally {
      if (!ctrl.signal.aborted) setAiLoading(false);
    }
  };

  return (
    <div className="board-view">
      <div className="board-header">
        <div className="board-title-row">
          <div className="board-identity">
            {editingBoard ? (
              <div className="board-edit-panel">
                <input value={boardName} onChange={(e) => setBoardName(e.target.value)} />
                <textarea value={boardDescription} onChange={(e) => setBoardDescription(e.target.value)} rows={2} />
                <div className="inline-actions">
                  <button onClick={handleSaveBoard} disabled={!boardName.trim()}>Save</button>
                  <button onClick={() => {
                    setBoardName(board.name);
                    setBoardDescription(board.description);
                    setEditingBoard(false);
                  }}>Cancel</button>
                </div>
              </div>
            ) : (
              <>
                <div className="title-actions">
                  <h2>{board.name}</h2>
                  <button className="icon-button" onClick={() => setEditingBoard(true)} title="Edit board">✎</button>
                </div>
                {board.description && <p className="board-desc">{board.description}</p>}
              </>
            )}
          </div>

          <div className="canvas-sync-controls">
            {syncStatus?.enabled ? (
              <>
                <button onClick={() => handleSyncAction("sync")} disabled={syncBusy}>Sync now</button>
                <button onClick={() => handleSyncAction("disable")} disabled={syncBusy}>Auto sync on</button>
              </>
            ) : (
              <button onClick={() => handleSyncAction("enable")} disabled={syncBusy}>Auto sync Canvas</button>
            )}
            <button onClick={() => setShowEvents((value) => !value)}>{showEvents ? "Hide events" : "Events"}</button>
          </div>
        </div>

        <div className="board-command-bar">
          <div className="board-stats">
            <span>{stats.total} cards</span>
            <span>{stats.subtasks} subtasks</span>
            <span>{stats.critical} critical</span>
            <span>{stats.done} done</span>
          </div>
          <div className="board-filters">
            <input
              placeholder="Search cards"
              value={search}
              onChange={(e) => {
                updateSearchState(() => setSearch(e.target.value));
              }}
            />
            <button
              className={aiCards ? "active" : ""}
              onClick={runAiSearch}
              disabled={aiLoading || !effectiveSearch.trim()}
              title="Run AI search"
            >
              {aiLoading ? "Searching..." : "AI"}
            </button>
            <button
              className={showAdvancedSearch || hasAdvancedFilter ? "active" : ""}
              onClick={() => setShowAdvancedSearch((value) => !value)}
            >
              Advanced
            </button>
            <select value={sortMode} onChange={(e) => setSortMode(e.target.value)}>
              {sortOptions.map((sort) => <option key={sort.value} value={sort.value}>{sort.label}</option>)}
            </select>
            <select value={priorityFilter} onChange={(e) => updateSearchState(() => setPriorityFilter(e.target.value as "all" | Card["priority"]))}>
              <option value="all">All priorities</option>
              {PRIORITIES.map((priority) => <option key={priority} value={priority}>{priority}</option>)}
            </select>
            <select value={labelFilter} onChange={(e) => updateSearchState(() => setLabelFilter(e.target.value))}>
              <option value="all">All labels</option>
              {labels.map((label) => <option key={label} value={label}>{label}</option>)}
            </select>
            {hasActiveCardFilter && <span className="search-count">{searchResult.directMatchIds.size}/{cards.length}</span>}
            <button onClick={() => setExpandedCards((value) => !value)}>{expandedCards ? "Compact" : "Expand"}</button>
          </div>
        </div>
        {showAdvancedSearch && (
          <div className="advanced-search-panel">
            <label>
              Search in
              <select
                value={advancedFilters.field}
                onChange={(e) => updateSearchState(() => setAdvancedFilters((value) => ({ ...value, field: e.target.value })))}
              >
                {SEARCH_FIELDS.map((field) => <option key={field} value={field}>{field.replaceAll("_", " ")}</option>)}
              </select>
            </label>
            <input
              placeholder="Field value"
              value={advancedFilters.value}
              onChange={(e) => {
                updateSearchState(() => setAdvancedFilters((value) => ({ ...value, value: e.target.value })));
              }}
            />
            <label><input type="checkbox" checked={advancedFilters.hasFiles} onChange={(e) => updateSearchState(() => setAdvancedFilters((value) => ({ ...value, hasFiles: e.target.checked })))} /> Files</label>
            <label><input type="checkbox" checked={advancedFilters.hasCommits} onChange={(e) => updateSearchState(() => setAdvancedFilters((value) => ({ ...value, hasCommits: e.target.checked })))} /> Commits</label>
            <label><input type="checkbox" checked={advancedFilters.hasSessions} onChange={(e) => updateSearchState(() => setAdvancedFilters((value) => ({ ...value, hasSessions: e.target.checked })))} /> Sessions</label>
            <label><input type="checkbox" checked={advancedFilters.hasEdges} onChange={(e) => updateSearchState(() => setAdvancedFilters((value) => ({ ...value, hasEdges: e.target.checked })))} /> Edges</label>
            <label><input type="checkbox" checked={advancedFilters.hideLowPriority} onChange={(e) => updateSearchState(() => setAdvancedFilters((value) => ({ ...value, hideLowPriority: e.target.checked })))} /> Hide low</label>
            {hasAdvancedFilter && (
              <button
                onClick={() => {
                  setAdvancedFilters(DEFAULT_ADVANCED_FILTERS);
                  clearAiSearch();
                }}
              >
                Reset
              </button>
            )}
          </div>
        )}
        {(visibleSearchError || aiError || aiReasoning || aiCards) && (
          <div className={`search-status ${visibleSearchError || aiError ? "error" : ""}`}>
            {visibleSearchError || aiError || aiReasoning || (aiCards ? `${aiCards.length} AI matches` : "")}
            {aiCards && <button onClick={clearAiSearch}>Clear AI</button>}
          </div>
        )}

        <div className="type-bar">
          <button className={labelFilter === "all" ? "active" : ""} onClick={() => updateSearchState(() => setLabelFilter("all"))}>All types</button>
          {typeStats.map((item) => (
            <button
              key={item.label}
              className={labelFilter === item.label ? "active" : ""}
              onClick={() => updateSearchState(() => setLabelFilter(item.label))}
            >
              {item.label.replaceAll("_", " ")} <span>{item.count}</span>
            </button>
          ))}
          <div className="add-state-form">
            <input
              placeholder="New state"
              value={newColumnName}
              onChange={(e) => setNewColumnName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAddColumn()}
            />
            <button onClick={handleAddColumn} disabled={!newColumnName.trim()}>Add state</button>
          </div>
        </div>

        {syncStatus?.enabled && (
          <div className={`canvas-sync-status ${syncStatus.last_error ? "error" : ""}`}>
            {syncStatus.last_error
              ? syncStatus.last_error
              : syncStatus.last_synced_at
                ? `Last Canvas sync ${new Date(syncStatus.last_synced_at).toLocaleString()}`
                : "Canvas sync enabled"}
          </div>
        )}
      </div>

      {showEvents && (
        <aside className="events-panel">
          <div className="events-header">
            <strong>Activity</strong>
            <button onClick={refreshEvents} disabled={eventsBusy}>Refresh</button>
          </div>
          <div className="events-list">
            {events.length === 0 && <span className="muted">No events yet</span>}
            {events.map((event, index) => (
              <div className="event-row" key={`${event.timestamp}-${index}`}>
                <span>{new Date(event.timestamp).toLocaleString()}</span>
                <strong>{event.type.replaceAll("_", " ")}</strong>
                {event.detail && <p>{event.detail}</p>}
              </div>
            ))}
          </div>
        </aside>
      )}

      <div className="columns">
        {board.columns.map((column) => {
          const columnCards = cardsForColumn(column.id);
          const totalColumnCards = cards.filter((card) => card.column_id === column.id).length;
          const countLabel = hasActiveCardFilter ? `${columnCards.length}/${totalColumnCards}` : `${totalColumnCards}`;

          return (
            <div
              key={column.id}
              className={`column ${dragCardId ? "drop-ready" : ""}`}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => handleDrop(column.id)}
            >
              <div className="column-header">
                {editingColumnId === column.id ? (
                  <input
                    autoFocus
                    value={editingColumnName}
                    onChange={(e) => setEditingColumnName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveColumn(column.id);
                      if (e.key === "Escape") setEditingColumnId(null);
                    }}
                  />
                ) : (
                  <h3>{column.name}</h3>
                )}
                <span className="card-count">{countLabel}</span>
                <button
                  className="icon-button"
                  onClick={() => {
                    setNewCardCol(column.id);
                    resetNewCard();
                  }}
                  title="Add card"
                >
                  +
                </button>
                <button
                  className="icon-button"
                  onClick={() => {
                    setEditingColumnId(column.id);
                    setEditingColumnName(column.name);
                  }}
                  title="Rename column"
                >
                  ✎
                </button>
                <button className="icon-button danger" onClick={() => handleDeleteColumn(column)} title="Delete column">×</button>
              </div>

              {editingColumnId === column.id && (
                <div className="column-edit-actions">
                  <button onClick={() => handleSaveColumn(column.id)} disabled={!editingColumnName.trim()}>Save</button>
                  <button onClick={() => setEditingColumnId(null)}>Cancel</button>
                </div>
              )}

              {newCardCol === column.id && (
                <div className="new-card-form">
                  <input
                    autoFocus
                    placeholder="Card title"
                    value={newCard.title}
                    onChange={(e) => setNewCard({ ...newCard, title: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) handleCreateCard(column.id);
                      if (e.key === "Escape") setNewCardCol(null);
                    }}
                  />
                  <textarea
                    placeholder="Description"
                    value={newCard.body}
                    onChange={(e) => setNewCard({ ...newCard, body: e.target.value })}
                    rows={2}
                  />
                  <div className="form-grid">
                    <select value={newCard.priority} onChange={(e) => setNewCard({ ...newCard, priority: e.target.value as Card["priority"] })}>
                      {PRIORITIES.map((priority) => <option key={priority} value={priority}>{priority}</option>)}
                    </select>
                    <input placeholder="labels, comma separated" value={newCard.labels} onChange={(e) => setNewCard({ ...newCard, labels: e.target.value })} />
                  </div>
                  <div className="new-card-actions">
                    <button onClick={() => handleCreateCard(column.id)} disabled={!newCard.title.trim()}>Add</button>
                    <button onClick={() => setNewCardCol(null)}>Cancel</button>
                  </div>
                </div>
              )}

              <div className="cards">
                {columnCards.length === 0 && <div className="column-empty">No matching cards</div>}
                {columnCards.map((card) => (
                  <CardComponent
                    key={card.id}
                    card={card}
                    subTasks={subTasksByParent.get(card.id) ?? []}
                    board={board}
                    allCards={cards}
                    edges={edges}
                    forceExpanded={expandedCards}
                    onDragStart={setDragCardId}
                    onDelete={handleDeleteCard}
                    onMove={handleMoveCard}
                    onRefresh={onRefresh}
                  />
                ))}
              </div>
            </div>
          );
        })}

      </div>
    </div>
  );
}
