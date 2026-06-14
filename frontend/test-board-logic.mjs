import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { getColumnRootCards, groupSameColumnChildren } from "./src/boardLogic.js";

const card = (id, column_id, parent_id = null, position = 0) => ({
  id,
  column_id,
  parent_id,
  position,
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
});
