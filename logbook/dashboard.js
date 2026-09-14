// The only interactive part of the published dashboard: recomputing a
// milestone's cost under any provider, model, or historical price
// already in the ledger. Never makes a network call. The ledger and
// the milestone data are both embedded in the page at generation
// time, in window.MILESTONE_DATA, set by a script tag this file does
// not own.

function priceForSelection(pricesBySeries, provider, model, targetDate) {
  const key = provider + "::" + model;
  const entries = pricesBySeries[key] || [];
  const eligible = entries.filter((e) => e.effective_date <= targetDate);
  if (eligible.length === 0) {
    return null;
  }
  return eligible.reduce((a, b) => (a.effective_date > b.effective_date ? a : b));
}

function calculateCost(tokens, priceEntry) {
  if (!priceEntry) {
    return null;
  }
  const amount =
    (tokens.input / 1000000) * priceEntry.input_price +
    (tokens.output / 1000000) * priceEntry.output_price +
    (tokens.cache_read / 1000000) * priceEntry.cache_read_price +
    (tokens.cache_creation / 1000000) * priceEntry.cache_creation_price;
  return Math.round(amount * 10000) / 10000;
}

function wireUpPricePanel() {
  const data = window.MILESTONE_DATA;
  const select = document.getElementById("price-series-select");
  const inputPrice = document.getElementById("price-input");
  const outputPrice = document.getElementById("price-output");
  const cacheReadPrice = document.getElementById("price-cache-read");
  const cacheCreationPrice = document.getElementById("price-cache-creation");
  const rows = document.querySelectorAll("[data-milestone-tokens]");

  function recalculate() {
    const priceEntry = {
      input_price: parseFloat(inputPrice.value) || 0,
      output_price: parseFloat(outputPrice.value) || 0,
      cache_read_price: parseFloat(cacheReadPrice.value) || 0,
      cache_creation_price: parseFloat(cacheCreationPrice.value) || 0,
    };
    rows.forEach((row) => {
      const tokens = JSON.parse(row.getAttribute("data-milestone-tokens"));
      const cost = calculateCost(tokens, priceEntry);
      const cell = row.querySelector("[data-live-cost]");
      if (cell) {
        cell.textContent = cost === null ? "-" : "$" + cost.toFixed(4);
      }
    });
  }

  function applySelection() {
    const [provider, model] = select.value.split("::");
    const latest = priceForSelection(data.prices, provider, model, data.today);
    if (latest) {
      inputPrice.value = latest.input_price;
      outputPrice.value = latest.output_price;
      cacheReadPrice.value = latest.cache_read_price;
      cacheCreationPrice.value = latest.cache_creation_price;
    }
    recalculate();
  }

  select.addEventListener("change", applySelection);
  [inputPrice, outputPrice, cacheReadPrice, cacheCreationPrice].forEach((el) =>
    el.addEventListener("input", recalculate)
  );
  applySelection();
}

if (typeof module !== "undefined") {
  module.exports = { priceForSelection, calculateCost };
}
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", wireUpPricePanel);
}
