import { useState } from "react";
import type { Board, Card } from "../types";
import * as api from "../api";
import CardComponent from "./CardComponent";

interface Props {
  board: Board;
  cards: Card[];
  onRefresh: () => void;
}

export default function BoardView({ board, cards, onRefresh }: Props) {
  const [dragCardId, setDragCardId] = useState<string | null>(null);
  const [newCardCol, setNewCardCol] = useState<string | null>(null);
  const [newCardTitle, setNewCardTitle] = useState("");

  const handleDragStart = (cardId: string) => {
    setDragCardId(cardId);
  };

  const handleDrop = async (columnId: string) => {
    if (!dragCardId) return;
    await api.moveCard(board.id, dragCardId, columnId);
    setDragCardId(null);
    onRefresh();
  };

  const handleCreateCard = async (columnId: string) => {
    if (!newCardTitle.trim()) return;
    await api.createCard(board.id, {
      title: newCardTitle.trim(),
      column_id: columnId,
    });
    setNewCardTitle("");
    setNewCardCol(null);
    onRefresh();
  };

  const handleDeleteCard = async (cardId: string) => {
    await api.deleteCard(board.id, cardId);
    onRefresh();
  };

  const handleMoveCard = async (cardId: string, columnId: string) => {
    await api.moveCard(board.id, cardId, columnId);
    onRefresh();
  };

  return (
    <div className="board-view">
      <div className="board-header">
        <h2>{board.name}</h2>
        {board.description && <p className="board-desc">{board.description}</p>}
      </div>
      <div className="columns">
        {board.columns.map((col) => {
          const colCards = cards
            .filter((c) => c.column_id === col.id)
            .sort((a, b) => a.position - b.position);

          return (
            <div
              key={col.id}
              className="column"
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => handleDrop(col.id)}
            >
              <div className="column-header">
                <h3>{col.name}</h3>
                <span className="card-count">{colCards.length}</span>
                <button
                  className="add-card-btn"
                  onClick={() => {
                    setNewCardCol(col.id);
                    setNewCardTitle("");
                  }}
                >
                  +
                </button>
              </div>

              {newCardCol === col.id && (
                <div className="new-card-form">
                  <input
                    autoFocus
                    placeholder="Card title..."
                    value={newCardTitle}
                    onChange={(e) => setNewCardTitle(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleCreateCard(col.id);
                      if (e.key === "Escape") setNewCardCol(null);
                    }}
                  />
                  <div className="new-card-actions">
                    <button onClick={() => handleCreateCard(col.id)} disabled={!newCardTitle.trim()}>
                      Add
                    </button>
                    <button onClick={() => setNewCardCol(null)}>Cancel</button>
                  </div>
                </div>
              )}

              <div className="cards">
                {colCards.map((card) => (
                  <CardComponent
                    key={card.id}
                    card={card}
                    board={board}
                    onDragStart={handleDragStart}
                    onDelete={handleDeleteCard}
                    onMove={handleMoveCard}
                    onSessionAdded={onRefresh}
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
