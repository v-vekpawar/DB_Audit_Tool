// Card renderer tests (no browser, no database): node --test frontend/
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const logic = require("../app.js");

const fixture = JSON.parse(fs.readFileSync(
  path.join(__dirname, "fixture-result.json"), "utf-8"));
const byName = Object.fromEntries(fixture.tables.map((t) => [t.name, t]));
const collapsed = () => new Set();

test("transitive card has header, graph, FD line and collapsed toggles", () => {
  const html = logic.buildCardHTML("t_3nf_transitive",
    byName.t_3nf_transitive, "faulted", collapsed());
  assert.match(html, /t_3nf_transitive/);
  assert.match(html, />2NF</);
  assert.match(html, /300 rows · 4 columns/);
  assert.match(html, /order_id<\/span>/);
  assert.match(html, /Transitive dependency \(violates 3NF\)\./);
  assert.match(html, /<svg class="rc-graph"/);
  assert.match(html, /transitive<\/text>/);
  assert.match(html, /FD 1/);
  assert.match(html, /customer_id.*→ customer_name/);
  assert.match(html, /High · 96/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /Evidence ▾/);
  assert.match(html, /<td>17<\/td><td>Acme<\/td><td>14<\/td>/);
  assert.match(html, /Also seen in customers_ref\.customer_name: 98\.4% match/);
  assert.match(html, /34 distinct values/);
  assert.match(html, /Recommendation\./);
  assert.match(html, /Show suggested tables ▾/);
  assert.match(html, /new table \(keyed by customer_id\)/);
  assert.match(html, /Illustrative sketch/);
  assert.doesNotMatch(html, /possibly/);
  assert.doesNotMatch(html, /opacity/);
});

test("partial + near-FD shows composite key and exception rows", () => {
  const html = logic.buildCardHTML("t_2nf_partial", byName.t_2nf_partial,
    "faulted", collapsed());
  assert.match(html, />1NF</);
  assert.match(html, /order_id, product_id/);
  assert.match(html, /Partial dependency \(violates 2NF\)\./);
  assert.match(html, /2 exception rows/);
  assert.match(html, /\(exception\)/);
  assert.match(html, /only part of the key/);
  assert.doesNotMatch(html, /0 exceptions/);
});

test("1NF cards render chips without graph, FD lines or toggles", () => {
  const html = logic.buildCardHTML("t_1nf_delimited", byName.t_1nf_delimited,
    "faulted", collapsed());
  assert.match(html, /below 1NF/);
  assert.match(html, /Multi-valued cells \(below 1NF\)\./);
  assert.match(html, /comma-separated lists/);
  assert.match(html, /red, green, blue/);
  assert.doesNotMatch(html, /rc-graph/);
  assert.doesNotMatch(html, /FD 1/);
  assert.doesNotMatch(html, /Evidence/);
  assert.doesNotMatch(html, /suggested tables/);
  const rep = logic.buildCardHTML("t_1nf_repeating", byName.t_1nf_repeating,
    "faulted", collapsed());
  assert.match(rep, /Repeating group \(below 1NF\)\./);
  assert.match(rep, /repeating group/);
});

test("column names are escaped", () => {
  const evil = {
    name: "t_evil",
    row_count: 10,
    status: "faulted",
    normal_form: "2NF",
    sampled: false,
    keys: [{ columns: ["<b>id</b>"], source: "declared" }],
    key_confidence: "High",
    columns: [{ name: "<b>id</b>" }, { name: "a/value" }],
    violations: [{ form: "3NF", kind: "transitive",
      determinant: ["<b>id</b>"], dependents: ["a/value"],
      reason: "x < y & z" }],
    fds: [{ determinant: ["<b>id</b>"], dependent: "a/value",
      confidence: 90, equivalent: false, exceptions: 0,
      coincidence: { text: "1 in 10^9" },
      evidence: { distinct_det: 1, repeated_rows: 1,
        candidates_tested: 1, examples: [] } }],
    recommendation: "Fix <it>.",
  };
  const html = logic.buildCardHTML("t_<evil>", evil, "faulted", collapsed());
  assert.doesNotMatch(html, /<script|<b>id<\/b>|t_<evil>|x < y/);
  assert.match(html, /&lt;b&gt;id&lt;\/b&gt;/);
  assert.match(html, /t_&lt;evil&gt;/);
  assert.match(html, /x &lt; y &amp; z/);
});

test("suspected card is amber with equivalent arrow and sampled note", () => {
  const html = logic.buildCardHTML("t_suspected", byName.t_suspected,
    "suspected", collapsed());
  assert.doesNotMatch(html, /opacity/);
  assert.match(html, /possibly 2NF/);
  assert.match(html, /pill-nf-suspected/);
  assert.match(html, /marker-start/);
  assert.match(html, /EQ/);
  assert.match(html, /equivalent columns/);
  assert.match(html, /sampled \(first 50,000 rows\)/);
});

test("open toggles render expanded panels", () => {
  const open = new Set(["t_3nf_transitive‖ev‖0", "t_3nf_transitive‖sugg"]);
  const html = logic.buildCardHTML("t_3nf_transitive",
    byName.t_3nf_transitive, "faulted", open);
  assert.match(html, /aria-expanded="true"/);
  assert.match(html, /Evidence ▴/);
  assert.match(html, /Show suggested tables ▴/);
  assert.doesNotMatch(html, /rc-evidence" id="ev-t_3nf_transitive-0" hidden/);
});

test("missing keys fall back without crashing", () => {
  const bare = { ...byName.t_3nf_transitive };
  delete bare.keys;
  delete bare.key_confidence;
  delete bare.row_count;
  const html = logic.buildCardHTML("t_3nf_transitive", bare, "faulted",
    collapsed());
  assert.match(html, /order_id/);
  assert.match(html, /Transitive dependency/);
});

test("confLevel thresholds", () => {
  assert.equal(logic.confLevel(96), "High");
  assert.equal(logic.confLevel(80), "High");
  assert.equal(logic.confLevel(79), "Medium");
  assert.equal(logic.confLevel(60), "Medium");
  assert.equal(logic.confLevel(59), "Low");
});

test("wrapText wraps long reasons", () => {
  const lines = logic.wrapText("a b c d e f", 5);
  assert.deepEqual(lines, ["a b c", "d e f"]);
  assert.deepEqual(logic.wrapText("", 10), [""]);
});
