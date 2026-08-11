import assert from "node:assert/strict";
import test from "node:test";

import { listCards, searchCards } from "./src/api.ts";

function mockPages(pages) {
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(url);
    const page = pages.shift();
    assert.ok(page, `Unexpected request: ${url}`);
    return { ok: true, json: async () => page };
  };
  return urls;
}

test("listCards requests full projections and drains every page", async () => {
  const urls = mockPages([
    { items: [{ id: "first" }], next_cursor: "next page", has_more: true },
    { items: [{ id: "second" }], next_cursor: null, has_more: false },
  ]);

  const cards = await listCards("board");

  assert.deepEqual(cards.map(({ id }) => id), ["first", "second"]);
  assert.deepEqual(urls, [
    "/api/boards/board/cards?include=*",
    "/api/boards/board/cards?include=*&cursor=next+page",
  ]);
});

test("searchCards preserves filters while draining pages", async () => {
  const urls = mockPages([
    { items: [], next_cursor: "next", has_more: true },
    { items: [{ id: "match" }], next_cursor: null, has_more: false },
  ]);

  const cards = await searchCards("board", { query: "fix bug", priority: "high" });

  assert.deepEqual(cards.map(({ id }) => id), ["match"]);
  assert.deepEqual(urls, [
    "/api/boards/board/cards/search?include=*&query=fix+bug&priority=high",
    "/api/boards/board/cards/search?include=*&query=fix+bug&priority=high&cursor=next",
  ]);
});
