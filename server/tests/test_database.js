// Runs the real schema in a real Postgres, including who is allowed to see and change what.
import assert from "node:assert/strict";
import { before, describe, it } from "node:test";
import { addUser, makeDatabase, pgDb } from "./helpers.js";

const ALICE = "00000000-0000-0000-0000-00000000000a", BOB = "00000000-0000-0000-0000-00000000000b", MONTH = "2026-11";
const usage = { input: 10, output: 5, cacheRead: 0, cacheWrite: 0 };
const finish = (db, runId, status, cost) => db.advisorFinish(runId, { status, costMicros: cost, tokens: usage, toolCalls: 1, model: "m" });

describe("member database", () => {
  let pg, db;
  const as = async (user, sql, params) => {
    await pg.query(`set request.jwt.sub = '${user || ""}'`);
    await pg.query(`set role ${user ? "authenticated" : "anon"}`);
    try {
      return await pg.query(sql, params);
    } finally {
      await pg.query("reset role");
    }
  };
  before(async () => {
    pg = await makeDatabase();
    db = pgDb(pg);
    await addUser(pg, ALICE, "a@example.test");
    await addUser(pg, BOB, "b@example.test");
    await db.saveMembership(ALICE, { status: "active" });
  });

  it("lets people read only their own membership", async () => {
    assert.equal((await as(ALICE, "select * from memberships")).rows.length, 1);
    assert.equal((await as(BOB, "select * from memberships")).rows.length, 0);
    assert.equal((await as(null, "select * from memberships")).rows.length, 0);
  });

  it("doesn't let anyone make themselves a paid member from the browser", async () => {
    await assert.rejects(as(ALICE, "insert into memberships (user_id, status) values ($1, 'active')", [BOB]));
    await assert.rejects(as(BOB, "insert into memberships (user_id, status) values ($1, 'active')", [BOB]));
    await as(BOB, "update memberships set status = 'active'");
    await as(ALICE, "delete from memberships");
    assert.equal((await db.membership(ALICE)).status, "active");
    assert.equal(await db.membership(BOB), null);
  });

  it("keeps saved drafts private to their owner", async () => {
    await as(ALICE, "insert into saved_items (title, data) values ('Mine', '{}')");
    assert.equal((await as(ALICE, "select * from saved_items")).rows.length, 1);
    assert.equal((await as(BOB, "select * from saved_items")).rows.length, 0);
    await assert.rejects(as(BOB, "insert into saved_items (user_id, title) values ($1, 'Forged')", [ALICE]));
    assert.equal((await as(BOB, "update saved_items set title = 'Taken' returning id")).rows.length, 0);
    assert.equal((await as(BOB, "delete from saved_items returning id")).rows.length, 0);
    assert.equal((await as(null, "select * from saved_items")).rows.length, 0);
  });

  it("rejects an oversized draft", async () => {
    await assert.rejects(as(ALICE, "insert into saved_items (title, data) values ('Big', $1)", [JSON.stringify({ text: Array.from({ length: 60000 }, () => Math.random().toString(36).slice(2, 3)).join("") })]));
  });

  it("doesn't let the browser call the spending functions or see other people's usage", async () => {
    await assert.rejects(as(ALICE, "select * from advisor_begin($1, $2, 1000, 999999999, 1)", [ALICE, MONTH]));
    await assert.rejects(as(null, "select advisor_finish(gen_random_uuid(), 'answered', 0, 0, 0, 0, 0, 0, 'm')"));
    const begun = await db.advisorBegin(BOB, "2020-01", 10, 10_000_000, 100);
    await finish(db, begun.runId, "answered", 50);
    assert.equal((await as(ALICE, "select * from advisor_runs where month = '2020-01'")).rows.length, 0);
    assert.equal((await as(BOB, "select * from advisor_runs where month = '2020-01'")).rows.length, 1);
    assert.equal((await as(BOB, "update advisor_runs set cost_micros = 0 returning id")).rows.length, 0);
    assert.equal((await as(BOB, "delete from advisor_runs returning id")).rows.length, 0);
  });

  it("stops at the person's monthly limit, and gives a question back after a server failure", async () => {
    const month = "2026-01";
    const first = await db.advisorBegin(ALICE, month, 2, 10_000_000, 100);
    assert.equal(first.result, "ok");
    assert.equal((await db.advisorBegin(ALICE, month, 2, 10_000_000, 100)).result, "busy");
    await finish(db, first.runId, "failed", 10);
    assert.equal(await db.questionsUsed(ALICE, month), 0);
    await finish(db, (await db.advisorBegin(ALICE, month, 2, 10_000_000, 100)).runId, "answered", 10);
    await finish(db, (await db.advisorBegin(ALICE, month, 2, 10_000_000, 100)).runId, "stopped", 10);
    assert.equal((await db.advisorBegin(ALICE, month, 2, 10_000_000, 100)).result, "user_limit");
  });

  it("counts failed runs' cost and unfinished runs' reserve toward the monthly budget", async () => {
    const month = "2026-02";
    const failed = await db.advisorBegin(ALICE, month, 9, 1000, 400);
    await finish(db, failed.runId, "failed", 300);
    const running = await db.advisorBegin(BOB, month, 9, 1000, 400);
    assert.equal(running.result, "ok");
    assert.equal((await db.advisorBegin(ALICE, month, 9, 1000, 400)).result, "budget_reached");
    await finish(db, running.runId, "answered", 100);
    assert.equal((await db.advisorBegin(ALICE, month, 9, 1000, 400)).result, "ok");
  });

  it("doesn't overrun the budget when questions start at the same moment", async () => {
    const month = "2026-03";
    const users = Array.from({ length: 12 }, (_, i) => `00000000-0000-0000-0001-${String(i).padStart(12, "0")}`);
    await Promise.all(users.map((u, i) => addUser(pg, u, `${i}@example.test`)));
    const results = await Promise.all(users.map(u => db.advisorBegin(u, month, 9, 1000, 300)));
    assert.equal(results.filter(r => r.result === "ok").length, 3);
    assert.equal(results.filter(r => r.result === "budget_reached").length, 9);
  });

  it("can't change a finished run", async () => {
    const begun = await db.advisorBegin(BOB, "2026-04", 9, 1000, 100);
    await finish(db, begun.runId, "answered", 90);
    await finish(db, begun.runId, "failed", 1);
    const row = (await db.asService("select status, cost_micros from advisor_runs where id = $1", [begun.runId])).rows[0];
    assert.deepEqual([row.status, Number(row.cost_micros)], ["answered", 90]);
  });

  it("deletes a person's saved work with their account, but keeps the spending total", async () => {
    const carol = "00000000-0000-0000-0000-00000000000c";
    await addUser(pg, carol, "c@example.test");
    await db.saveMembership(carol, { status: "active" });
    await as(carol, "insert into saved_items (title) values ('Draft')");
    const begun = await db.advisorBegin(carol, "2026-05", 9, 5000, 100);
    await finish(db, begun.runId, "answered", 77);
    await db.deleteUser(carol);
    assert.equal(await db.membership(carol), null);
    assert.equal((await pg.query("select * from saved_items where user_id = $1", [carol])).rows.length, 0);
    const kept = await pg.query("select user_id, cost_micros from advisor_runs where id = $1", [begun.runId]);
    assert.deepEqual([kept.rows[0].user_id, Number(kept.rows[0].cost_micros)], [null, 77]);
  });

  it("reports average cost per question", async () => {
    const rows = (await pg.query("select * from advisor_cost_by_month where month = '2026-05'")).rows;
    assert.equal(rows[0].questions, 1);
  });
});
