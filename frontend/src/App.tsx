import { useState, useEffect, useCallback } from "react";
import type { Board, Card, Edge } from "./types";
import * as api from "./api";
import BoardView from "./components/BoardView";
import "./App.css";

function App() {
  const [boards, setBoards] = useState<Board[]>([]);
  const [activeBoardId, setActiveBoardId] = useState<string | null>(null);
  const [cards, setCards] = useState<Card[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [newBoardName, setNewBoardName] = useState("");

  const loadBoards = useCallback(async () => {
    const list = await api.listBoards();
    setBoards(list);
    setActiveBoardId((current) => current ?? list[0]?.id ?? null);
  }, []);

  useEffect(() => {
    void api.listBoards().then((list) => {
      setBoards(list);
      setActiveBoardId((current) => current ?? list[0]?.id ?? null);
    });
  }, []);

  useEffect(() => {
    if (!activeBoardId) return;
    api.listCards(activeBoardId).then(setCards);
    api.listEdges(activeBoardId).then(setEdges);
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

  const handleBoardUpdated = async (board: Board) => {
    setBoards((current) => current.map((item) => (item.id === board.id ? board : item)));
    await loadBoards();
  };

  const handleRefreshCards = async () => {
    if (!activeBoardId) return;
    const [cardList, edgeList] = await Promise.all([
      api.listCards(activeBoardId),
      api.listEdges(activeBoardId),
    ]);
    setCards(cardList);
    setEdges(edgeList);
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
            edges={edges}
            onRefresh={handleRefreshCards}
            onBoardUpdated={handleBoardUpdated}
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
