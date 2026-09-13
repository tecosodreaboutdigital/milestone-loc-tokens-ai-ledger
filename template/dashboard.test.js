const assert = require("assert");
const { priceForSelection, calculateCost } = require("./dashboard.js");

const pricesBySeries = {
  "anthropic::claude-sonnet-5": [
    {
      effective_date: "2026-01-01",
      input_price: 5.0, output_price: 20.0,
      cache_read_price: 0.5, cache_creation_price: 6.0,
    },
    {
      effective_date: "2026-09-13",
      input_price: 3.0, output_price: 15.0,
      cache_read_price: 0.3, cache_creation_price: 3.75,
    },
  ],
};

// picks the entry effective on the target date, not just the newest
let entry = priceForSelection(pricesBySeries, "anthropic", "claude-sonnet-5", "2026-05-01");
assert.strictEqual(entry.input_price, 5.0, "should pick the January entry for a May date");

entry = priceForSelection(pricesBySeries, "anthropic", "claude-sonnet-5", "2026-12-31");
assert.strictEqual(entry.input_price, 3.0, "should pick the September entry for a later date");

assert.strictEqual(
  priceForSelection(pricesBySeries, "anthropic", "claude-sonnet-5", "2025-01-01"),
  null,
  "should return null before any entry existed"
);

// cost calculation
const cost = calculateCost(
  { input: 1000000, output: 1000000, cache_read: 0, cache_creation: 0 },
  { input_price: 3.0, output_price: 15.0, cache_read_price: 0.3, cache_creation_price: 3.75 }
);
assert.strictEqual(cost, 18.0);

assert.strictEqual(calculateCost({ input: 1, output: 0, cache_read: 0, cache_creation: 0 }, null), null);

console.log("all dashboard.js tests passed");
