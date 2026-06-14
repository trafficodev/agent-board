import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { getColumnRootCards, groupSameColumnChildren, isVisualChild } from "./src/boardLogic.js";

const card = (id, column_id, parent_id = null, position = 0, labels = ["feature"]) => ({
  id,
  column_id,
  parent_id,
  position,
  labels,
});

describe("board column card grouping", () => {
  it("renders cross-column children as roots in their own column", () => {
    const cards = [
      card("feature", "features", null, 0),
      card("requirement", "requirements", "feature", 0),
    ];

    assert.deepEqual(getColumnRootCards(cards, "requirements").map((item) => item.id), ["requirement"]);
    assert.equal(groupSameColumnChildren(cards).has("feature"), false);
  });

  it("nests same-column children under their parent", () => {
    const cards = [
      card("parent", "features", null, 0),
      card("child", "features", "parent", 0),
    ];

    assert.deepEqual(getColumnRootCards(cards, "features").map((item) => item.id), ["parent"]);
    assert.deepEqual(groupSameColumnChildren(cards).get("parent")?.map((item) => item.id), ["child"]);
  });

  it("renders same-state cross-type linked cards as roots", () => {
    const cards = [
      card("feature", "backlog", null, 0, ["feature"]),
      card("requirement", "backlog", "feature", 1, ["requirement"]),
    ];

    assert.deepEqual(getColumnRootCards(cards, "backlog").map((item) => item.id), ["feature", "requirement"]);
    assert.equal(groupSameColumnChildren(cards).has("feature"), false);
    assert.equal(isVisualChild(cards[1], cards[0]), false);
  });
});
