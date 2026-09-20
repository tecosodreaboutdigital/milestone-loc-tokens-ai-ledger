// The only interactive part of the published dashboard: recomputing a
// milestone's cost under any provider, model, or historical price
// already in the ledger. Never makes a network call. The ledger and
// the milestone data are both embedded in the page at generation
// time, in window.MILESTONE_DATA, set by a script tag this file does
// not own.

const PRICE_SELECTION_STORAGE_KEY = "milestone-ledger-price-selection";

function priceForSelection(pricesBySeries, provider, model, targetDate) {
  const key = provider + "::" + model;
  const entries = pricesBySeries[key] || [];
  const eligible = entries.filter((e) => e.effective_date <= targetDate);
  if (eligible.length === 0) {
    return null;
  }
  return eligible.reduce((a, b) => (a.effective_date > b.effective_date ? a : b));
}

function hasOneHourPrice(priceEntry) {
  return typeof priceEntry.cache_creation_1h_price === "number" && isFinite(priceEntry.cache_creation_1h_price);
}

// Mirrors engine/cost.py's price_tokens. cache_creation is the total of
// every cache write and cache_creation_1h the 1-hour part of it (absent
// in milestones frozen by an older engine, then 0): the 5-minute part is
// the difference, priced at cache_creation_price; the 1-hour part is
// priced at cache_creation_1h_price when the entry has one. When the
// entry has none, a milestone that wrote 1-hour cache cannot be priced
// honestly, so the result is null (shown as "-", 0 in the running
// total), never the 5-minute rate applied to 1-hour writes.
function calculateCost(tokens, priceEntry) {
  if (!priceEntry) {
    return null;
  }
  const cacheCreation = tokens.cache_creation || 0;
  const oneHour = Math.min(tokens.cache_creation_1h || 0, cacheCreation);
  const fiveMinute = cacheCreation - oneHour;
  if (oneHour > 0 && !hasOneHourPrice(priceEntry)) {
    return null;
  }
  let amount =
    ((tokens.input || 0) / 1000000) * priceEntry.input_price +
    ((tokens.output || 0) / 1000000) * priceEntry.output_price +
    ((tokens.cache_read || 0) / 1000000) * priceEntry.cache_read_price +
    (fiveMinute / 1000000) * priceEntry.cache_creation_price;
  if (oneHour > 0) {
    amount += (oneHour / 1000000) * priceEntry.cache_creation_1h_price;
  }
  return Math.round(amount * 10000) / 10000;
}

// The series the price panel opens on: the one the engine says this
// project is priced with, when it is among the options, else the first
// option (the behaviour before the engine embedded a default).
function defaultSeriesKey(options, preferred) {
  if (preferred && options.some((option) => option.value === preferred)) {
    return preferred;
  }
  return options.length > 0 ? options[0].value : null;
}

// Turns the embedded provider/model price series into a stable,
// sorted list of selectable options. Pure and DOM-free so it can be
// tested from Node the same way priceForSelection and calculateCost
// already are.
function buildSeriesOptions(pricesBySeries) {
  return Object.keys(pricesBySeries)
    .sort()
    .map((key) => ({ value: key, label: key.replace("::", " / ") }));
}

// A milestone with no live-priced cost (no price entry applies)
// contributes 0 to the running total, the same convention
// engine/generate_metrics.py uses for the server-rendered chart: a
// ledger of what is actually known, never a guess for what is not.
function cumulativeCostValues(tokensList, priceEntry) {
  let running = 0;
  return tokensList.map((tokens) => {
    running += calculateCost(tokens, priceEntry) || 0;
    return running;
  });
}

