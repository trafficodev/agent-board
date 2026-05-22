import { useState, useEffect, useCallback, useRef } from "react";
import type { Board, Card } from "./types";
import * as api from "./api";
import BoardView from "./components/BoardView";
import "./App.css";

function App() {
  const [boards, setBoards] = useState<Board[]>([]);
  const [activeBoardId, setActiveBoardId] = useState<string | null>(null);
  const [cards, setCards] = useState<Card[]>([]);
  const [newBoardName, setNewBoardName] = useState("");
  const activeBoardRef = useRef<string | null>(null);

  // Keep ref in sync
  activeBoardRef.current = activeBoardId;

  const loadBoards = useCallback(async () => {
    const list = await api.listBoards();
    setBoards(list);
    // Only auto-select on initial load (no board selected yet)
    if (!activeBoardRef.current && list.length > 0) {
      setActiveBoardId(list[0].id);
    }
  }, []); // no deps — uses ref to read current state

  useEffect(() => {
    loadBoards();
  }, [loadBoards]);

  useEffect(() => {
    if (!activeBoardId) return;
    api.listCards(activeBoardId).then(setCards);
  }, [activeBoardId]);

  const activeBoard = boards.find((b) => b.id === activeBoardId) ?? null;

  const handleCreateBoard = async () => {
    if (!newBoardName.trim()) return;
    const board = await api.createBoard(newBoardName.trim());
    setNewBoardName("");
    await loadBoards();
    setActiveBoardId(board.id);
  };

  const handleDeleteBoard = async (id: string) => {
    await api.deleteBoard(id);
    if (activeBoardId === id) {
      const remaining = boards.filter((b) => b.id !== id);
      setActiveBoardId(remaining.length > 0 ? remaining[0].id : null);
    }
    await loadBoards();
  };

  const handleRefreshCards = async () => {
    if (!activeBoardId) return;
    const list = await api.listCards(activeBoardId);
    setCards(list);
  };

  return (
    <div className="app">
      <header className="sidebar">
        <h1 className="logo">Agent Board</h1>

        <div className="sidebar-section">
          <div className="sidebar-header">
            <span>Boards</span>
          </div>
          <div className="new-board-row">
            <input
              placeholder="New board..."
              value={newBoardName}
              onChange={(e) => setNewBoardName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreateBoard()}
            />
            <button onClick={handleCreateBoard} disabled={!newBoardName.trim()}>+</button>
          </div>
          <ul className="board-list">
            {boards.map((b) => (
              <li
                key={b.id}
                className={`board-item ${b.id === activeBoardId ? "active" : ""}`}
              >
                <button className="board-name" onClick={() => setActiveBoardId(b.id)}>
                  {b.name}
                </button>
                <button className="board-delete" onClick={() => handleDeleteBoard(b.id)} title="Delete">
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>
      </header>

      <main className="main">
        {activeBoard ? (
          <BoardView
            key={activeBoard.id}
            board={activeBoard}
            cards={cards}
            onRefresh={handleRefreshCards}
          />
        ) : (
          <div className="empty-state">
            <p>Create a board to get started</p>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
