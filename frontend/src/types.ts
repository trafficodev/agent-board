export interface Column {
  id: string;
  board_id: string;
  name: string;
  position: number;
}

export interface SessionEntry {
  session_id: string;
  system: string;
  timestamp: string;
  action: string;
  outcome: "success" | "failed" | "partial" | null;
}

export interface Card {
  id: string;
  board_id: string;
  external_id: string;
  title: string;
  body: string;
  column_id: string;
  parent_id: string | null;
  position: number;
  priority: "critical" | "high" | "medium" | "low";
  labels: string[];
  metadata: Record<string, unknown>;
  session_history: SessionEntry[];
  created_at: string;
  updated_at: string;
}

export interface Edge {
  id: string;
  board_id: string;
  from_card_id: string;
  to_card_id: string;
  type: string;
  label: string;
  created_at: string;
}

export interface CardAiSearchResult {
  results: Card[];
  reasoning: string;
  error: string | null;
}

export interface Board {
  id: string;
  name: string;
  description: string;
  columns: Column[];
  created_at: string;
  updated_at: string;
}

export interface Event {
  timestamp: string;
  type: string;
  actor: string;
  detail: string;
}

export interface ChangeVoteSummary {
  up: number;
  down: number;
  current: -1 | 1 | null;
}

export interface CardChangeItem {
  id: string;
  event_id: string;
  projection_version: number;
  ordinal: number;
  path: string;
  operation: "add" | "remove" | "replace";
  diff: string;
  commit_sha: string;
  votes: ChangeVoteSummary;
}

export interface CanvasSyncStatus {
  enabled: boolean;
  canvas_board_id: string | null;
  canvas_api_url: string;
  last_synced_at: string | null;
  last_error: string | null;
}
