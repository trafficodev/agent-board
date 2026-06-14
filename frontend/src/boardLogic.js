export function isColumnRoot(card, cardsById) {
  if (!card.parent_id) return true;
  return cardsById.get(card.parent_id)?.column_id !== card.column_id;
}

export function groupSameColumnChildren(cards) {
  const cardsById = new Map(cards.map((card) => [card.id, card]));
  const grouped = new Map();

  for (const card of cards) {
    if (!card.parent_id) continue;
    if (isColumnRoot(card, cardsById)) continue;
    const group = grouped.get(card.parent_id) ?? [];
    group.push(card);
    grouped.set(card.parent_id, group);
  }

  for (const group of grouped.values()) {
    group.sort((a, b) => a.position - b.position);
  }

  return grouped;
}

export function getColumnRootCards(cards, columnId) {
  const cardsById = new Map(cards.map((card) => [card.id, card]));
  return cards
    .filter((card) => card.column_id === columnId)
    .filter((card) => isColumnRoot(card, cardsById))
    .sort((a, b) => a.position - b.position);
}