// A line-and-label growth chart as a self-contained SVG string,
// ported unchanged in behaviour from engine/svg_chart.py's
// svg_growth_chart, including its label-thinning logic, so the
// client-redrawn Cost chart never looks different in kind from the
// server-rendered Words/Lines/Tokens charts sitting right above it.
function growthChartSVG(values, xLabels, yFmt, caption, subcaption, viewboxH) {
  const width = 700;
  const height = viewboxH || 260;
  const left = 78, right = 652, top = 34, bottom = height - 66;
  const n = values.length;
  const maxRaw = n > 0 ? Math.max.apply(null, values) : 0;
  const maxV = maxRaw > 0 ? maxRaw * 1.12 : 1;
  const xs = values.map((_, i) => (n > 1 ? left + (i * (right - left)) / (n - 1) : left));
  const ys = values.map((v) => bottom - (v / maxV) * (bottom - top));
  const points = xs.map((x, i) => x.toFixed(1) + "," + ys[i].toFixed(1)).join(" ");

  const parts = [];
  parts.push('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + " " + height + '" role="img" aria-label="' + caption + '">');
  parts.push('<line x1="' + left + '" y1="' + bottom + '" x2="' + right + '" y2="' + bottom + '" stroke="#c9c7bf" stroke-width=".7"/>');
  parts.push('<line x1="' + left + '" y1="' + top + '" x2="' + right + '" y2="' + top + '" stroke="#c9c7bf" stroke-width=".5" stroke-dasharray="2,3"/>');
  parts.push('<text class="svg-sub" x="' + (left - 6) + '" y="' + (top + 4) + '" text-anchor="end">' + yFmt(maxV) + "</text>");
  parts.push('<text class="svg-sub" x="' + (left - 6) + '" y="' + (bottom + 4) + '" text-anchor="end">0</text>');
  parts.push('<polyline points="' + points + '" fill="none" stroke="#1b1b19" stroke-width="1"/>');

  if (n === 0) {
    parts.push('<text class="svg-cap" x="' + left + '" y="' + (height - 16) + '">' + subcaption + "</text>");
    parts.push("</svg>");
    return parts.join("\n");
  }

  const maxLabels = Math.max(2, Math.floor((right - left) / 70));
  const step = n > 1 ? Math.max(1, Math.round((n - 1) / (maxLabels - 1))) : 1;
  const rawLabelled = new Set();
  for (let i = 0; i < n; i += step) rawLabelled.add(i);
  rawLabelled.add(0);
  const labelled = new Set(
    Array.from(rawLabelled).filter((i) => i === n - 1 || n - 1 - i >= Math.max(2, Math.floor(step / 2)))
  );
  labelled.add(n - 1);

  const xMaxLabels = Math.max(2, Math.floor((right - left) / 26));
  const xStep = n > 1 ? Math.max(1, Math.round((n - 1) / (xMaxLabels - 1))) : 1;
  const xLabelled = new Set();
  for (let i = 0; i < n; i += xStep) xLabelled.add(i);
  xLabelled.add(0);
  xLabelled.add(n - 1);

  for (let i = 0; i < n; i++) {
    const x = xs[i], y = ys[i], v = values[i];
    parts.push('<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="3" fill="#1b1b19"/>');
    if (labelled.has(i)) {
      const labelY = y > top + 16 ? y - 10 : y + 16;
      parts.push('<text class="svg-sub" x="' + x.toFixed(1) + '" y="' + labelY.toFixed(1) + '" text-anchor="middle">' + yFmt(v) + "</text>");
    }
    if (xLabelled.has(i)) {
      parts.push('<text class="svg-sub" x="' + x.toFixed(1) + '" y="' + (height - 40) + '" text-anchor="middle">' + xLabels[i] + "</text>");
    }
  }

  parts.push('<text class="svg-cap" x="' + left + '" y="' + (height - 16) + '">' + subcaption + "</text>");
  parts.push("</svg>");
  return parts.join("\n");
}

function loadSavedPriceSelection() {
  try {
    const raw = window.localStorage.getItem(PRICE_SELECTION_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    // A blocked or unavailable localStorage (private browsing, disabled
    // site data) must never break the page: fall back to no saved
    // selection.
    return null;
  }
}

function savePriceSelection(series, date) {
  try {
    window.localStorage.setItem(
      PRICE_SELECTION_STORAGE_KEY,
      JSON.stringify({ series: series, date: date })
    );
  } catch (err) {
    // Same as above: saving is best-effort only.
  }
}

function renderSources(sourcesEl, sources, note) {
  sourcesEl.innerHTML = "";
  const list = sources || [];
  if (note) {
    // An entry's own note (what a correction fixed, a caveat about its
    // sources) is shown with its sources, as text, never as markup.
    const noteLi = document.createElement("li");
    noteLi.className = "entry-note";
    noteLi.textContent = "Note: " + note;
    sourcesEl.appendChild(noteLi);
  }
  if (list.length === 0) {
    const li = document.createElement("li");
    li.textContent = "No source recorded for this price entry.";
    sourcesEl.appendChild(li);
    return;
  }
  list.forEach((source) => {
    const li = document.createElement("li");
    if (source.url) {
      const a = document.createElement("a");
      a.href = source.url;
      a.textContent = source.name;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      li.appendChild(a);
    } else {
      li.appendChild(document.createTextNode(source.name));
    }
    const checkedAt = document.createElement("span");
    checkedAt.className = "checked-at";
    checkedAt.textContent = " — checked " + source.checked_at;
    li.appendChild(checkedAt);
    sourcesEl.appendChild(li);
  });
}

function wireUpPricePanel() {
  const data = window.MILESTONE_DATA;
  const select = document.getElementById("price-series-select");
  const dateInput = document.getElementById("price-date");
  const inputPrice = document.getElementById("price-input");
  const outputPrice = document.getElementById("price-output");
  const cacheReadPrice = document.getElementById("price-cache-read");
  const cacheCreationPrice = document.getElementById("price-cache-creation");
  const cacheCreation1hPrice = document.getElementById("price-cache-creation-1h");
  // Every number field is paired with a range slider carrying the
  // same value, either one editable, always kept in sync (see
  // linkNumberAndRange below).
  const inputPriceRange = document.getElementById("price-input-range");
  const outputPriceRange = document.getElementById("price-output-range");
  const cacheReadPriceRange = document.getElementById("price-cache-read-range");
  const cacheCreationPriceRange = document.getElementById("price-cache-creation-range");
  const cacheCreation1hPriceRange = document.getElementById("price-cache-creation-1h-range");
  const sourcesEl = document.getElementById("price-sources");
  const costChartEl = document.getElementById("chart-cost");
  const rows = document.querySelectorAll("[data-milestone-tokens]");
  const tokensList = Array.from(rows).map((row) => JSON.parse(row.getAttribute("data-milestone-tokens")));
  const xLabels = tokensList.map((_, i) => "M" + (i + 1));

  const options = buildSeriesOptions(data.prices);
  options.forEach((option) => {
    const el = document.createElement("option");
    el.value = option.value;
    el.textContent = option.label;
    select.appendChild(el);
  });

  dateInput.value = data.today;
  const initialSeries = defaultSeriesKey(options, data.default_series);
  if (initialSeries) {
    select.value = initialSeries;
  }

  const saved = loadSavedPriceSelection();
  if (saved) {
    if (saved.series && options.some((option) => option.value === saved.series)) {
      select.value = saved.series;
    }
    if (saved.date) {
      dateInput.value = saved.date;
    }
  }

  function recalculate() {
    const priceEntry = {
      input_price: parseFloat(inputPrice.value) || 0,
      output_price: parseFloat(outputPrice.value) || 0,
      cache_read_price: parseFloat(cacheReadPrice.value) || 0,
      cache_creation_price: parseFloat(cacheCreationPrice.value) || 0,
    };
    // A blank 1-hour field means "this entry has no 1-hour write price",
    // not zero: it stays undefined, so calculateCost leaves any 1-hour
    // cache writes unpriced instead of pricing them for free.
    const oneHourPrice = parseFloat(cacheCreation1hPrice.value);
    if (!isNaN(oneHourPrice)) {
      priceEntry.cache_creation_1h_price = oneHourPrice;
    }
    rows.forEach((row) => {
      const tokens = JSON.parse(row.getAttribute("data-milestone-tokens"));
      const cost = calculateCost(tokens, priceEntry);
      const cell = row.querySelector("[data-live-cost]");
      if (cell) {
        cell.textContent = cost === null ? "-" : "$" + cost.toFixed(4);
        cell.title = cost === null && (tokens.cache_creation_1h || 0) > 0
          ? "1-hour cache writes cannot be priced: this price entry has no 1-hour write price"
          : "";
      }
    });
    if (costChartEl && tokensList.length > 0) {
      const costCumulative = cumulativeCostValues(tokensList, priceEntry);
      costChartEl.innerHTML = growthChartSVG(
        costCumulative, xLabels, (v) => "$" + v.toFixed(2),
        "Recorded cost per milestone", "Cumulative recorded cost (USD)", 260
      );
    }
  }

  function applySelection() {
    const [provider, model] = select.value.split("::");
    const targetDate = dateInput.value || data.today;
    const latest = priceForSelection(data.prices, provider, model, targetDate);
    if (latest) {
      inputPrice.value = latest.input_price;
      outputPrice.value = latest.output_price;
      cacheReadPrice.value = latest.cache_read_price;
      cacheCreationPrice.value = latest.cache_creation_price;
      if (inputPriceRange) inputPriceRange.value = latest.input_price;
      if (outputPriceRange) outputPriceRange.value = latest.output_price;
      if (cacheReadPriceRange) cacheReadPriceRange.value = latest.cache_read_price;
      if (cacheCreationPriceRange) cacheCreationPriceRange.value = latest.cache_creation_price;
      // An entry without a 1-hour write price leaves the field blank (and
      // the slider at 0) rather than copying the 5-minute price into it.
      const oneHour = hasOneHourPrice(latest) ? latest.cache_creation_1h_price : "";
      cacheCreation1hPrice.value = oneHour;
      if (cacheCreation1hPriceRange) cacheCreation1hPriceRange.value = oneHour === "" ? 0 : oneHour;
    }
    if (sourcesEl) {
      renderSources(sourcesEl, latest ? latest.sources : [], latest ? latest.note : null);
    }
    recalculate();
    savePriceSelection(select.value, targetDate);
  }

  // Keeps a number input and its paired range slider showing the same
  // value regardless of which one the reader actually moves.
  function linkNumberAndRange(numberEl, rangeEl) {
    if (!numberEl || !rangeEl) {
      return;
    }
    numberEl.addEventListener("input", () => {
      rangeEl.value = numberEl.value;
      recalculate();
    });
    rangeEl.addEventListener("input", () => {
      numberEl.value = rangeEl.value;
      recalculate();
    });
  }

  select.addEventListener("change", applySelection);
  dateInput.addEventListener("change", applySelection);
  linkNumberAndRange(inputPrice, inputPriceRange);
  linkNumberAndRange(outputPrice, outputPriceRange);
  linkNumberAndRange(cacheReadPrice, cacheReadPriceRange);
  linkNumberAndRange(cacheCreationPrice, cacheCreationPriceRange);
  linkNumberAndRange(cacheCreation1hPrice, cacheCreation1hPriceRange);
  applySelection();
}

if (typeof module !== "undefined") {
  module.exports = {
    priceForSelection, calculateCost, buildSeriesOptions,
    cumulativeCostValues, growthChartSVG, defaultSeriesKey,
  };
}
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", wireUpPricePanel);
}
