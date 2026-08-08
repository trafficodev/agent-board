import { useEffect, useState } from "react";
import * as api from "../api";
import type { CardChangeItem } from "../types";


export default function CardChangelog({
  boardId,
  cardId,
  revision,
}: {
  boardId: string;
  cardId: string;
  revision: string;
}) {
  const [items, setItems] = useState<CardChangeItem[]>([]);
  const [error, setError] = useState("");
  const [busyTarget, setBusyTarget] = useState("");
  const [reviewedCommit, setReviewedCommit] = useState("");
  const [nextOffset, setNextOffset] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([
      api.getCardChanges(boardId, cardId),
      api.getCurrentRevision(),
    ])
      .then(([result, commitSha]) => {
        if (!active) return;
        setItems(result.items);
        setNextOffset(result.next_offset);
        setReviewedCommit(commitSha);
        setError("");
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load changes");
      });
    return () => { active = false; };
  }, [boardId, cardId, revision]);

  const vote = async (item: CardChangeItem, direction: -1 | 1) => {
    setBusyTarget(item.id);
    setError("");
    try {
      const result = await api.setChangeVote(
        boardId,
        cardId,
        item.id,
        direction,
        reviewedCommit,
      );
      setItems((current) => current.map((candidate) => (
        candidate.id === item.id
          ? { ...candidate, votes: result.summary }
          : candidate
      )));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save vote");
    } finally {
      setBusyTarget("");
    }
  };

  const loadMore = async () => {
    if (nextOffset === null) return;
    try {
      const result = await api.getCardChanges(boardId, cardId, nextOffset);
      setItems((current) => [...current, ...result.items]);
      setNextOffset(result.next_offset);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load changes");
    }
  };

  if (!items.length && !error) return null;
  const commitOptions = Array.from(new Set([
    reviewedCommit,
    ...items.map((item) => item.commit_sha),
  ].filter(Boolean)));
  return (
    <div className="card-changelog">
      <div className="change-heading">
        <span className="section-label">Changes:</span>
        <label>
          Reviewed revision
          <select value={reviewedCommit} onChange={(event) => setReviewedCommit(event.target.value)}>
            {commitOptions.map((commitSha) => (
              <option value={commitSha} key={commitSha}>{commitSha.slice(0, 8)}</option>
            ))}
          </select>
        </label>
      </div>
      {items.map((item) => (
        <div className="change-item" key={item.id}>
          <pre>{item.diff}</pre>
          <div className="change-votes">
            <button
              className={item.votes.current === 1 ? "selected" : ""}
              disabled={busyTarget === item.id}
              aria-label="Upvote change"
              onClick={() => void vote(item, 1)}
            >▲ {item.votes.up}</button>
            <button
              className={item.votes.current === -1 ? "selected" : ""}
              disabled={busyTarget === item.id}
              aria-label="Downvote change"
              onClick={() => void vote(item, -1)}
            >▼ {item.votes.down}</button>
          </div>
        </div>
      ))}
      {nextOffset !== null && <button onClick={() => void loadMore()}>Load more changes</button>}
      {error && <span className="change-error">{error}</span>}
    </div>
  );
}
