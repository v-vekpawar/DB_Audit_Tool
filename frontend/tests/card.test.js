// Card renderer tests (no browser, no database): node --test frontend/
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const logic = require("../app.js");

const fixture = JSON.parse(fs.readFileSync(
  path.join(__dirname, "fixture-result.json"), "utf-8"));
const byName = Object.fromEntries(fixture.tables.map((t) => [t.name, t]));

test("transitive card has header, arrows, score line and boxes", () => {
  const svg = logic.buildCardSVG("t_3nf_transitive",
    byName.t_3nf_transitive, "faulted");
  assert.match(svg, /<svg /);
  assert.match(svg, /t_3nf_transitive/);
  assert.match(svg, />2NF</);
  assert.match(svg, /PK/);
  assert.match(svg, /DET/);
  assert.match(svg, /marker-end/);
  assert.match(svg, /Confidence Score: 96 \(chance of coincidence about 1 in 10\^48\)/);
  assert.match(svg, /34 distinct values/);
  assert.match(svg, /supported by customers_ref\.customer_name: 98\.4% match/);
  assert.match(svg, /Why this fails 3NF/);
  assert.match(svg, /Recommendation:/);
  assert.match(svg, /e\.g\. 17 → Acme in 14 rows/);
  assert.doesNotMatch(svg, /possibly/);
});

test("partial + near-FD shows exceptions", () => {
  const svg = logic.buildCardSVG("t_2nf_partial", byName.t_2nf_partial,
    "faulted");
  assert.match(svg, />1NF</);
  assert.match(svg, /2 exception rows/);
  assert.match(svg, /\(exception\)/);
  assert.match(svg, /only part of the key/);
});

test("1NF cards render without FDs", () => {
  const svg = logic.buildCardSVG("t_1nf_delimited", byName.t_1nf_delimited,
    "faulted");
  assert.match(svg, /below 1NF/);
  assert.match(svg, /comma-separated lists/);
  assert.doesNotMatch(svg, /Confidence Score/);
  const rep = logic.buildCardSVG("t_1nf_repeating", byName.t_1nf_repeating,
    "faulted");
  assert.match(svg, /<svg /);
  assert.match(rep, /repeating group/);
});

test("column names are escaped", () => {
  const svg = logic.buildCardSVG("t_1nf_repeating", byName.t_1nf_repeating,
    "faulted");
  assert.doesNotMatch(svg, /<script>alert/);
  assert.match(svg, /&lt;script&gt;/);
});

test("suspected card is dimmed with equivalent arrow and sampled note", () => {
  const svg = logic.buildCardSVG("t_suspected", byName.t_suspected,
    "suspected");
  assert.match(svg, /opacity="0\.72"/);
  assert.match(svg, /possibly 2NF/);
  assert.match(svg, /marker-start/);
  assert.match(svg, /\(equivalent\)/);
  assert.match(svg, /sampled/);
  assert.match(svg, /FK/);
});

test("wrapText wraps long reasons", () => {
  const lines = logic.wrapText("a b c d e f", 5);
  assert.deepEqual(lines, ["a b c", "d e f"]);
  assert.deepEqual(logic.wrapText("", 10), [""]);
});
