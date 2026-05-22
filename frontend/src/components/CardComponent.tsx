import { useState, useEffect } from "react";
import type { Board, Card } from "../types";
import * as api from "../api";

interface Props {
  card: Card;
  board: Board;
  onDragStart: (cardId: string) => void;
  onDelete: (cardId: string) => void;
  onMove: (cardId: string, columnId: string) => void;
  onRefresh: () => void;
}

const PRIORITY_COLORS: Record<string, string> = {
  critical: "#e74c3c",
  high: "#e67e22",
  medium: "#3498db",
  low: "#95a5a6",
};

export default function CardComponent({ card, board, onDragStart, onDelete, onMove, onRefresh }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(card.title);
  const [editBody, setEditBody] = useState(card.body);

  // Sync edit fields from latest props whenever expanded
  useEffect(() => {
    if (expanded) {
      setEditTitle(card.title);
      setEditBody(card.body);
    }
  }, [expanded, card.title, card.body]);

  const handleSaveEdit = async () => {
    await api.updateCard(board.id, card.id, { title: editTitle, body: editBody });
    setEditing(false);
    onRefresh();
  };

  const otherColumns = board.columns.filter((c) => c.id !== card.column_id);

  return (
    <div
      className={`card priority-${card.priority}`}
      draggable
      onDragStart={() => onDragStart(card.id)}
      onClick={() => !editing && setExpanded(!expanded)}
    >
      <div className="card-top">
        <span className="priority-dot" style={{ background: PRIORITY_COLORS[card.priority] }} />
        <span className="card-title">{card.title}</span>
      </div>

      {card.labels.length > 0 && (
        <div className="card-labels">
          {card.labels.map((l) => (
            <span key={l} className="label">{l}</span>
          ))}
        </div>
      )}

      {expanded && (
        <div className="card-expanded" onClick={(e) => e.stopPropagation()}>
          {editing ? (
            <div className="card-edit">
              <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
              <textarea value={editBody} onChange={(e) => setEditBody(e.target.value)} rows={3} />
              <div className="edit-actions">
                <button onClick={handleSaveEdit}>Save</button>
                <button onClick={() => setEditing(false)}>Cancel</button>
              </div>
            </div>
          ) : (
            <>
              {card.body && <p className="card-body">{card.body}</p>}

              {otherColumns.length > 0 && (
                <div className="move-actions">
                  <span>Move to:</span>
                  {otherColumns.map((col) => (
                    <button key={col.id} onClick={() => onMove(card.id, col.id)}>
                      {col.name}
                    </button>
                  ))}
                </div>
              )}

              {card.session_history.length > 0 && (
                <div className="session-history">
                  <span className="section-label">Sessions:</span>
                  {card.session_history.map((s, i) => (
                    <div key={i} className="session-entry">
                      <span className="session-id">{s.session_id.slice(0, 8)}…</span>
                      {s.system && <span className="session-system">{s.system}</span>}
                      {s.action && <span className="session-action">{s.action}</span>}
                      {s.outcome && (
                        <span className={`session-outcome ${s.outcome}`}>{s.outcome}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="card-actions">
                <button onClick={() => setEditing(true)}>Edit</button>
                <button className="danger" onClick={() => onDelete(card.id)}>Delete</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
