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
  title: string;
  body: string;
  column_id: string;
  position: number;
  priority: "critical" | "high" | "medium" | "low";
  labels: string[];
  session_history: SessionEntry[];
  created_at: string;
  updated_at: string;
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
