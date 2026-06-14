import type { Card } from "./types";

export function isColumnRoot(card: Card, cardsById: Map<string, Card>): boolean;
export function groupSameColumnChildren(cards: Card[]): Map<string, Card[]>;
export function getColumnRootCards(cards: Card[], columnId: string): Card[];
