export function isColumnRoot(card, cardsById) {
  if (!card.parent_id) return true;
  const parent = cardsById.get(card.parent_id);
  if (!parent) return true;
  return !isVisualChild(card, parent);
}

export function isVisualChild(card, parent) {
  if (parent.column_id !== card.column_id) return false;
  return hasSharedLabel(card, parent);
}

export function hasSharedLabel(card, parent) {
  if (card.labels.length === 0 || parent.labels.length === 0) return true;
  return card.labels.some((label) => parent.labels.includes(label));
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
