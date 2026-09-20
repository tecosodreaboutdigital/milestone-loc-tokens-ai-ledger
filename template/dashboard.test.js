const assert = require("assert");
const {
  priceForSelection, calculateCost, buildSeriesOptions,
  cumulativeCostValues, growthChartSVG, defaultSeriesKey,
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

svg = growthChartSVG([1, 2], ["M1", "M2"], fmtInt, "cap", "sub");
assert.ok(svg.includes('xmlns="http://www.w3.org/2000/svg"'), "should declare the SVG namespace, matching engine/svg_chart.py's own output");

// 1-hour cache writes. cache_creation is the total of every write and
// cache_creation_1h the 1-hour part of it; the 5-minute part is the
// difference, so nothing is counted twice.
const priceWith1h = {
  input_price: 2.0, output_price: 10.0, cache_read_price: 0.2,
  cache_creation_price: 2.5, cache_creation_1h_price: 4.0,
};
assert.strictEqual(
  calculateCost({ input: 0, output: 0, cache_read: 0, cache_creation: 1000000, cache_creation_1h: 400000 }, priceWith1h),
  2.5 * 0.6 + 4.0 * 0.4,
  "600k 5-minute writes at 2.50 plus 400k 1-hour writes at 4.00, not 1M at either rate"
);
assert.strictEqual(
  calculateCost({ input: 0, output: 0, cache_read: 0, cache_creation: 1000000, cache_creation_1h: 1000000 }, priceWith1h),
  4.0,
  "all cache writes 1-hour: all at the 1-hour price"
);
assert.strictEqual(
  calculateCost({ input: 0, output: 0, cache_read: 0, cache_creation: 1000000, cache_creation_1h: 5000000 }, priceWith1h),
  4.0,
  "a 1-hour figure larger than cache_creation is capped at it, never a second, larger number"
);

// A milestone frozen before the engine recorded 1-hour writes has no
// cache_creation_1h at all: it prices exactly as before.
assert.strictEqual(
  calculateCost({ input: 1000000, output: 0, cache_read: 0, cache_creation: 1000000 }, priceWith1h),
  2.0 + 2.5,
  "no cache_creation_1h field: every cache write is a 5-minute write"
);

// A price entry with no 1-hour price never prices 1-hour writes at the
// 5-minute rate: the milestone is unpriceable (null), while one with no
// 1-hour writes still prices normally under the same entry.
const priceWithout1h = { input_price: 3.0, output_price: 15.0, cache_read_price: 0.3, cache_creation_price: 3.75 };
assert.strictEqual(
  calculateCost({ input: 1000000, output: 0, cache_read: 0, cache_creation: 1000000, cache_creation_1h: 1 }, priceWithout1h),
  null,
  "1-hour writes under an entry with no 1-hour price are not priced at the 5-minute rate"
);
assert.strictEqual(
  calculateCost({ input: 1000000, output: 0, cache_read: 0, cache_creation: 1000000, cache_creation_1h: 0 }, priceWithout1h),
  3.0 + 3.75,
  "no 1-hour writes: an entry with no 1-hour price still prices"
);
assert.strictEqual(
  calculateCost({ input: 0, output: 0, cache_read: 0, cache_creation: 10, cache_creation_1h: 10 }, { ...priceWithout1h, cache_creation_1h_price: 0 }),
  0,
  "an explicit 1-hour price of 0 (a free series) is a price, not a missing one"
);
assert.deepStrictEqual(
  cumulativeCostValues(
    [
      { input: 1000000, output: 0, cache_read: 0, cache_creation: 0, cache_creation_1h: 0 },
      { input: 0, output: 0, cache_read: 0, cache_creation: 100, cache_creation_1h: 100 },
    ],
    priceWithout1h
  ),
  [3.0, 3.0],
  "an unpriceable milestone contributes 0 to the running total"
);

// Same-date tie: the later entry in the list wins, which is how a
// correcting entry (same effective_date, appended after) takes over.
const corrected = {
  "anthropic::claude-sonnet-5": [
    { effective_date: "2026-09-13", input_price: 3.0, output_price: 15.0, cache_read_price: 0.3, cache_creation_price: 3.75 },
    { effective_date: "2026-09-13", corrects: "2026-09-13", input_price: 2.0, output_price: 10.0, cache_read_price: 0.2, cache_creation_price: 2.5, cache_creation_1h_price: 4.0 },
  ],
};
assert.strictEqual(
  priceForSelection(corrected, "anthropic", "claude-sonnet-5", "2026-09-13").input_price,
  2.0,
  "a correcting entry with the same effective_date wins the tie"
);

// The price panel opens on the project's own configured series.
const seriesOptions = [
  { value: "anthropic::claude-haiku-4-5-20251001", label: "a" },
  { value: "anthropic::claude-sonnet-5", label: "b" },
];
assert.strictEqual(defaultSeriesKey(seriesOptions, "anthropic::claude-sonnet-5"), "anthropic::claude-sonnet-5");
assert.strictEqual(defaultSeriesKey(seriesOptions, "anthropic::not-there"), "anthropic::claude-haiku-4-5-20251001", "an unknown preferred series falls back to the first option");
assert.strictEqual(defaultSeriesKey(seriesOptions, undefined), "anthropic::claude-haiku-4-5-20251001", "no preferred series (an older generated page): first option");
assert.strictEqual(defaultSeriesKey([], "x"), null);

console.log("all dashboard.js tests passed");
