import type { Board, CanvasSyncStatus, Card, CardAiSearchResult, CardChangeItem, ChangeVoteSummary, Column, Edge, Event } from "./types";

const BASE = "/api";

const IDENTITY_KEY = "agent-board-browser-session";

function browserSessionId(): string {
  const existing = localStorage.getItem(IDENTITY_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID();
  localStorage.setItem(IDENTITY_KEY, created);
  return created;
}

function identityHeaders(commitSha = ""): Record<string, string> {
  return {
    "X-Agent-Board-Provider": "browser",
    "X-Agent-Board-Session": browserSessionId(),
    ...(commitSha ? { "X-Agent-Board-Commit": commitSha } : {}),
  };
}

export async function getCurrentRevision(): Promise<string> {
  const response = await fetch(`${BASE}/revision`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const revision = await response.json() as { commit_sha: string };
  return revision.commit_sha;
}

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const method = (opts?.method ?? "GET").toUpperCase();
  const headers = new Headers(opts?.headers);
  headers.set("Content-Type", "application/json");
  const isCardMutation = ["POST", "PATCH", "DELETE"].includes(method)
    && /^\/boards\/[0-9a-f]{12}\/cards(?:\/|$)/.test(path);
  if (isCardMutation && !headers.has("X-Agent-Board-Commit")) {
    const commitSha = await getCurrentRevision();
    for (const [key, value] of Object.entries(identityHeaders(commitSha))) headers.set(key, value);
  }
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// Boards
export const listBoards = () => req<Board[]>("/boards");
export const getBoard = (id: string) => req<Board>(`/boards/${id}`);
export const createBoard = (name: string, description = "", columns?: string[]) =>
  req<Board>("/boards", { method: "POST", body: JSON.stringify({ name, description, columns }) });
export const updateBoard = (id: string, data: { name?: string; description?: string }) =>
  req<Board>(`/boards/${id}`, { method: "PATCH", body: JSON.stringify(data) });
export const deleteBoard = (id: string) => req<{ ok: boolean }>(`/boards/${id}`, { method: "DELETE" });

// Cards
export const listCards = (boardId: string) => req<Card[]>(`/boards/${boardId}/cards`);
export const searchCards = (boardId: string, data: { query?: string; priority?: string; label?: string; sort?: string }) => {
  const params = new URLSearchParams();
  if (data.query) params.set("query", data.query);
  if (data.priority) params.set("priority", data.priority);
  if (data.label) params.set("label", data.label);
  if (data.sort) params.set("sort", data.sort);
  const suffix = params.toString() ? `?${params}` : "";
  return req<Card[]>(`/boards/${boardId}/cards/search${suffix}`);
};
export const aiSearchCards = (boardId: string, data: { query: string; priority?: string; label?: string; signal?: AbortSignal }) => {
  const params = new URLSearchParams();
  params.set("query", data.query);
  if (data.priority) params.set("priority", data.priority);
  if (data.label) params.set("label", data.label);
  return req<CardAiSearchResult>(`/boards/${boardId}/cards/ai-search?${params}`, { signal: data.signal });
};
export const createCard = (boardId: string, data: { title: string; body?: string; column_id: string; parent_id?: string | null; priority?: string; labels?: string[] }) =>
  req<Card>(`/boards/${boardId}/cards`, { method: "POST", body: JSON.stringify(data) });
export const updateCard = (boardId: string, cardId: string, data: Record<string, unknown>) =>
  req<Card>(`/boards/${boardId}/cards/${cardId}`, { method: "PATCH", body: JSON.stringify(data) });
export const moveCard = (boardId: string, cardId: string, column_id: string, position?: number) =>
  req<Card>(`/boards/${boardId}/cards/${cardId}/move`, { method: "POST", body: JSON.stringify({ column_id, position }) });
export const deleteCard = (boardId: string, cardId: string) =>
  req<{ ok: boolean }>(`/boards/${boardId}/cards/${cardId}`, { method: "DELETE" });
export const addSession = (boardId: string, cardId: string, data: { session_id: string; system?: string; action?: string; outcome?: string | null }) =>
  req<Card>(`/boards/${boardId}/cards/${cardId}/sessions`, { method: "POST", body: JSON.stringify(data) });

export const getCardChanges = (boardId: string, cardId: string, offset = 0, limit = 100) =>
  req<{ card_id: string; count: number; items: CardChangeItem[]; next_offset: number | null }>(
    `/boards/${boardId}/cards/${cardId}/changes?offset=${offset}&limit=${limit}`,
    { headers: identityHeaders() },
  );

export const setChangeVote = (
  boardId: string,
  cardId: string,
  targetId: string,
  direction: -1 | 1,
  commitSha: string,
) => req<{ summary: ChangeVoteSummary }>(
  `/boards/${boardId}/cards/${cardId}/changes/${targetId}/vote`,
  {
    method: "POST",
    headers: identityHeaders(commitSha),
    body: JSON.stringify({ direction }),
  },
);

// Events
export const getEvents = (boardId: string) => req<Event[]>(`/boards/${boardId}/events`);

// Canvas sync
export const getCanvasSync = (boardId: string) => req<CanvasSyncStatus>(`/boards/${boardId}/canvas-sync`);
export const enableCanvasSync = (boardId: string) =>
  req<CanvasSyncStatus>(`/boards/${boardId}/canvas-sync/enable`, { method: "POST", body: JSON.stringify({}) });
export const disableCanvasSync = (boardId: string) =>
  req<CanvasSyncStatus>(`/boards/${boardId}/canvas-sync/disable`, { method: "POST" });
export const syncCanvas = (boardId: string) =>
  req<CanvasSyncStatus>(`/boards/${boardId}/canvas-sync/sync`, { method: "POST" });

// Edges
export const listEdges = (boardId: string, cardId?: string, type?: string) => {
  const params = new URLSearchParams();
  if (cardId) params.set("card_id", cardId);
  if (type) params.set("type", type);
  const suffix = params.toString() ? `?${params}` : "";
  return req<Edge[]>(`/boards/${boardId}/edges${suffix}`);
};
export const createEdge = (boardId: string, data: { from_card_id: string; to_card_id: string; type?: string; label?: string }) =>
  req<Edge>(`/boards/${boardId}/edges`, { method: "POST", body: JSON.stringify(data) });
export const deleteEdge = (boardId: string, edgeId: string) =>
  req<{ ok: boolean }>(`/boards/${boardId}/edges/${edgeId}`, { method: "DELETE" });

// Columns
export const addColumn = (boardId: string, name: string, position?: number) =>
  req<Column>(`/boards/${boardId}/columns`, { method: "POST", body: JSON.stringify({ name, position }) });
export const updateColumn = (boardId: string, columnId: string, data: { name?: string; position?: number }) =>
  req<Column>(`/boards/${boardId}/columns/${columnId}`, { method: "PATCH", body: JSON.stringify(data) });
export const deleteColumn = (boardId: string, columnId: string) =>
  req<{ ok: boolean }>(`/boards/${boardId}/columns/${columnId}`, { method: "DELETE" });
