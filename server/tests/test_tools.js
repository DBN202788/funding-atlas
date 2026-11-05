// The server's tools must agree with the website and with the real published data.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { createDataStore, DataUnavailable } from "../lib/data.js";
import { RULES } from "../lib/prompt.js";
import { inputProblem, OUTCOMES, runTool, TOOLS } from "../lib/tools.js";
import { fakeStore, PROJECT_ROOT, SERVER_ROOT } from "./helpers.js";

const WEB = path.join(PROJECT_ROOT, "web");
const piece_fl = 'FL: { year: "FY2026-27", rates: true';
const page = fs.readFileSync(path.join(WEB, "index.html"), "utf8").replaceAll(String.fromCharCode(13), "");
const localFetch = async url => {
  const file = path.join(WEB, "data", url.split("/").pop());
  return fs.existsSync(file) ? new Response(fs.readFileSync(file)) : new Response("", { status: 404 });
};
const realStore = () => createDataStore({ baseUrl: "https://data.test", fetchImpl: localFetch });
const call = async (name, input, store = fakeStore()) => {
  const result = await runTool({ id: "t", name, input }, store);
  return result.is_error ? result : JSON.parse(result.content);
};

describe("advisor tools", () => {
  it("uses exactly the website's rules", () => {
    const match = page.match(/const RULES = `([\s\S]*?)`;/);
    assert.equal(RULES, match[1]);
  });

  it("offers every outcome the website knows", () => {
    const block = page.match(/const STATUS_STYLE = \{([\s\S]*?)\n\};/)[1];
    const keys = [...block.matchAll(/^\s*"([^"]+)":/gm)].map(m => m[1]);
    assert.deepEqual([...OUTCOMES].sort(), keys.sort());
  });

  it("offers the same tools as the website", () => {
    const names = [...page.matchAll(/\{ name: "(\w+)", description: "/g)].map(m => m[1]);
    assert.deepEqual(TOOLS.map(t => t.name), names);
  });

  it("checks tool input", () => {
    assert.equal(inputProblem("get_requirements", { state: "FL" }), null);
    assert.match(inputProblem("get_requirements", {}), /required/);
    assert.match(inputProblem("get_requirements", { state: "TX" }), /one of/);
    assert.match(inputProblem("search_funding", { query: 5 }), /text/);
    assert.match(inputProblem("search_funding", { sql: "x" }), /Unknown field/);
    assert.match(inputProblem("delete_all", {}), /Unknown tool/);
    assert.match(inputProblem("search_funding", null), /object/);
  });

  it("searches with every word required, biggest awards first", async () => {
    const found = await call("search_funding", { query: "mentor", state: "FL" });
    assert.equal(found.total_matches, 2);
    assert.deepEqual(found.records.map(r => r.id), ["FL-FY2026-27-1", "FL-FY2026-27-2"]);
    assert.equal((await call("search_funding", { query: "mentor arts" })).total_matches, 0);
    assert.equal((await call("search_funding", { outcome: "Vetoed by Governor" })).total_matches, 1);
  });

  it("shares results across states when no state is named", async () => {
    const store = realStore();
    const all = await call("search_funding", { query: "youth" }, store);
    assert.equal(all.records.length, 15);
    assert.deepEqual([...new Set(all.records.map(r => r.state))].sort(), ["DE", "FL", "LA"]);
    assert.equal(all.total_matches, all.by_state.FL.matches + all.by_state.DE.matches + all.by_state.LA.matches);
    assert.ok(all.by_state.DE.budgeted > 0);
    const florida = await call("search_funding", { query: "youth", state: "FL" }, store);
    assert.ok(florida.records.every(r => r.state === "FL"));
    assert.equal(florida.by_state.DE.matches, 0);
  });

  it("summarizes by program area", async () => {
    const [youth] = await call("summarize_funding", { state: "FL" });
    assert.deepEqual(youth, { area: "Youth", records: 2, in_budget: 1, vetoed: 1, requested: 400, budgeted: 50 });
  });

  it("describes how similar requests fared, without a prediction", async () => {
    const fake = await call("comparable_outcomes", { query: "mentor", state: "FL" });
    assert.deepEqual([fake.matches, fake.in_budget, fake.vetoed, fake.in_budget_percent, fake.median_budgeted], [2, 1, 1, null, 50]);
    assert.match(fake.limits, /doesn't predict/);
    assert.equal(JSON.stringify(fake).includes("chance"), false);
    assert.equal((await call("comparable_outcomes", { state: "FL" })).is_error, true);
  });

  it("gets real comparable outcomes, and shows no rates where they'd mislead", async () => {
    const store = realStore();
    const fl = await call("comparable_outcomes", { query: "mentor", state: "FL" }, store);
    assert.equal(fl.matches, fl.in_budget + fl.vetoed + fl.other);
    assert.ok(fl.matches >= 10);
    assert.equal(fl.in_budget_percent, Math.round(fl.in_budget / fl.matches * 100));
    assert.ok(fl.lowest_budgeted <= fl.median_budgeted && fl.median_budgeted <= fl.highest_budgeted);
    const everyFl = await call("comparable_outcomes", { query: "a", state: "FL" }, store);
    assert.equal(everyFl.year, "FY2026-27");
    const de = await call("comparable_outcomes", { query: "arts", state: "DE" }, store);
    assert.ok(de.matches > 0);
    assert.deepEqual([de.in_budget_percent, de.vetoed_percent], [null, null]);
    assert.match(de.limits, /awards only/);
    const la = await call("comparable_outcomes", { query: "ward", state: "LA" }, store);
    assert.match(la.limits, /matching organization names/);
  });

  it("uses the same outcome rules as the website", () => {
    for (const piece of ['FL: { year: "FY2026-27", rates: true', 'LA: { year: "2026 Regular Session", rates: true', 'DE: { year: "FY2027", rates: false']) assert.ok(page.includes(piece), piece);
    assert.ok(fs.readFileSync(path.join(SERVER_ROOT, "lib", "tools.js"), "utf8").includes(piece_fl));
  });

  it("reads only applications that have been read", async () => {
    assert.equal((await call("get_application", { id: "LA-26RS-3" })).organization, "Ward");
    assert.equal((await call("get_application", { id: "DE-FY2027-9" })).is_error, true);
    assert.equal((await call("get_application", { id: "nope" })).is_error, true);
  });

  it("gets the same answers from the real published data as the audited figures", async () => {
    const store = realStore();
    const ward = await call("search_funding", { query: "18th ward", state: "LA" }, store);
    const record = ward.records[0];
    assert.equal(record.budgeted, 120000);
    assert.equal(record.requested, 150000);
    const form = await call("get_application", { id: record.id }, store);
    assert.equal(form.is_error, undefined);
    assert.ok(form.summary);
    const vetoed = await call("search_funding", { state: "LA", outcome: "Vetoed by Governor" }, store);
    assert.equal(vetoed.total_matches, 26);
    const fl = await call("summarize_funding", { state: "FL", fiscal_year: "FY2026-27" }, store);
    assert.equal(fl.reduce((sum, area) => sum + area.records, 0), 2808);
    assert.equal(fl.reduce((sum, area) => sum + area.vetoed, 0), 478);
    assert.equal(fl.reduce((sum, area) => sum + area.in_budget, 0), 1376);
    assert.ok((await call("compare_applications", {}, store)).counts);
    assert.ok((await call("get_requirements", { state: "LA" }, store)).items.length > 5);
  });

  it("has no email addresses in the searchable text", async () => {
    const { records } = await realStore().records();
    assert.ok(records.length > 20000);
    assert.equal(/\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b/i.test(records.map(r => r.hay).join(" ")), false);
  });

  it("reports missing data clearly, and tries again next time", async () => {
    let healthy = false;
    const store = createDataStore({ baseUrl: "https://data.test", fetchImpl: async () => healthy ? new Response("{}") : new Response("", { status: 500 }) });
    await assert.rejects(store.meta(), DataUnavailable);
    healthy = true;
    assert.deepEqual(await store.meta(), {});
  });
});
