import { useState } from "react";
import type { Board, Card } from "../types";
import * as api from "../api";

interface Props {
  card: Card;
  subTasks: Card[];
  board: Board;
  allCards: Card[];
  forceExpanded: boolean;
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

function SubTaskTree({ card, allCards, depth = 0 }: { card: Card; allCards: Card[]; depth?: number }) {
  const children = allCards
    .filter((c) => c.parent_id === card.id)
    .sort((a, b) => a.position - b.position);

  return (
    <div className={`sub-task priority-${card.priority}`} style={{ marginLeft: depth > 0 ? 16 : 0 }}>
      <span className="priority-dot" style={{ background: PRIORITY_COLORS[card.priority] }} />
      <span className="sub-task-title">{card.title}</span>
      {card.body && <span className="sub-task-body">{card.body}</span>}
      {children.length > 0 && (
        <div className="sub-tasks nested">
          {children.map((child) => (
            <SubTaskTree key={child.id} card={child} allCards={allCards} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function CardComponent({ card, subTasks, board, allCards, forceExpanded, onDragStart, onDelete, onMove, onRefresh }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(card.title);
  const [editBody, setEditBody] = useState(card.body);
  const [editPriority, setEditPriority] = useState<Card["priority"]>(card.priority);
  const [editLabels, setEditLabels] = useState(card.labels.join(", "));
  const [showSubTaskForm, setShowSubTaskForm] = useState(false);
  const [subTaskTitle, setSubTaskTitle] = useState("");
  const [subTaskBody, setSubTaskBody] = useState("");
  const [subTaskPriority, setSubTaskPriority] = useState<Card["priority"]>("medium");

  const handleSaveEdit = async () => {
    if (!editTitle.trim()) return;
    await api.updateCard(board.id, card.id, {
      title: editTitle.trim(),
      body: editBody.trim(),
      priority: editPriority,
      labels: editLabels.split(",").map((label) => label.trim()).filter(Boolean),
    });
    setEditing(false);
    onRefresh();
  };

  const handleCreateSubTask = async () => {
    if (!subTaskTitle.trim()) return;
    await api.createCard(board.id, {
      title: subTaskTitle.trim(),
      body: subTaskBody.trim(),
      column_id: card.column_id,
      parent_id: card.id,
      priority: subTaskPriority,
    });
    setSubTaskTitle("");
    setSubTaskBody("");
    setSubTaskPriority("medium");
    setShowSubTaskForm(false);
    onRefresh();
  };

  const otherColumns = board.columns.filter((c) => c.id !== card.column_id);
  const isExpanded = expanded || forceExpanded;

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
        {subTasks.length > 0 && <span className="subtask-count">{subTasks.length}</span>}
      </div>

      {card.labels.length > 0 && (
        <div className="card-labels">
          {card.labels.map((l) => (
            <span key={l} className="label">{l}</span>
          ))}
        </div>
      )}

      {isExpanded && (
        <div className="card-expanded" onClick={(e) => e.stopPropagation()}>
          {editing ? (
            <div className="card-edit">
              <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
              <textarea value={editBody} onChange={(e) => setEditBody(e.target.value)} rows={3} />
              <div className="form-grid">
                <select value={editPriority} onChange={(e) => setEditPriority(e.target.value as Card["priority"])}>
                  {Object.keys(PRIORITY_COLORS).map((priority) => (
                    <option key={priority} value={priority}>{priority}</option>
                  ))}
                </select>
                <input value={editLabels} onChange={(e) => setEditLabels(e.target.value)} placeholder="labels, comma separated" />
              </div>
              <div className="edit-actions">
                <button onClick={handleSaveEdit} disabled={!editTitle.trim()}>Save</button>
                <button onClick={() => setEditing(false)}>Cancel</button>
              </div>
            </div>
          ) : (
            <>
              {card.body && <p className="card-body">{card.body}</p>}

              {subTasks.length > 0 && (
                <div className="sub-tasks">
                  <span className="section-label">Sub-tasks ({subTasks.length}):</span>
                  {subTasks.map((sub) => (
                    <SubTaskTree key={sub.id} card={sub} allCards={allCards} />
                  ))}
                </div>
              )}

              {showSubTaskForm ? (
                <div className="sub-task-form">
                  <input
                    autoFocus
                    placeholder="Sub-task title..."
                    value={subTaskTitle}
                    onChange={(e) => setSubTaskTitle(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) handleCreateSubTask();
                      if (e.key === "Escape") setShowSubTaskForm(false);
                    }}
                  />
                  <textarea
                    placeholder="Description (optional)..."
                    value={subTaskBody}
                    onChange={(e) => setSubTaskBody(e.target.value)}
                    rows={2}
                  />
                  <select value={subTaskPriority} onChange={(e) => setSubTaskPriority(e.target.value as Card["priority"])}>
                    {Object.keys(PRIORITY_COLORS).map((priority) => (
                      <option key={priority} value={priority}>{priority}</option>
                    ))}
                  </select>
                  <div className="new-card-actions">
                    <button onClick={handleCreateSubTask} disabled={!subTaskTitle.trim()}>Add</button>
                    <button onClick={() => setShowSubTaskForm(false)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <button className="add-subtask-btn" onClick={() => setShowSubTaskForm(true)}>+ Sub-task</button>
              )}

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
                <button onClick={() => {
                  setEditTitle(card.title);
                  setEditBody(card.body);
                  setEditPriority(card.priority);
                  setEditLabels(card.labels.join(", "));
                  setEditing(true);
                }}>Edit</button>
                <button className="danger" onClick={() => onDelete(card.id)}>Delete</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
