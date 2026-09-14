const assert = require("assert");
const {
  priceForSelection, calculateCost, buildSeriesOptions,
  cumulativeCostValues, growthChartSVG,
} = require("./dashboard.js");

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

// buildSeriesOptions: turns the embedded price series map into a
// sorted, human-readable option list, without touching the DOM.
const options = buildSeriesOptions({
  "custom::on-premise": [],
  "anthropic::claude-sonnet-5": [],
});
assert.deepStrictEqual(options, [
  { value: "anthropic::claude-sonnet-5", label: "anthropic / claude-sonnet-5" },
  { value: "custom::on-premise", label: "custom / on-premise" },
], "should sort by key and turn :: into / in the label");

assert.deepStrictEqual(buildSeriesOptions({}), [], "should return an empty list for an empty series map");

// cumulativeCostValues: the running total the live Cost chart redraws
// itself from every time the price panel changes.
const priceEntry = { input_price: 3.0, output_price: 15.0, cache_read_price: 0.3, cache_creation_price: 3.75 };
const tokensList = [
  { input: 1000000, output: 0, cache_read: 0, cache_creation: 0 }, // $3.00
  { input: 0, output: 1000000, cache_read: 0, cache_creation: 0 }, // $15.00
];
assert.deepStrictEqual(cumulativeCostValues(tokensList, priceEntry), [3.0, 18.0], "should accumulate cost across milestones in order");

assert.deepStrictEqual(
  cumulativeCostValues([{ input: 1000000, output: 0, cache_read: 0, cache_creation: 0 }], null),
  [0],
  "a milestone with no applicable price contributes 0, never null, to the running total"
);

// growthChartSVG: the client-side port of engine/svg_chart.py's
// svg_growth_chart, exercised the same way engine/tests/test_svg_chart.py
// exercises the Python original, so the two never silently drift apart.
const fmtInt = (v) => String(Math.round(v));
let svg = growthChartSVG([10, 20, 30], ["M1", "M2", "M3"], fmtInt, "test chart", "test subcaption");
assert.strictEqual((svg.match(/<circle/g) || []).length, 3, "should render one circle per value");
assert.ok(svg.includes('viewBox="0 0 700 260"'), "should default to the 260 viewBox height");
assert.ok(svg.includes(">M1<") && svg.includes(">M3<"), "first and last x labels are always present");

svg = growthChartSVG([1, 2], ["M1", "M2"], fmtInt, "cap", "sub", 200);
assert.ok(svg.includes('viewBox="0 0 700 200"'), "should honour a custom viewBox height");

svg = growthChartSVG([], [], fmtInt, "cap", "sub");
assert.ok(svg.includes("<svg"), "an empty series should still render a valid, empty chart, not throw");

console.log("all dashboard.js tests passed");
