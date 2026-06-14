import type { Board, CanvasSyncStatus, Card, Column, Event } from "./types";

const BASE = "/api";

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
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

// Columns
export const addColumn = (boardId: string, name: string, position?: number) =>
  req<Column>(`/boards/${boardId}/columns`, { method: "POST", body: JSON.stringify({ name, position }) });
export const updateColumn = (boardId: string, columnId: string, data: { name?: string; position?: number }) =>
  req<Column>(`/boards/${boardId}/columns/${columnId}`, { method: "PATCH", body: JSON.stringify(data) });
export const deleteColumn = (boardId: string, columnId: string) =>
  req<{ ok: boolean }>(`/boards/${boardId}/columns/${columnId}`, { method: "DELETE" });
