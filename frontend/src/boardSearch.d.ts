import type { Card, Column } from "./types";

export interface BoardSearchFilters {
  query: string;
  priority: "all" | Card["priority"];
  label: "all" | string;
}

export interface BoardSearchResult {
  cards: Card[];
  directMatchIds: Set<string>;
}

export interface SearchTerm {
  negative: boolean;
  field: string | null;
  value: string;
}

export function filterCardsForBoard(cards: Card[], columns: Column[], filters: BoardSearchFilters): BoardSearchResult;
export function parseSearchQuery(query: string): SearchTerm[];
export function requiresBackendSearch(query: string): boolean;
