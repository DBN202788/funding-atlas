// Stand-ins for outside services: a real Postgres (in memory) running the real schema,
// and a local web server that speaks the Anthropic streaming format to the real SDK.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PGlite } from "@electric-sql/pglite";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SERVER_ROOT = path.resolve(HERE, "..");
export const PROJECT_ROOT = path.resolve(SERVER_ROOT, "..");

export async function makeDatabase() {
  const pg = new PGlite();
  await pg.exec(`
    create schema auth;
    create table auth.users (id uuid primary key, email text);
    create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
    create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.sub', true), '')::uuid $$;
    grant usage on schema auth, public to anon, authenticated, service_role;
    grant execute on function auth.uid() to anon, authenticated, service_role;
    alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  `);
  await pg.exec(fs.readFileSync(path.join(SERVER_ROOT, "supabase", "schema.sql"), "utf8"));
  return pg;
}

/** Same operations as the Supabase-backed db in lib/deps.js, calling the same SQL functions. */
export function pgDb(pg) {
  const asService = async (sql, params) => {
    await pg.query("set role service_role");
    try {
      return await pg.query(sql, params);
    } finally {
      await pg.query("reset role");
    }
  };
  return {
    pg,
    asService,
    membership: async id => (await asService("select * from memberships where user_id = $1", [id])).rows[0] || null,
    membershipByCustomer: async id => (await asService("select * from memberships where stripe_customer_id = $1", [id])).rows[0] || null,
    async saveMembership(id, fields) {
      const keys = Object.keys(fields);
      const sets = keys.map(k => `${k} = excluded.${k}`).concat("updated_at = now()").join(", ");
      await asService(`insert into memberships (user_id, ${keys.join(", ")}) values ($1, ${keys.map((_, i) => `$${i + 2}`).join(", ")}) on conflict (user_id) do update set ${sets}`, [id, ...keys.map(k => fields[k])]);
    },
    questionsUsed: async (id, month) => Number((await asService("select count(*) c from advisor_runs where user_id = $1 and month = $2 and status <> 'failed'", [id, month])).rows[0].c),
    async advisorBegin(id, month, limit, cap, reserve) {
      const row = (await asService("select * from advisor_begin($1, $2, $3, $4, $5)", [id, month, limit, cap, reserve])).rows[0];
      return { result: row.result, runId: row.run_id };
    },
    advisorFinish: (run, o) => asService("select advisor_finish($1, $2, $3, $4, $5, $6, $7, $8, $9)", [run, o.status, o.costMicros, o.tokens.input, o.tokens.output, o.tokens.cacheRead, o.tokens.cacheWrite, o.toolCalls, o.model]),
    deleteUser: id => pg.query("delete from auth.users where id = $1", [id]),
  };
}

export const addUser = (pg, id, email) => pg.query("insert into auth.users (id, email) values ($1, $2)", [id, email]);

const sse = (event, data) => `event: ${event}\ndata: ${JSON.stringify({ type: event, ...data })}\n\n`;

/** Writes one API response as the same event stream the real API sends. */
export function streamMessage(res, { id = "msg_test", model = "claude-opus-5-5", content, stop_reason = "end_turn", usage = { input_tokens: 1000, output_tokens: 200 }, holdOpen = false }) {
  res.writeHead(200, { "content-type": "text/event-stream" });
  res.write(sse("message_start", { message: { id, type: "message", role: "assistant", model, content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: usage.input_tokens, output_tokens: 1, cache_read_input_tokens: usage.cache_read_input_tokens || 0, cache_creation_input_tokens: usage.cache_creation_input_tokens || 0 } } }));
  content.forEach((block, index) => {
    if (block.type === "text") {
      res.write(sse("content_block_start", { index, content_block: { type: "text", text: "" } }));
      for (const piece of block.text.match(/.{1,20}/gs) || []) res.write(sse("content_block_delta", { index, delta: { type: "text_delta", text: piece } }));
    } else if (block.type === "tool_use") {
      res.write(sse("content_block_start", { index, content_block: { type: "tool_use", id: block.id, name: block.name, input: {} } }));
      res.write(sse("content_block_delta", { index, delta: { type: "input_json_delta", partial_json: block.rawInput ?? JSON.stringify(block.input) } }));
    } else {
      res.write(sse("content_block_start", { index, content_block: block }));
    }
    res.write(sse("content_block_stop", { index }));
  });
  if (holdOpen) return;
  res.write(sse("message_delta", { delta: { stop_reason, stop_sequence: null }, usage: { ...usage } }));
  res.write(sse("message_stop", {}));
  res.end();
}

/** A local stand-in for the Anthropic API. Each reply function answers one request, in order. */
export async function fakeAnthropic(replies) {
  const requests = [];
  const queue = [...replies];
  const server = http.createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    requests.push({ headers: req.headers, url: req.url, body: JSON.parse(body) });
    const reply = queue.shift();
    if (!reply) {
      res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ type: "error", error: { type: "api_error", message: "no reply queued" } }));
      return;
    }
    await reply(res, requests.at(-1));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${server.address().port}`, requests, queue, close: () => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }) };
}

export const replyWith = spec => res => streamMessage(res, spec);

export function testConfig(overrides = {}) {
  return {
    siteUrl: "https://site.test/atlas/", allowedOrigins: ["https://site.test"], model: "claude-opus-5-5", effort: "medium", maxTokens: 8000, maxRounds: 6, timeoutMs: 20000,
    freeQuestions: 2, memberQuestions: 5, monthlyBudgetMicros: 25_000_000, questionReserveMicros: 1_000_000,
    stripeWebhookSecret: "whsec_test", stripePriceId: "price_test", automaticTax: false, paymentsMode: "test", paymentsOpen: true, ...overrides,
  };
}

/** A small data store with the same shape as the published files. */
export function fakeStore() {
  const records = [
    { id: "FL-FY2026-27-1", state: "FL", year: "FY2026-27", name: "Youth Mentoring", organization: "Mentors Inc", category: "Youth", sponsor: "A", requested: 100, appropriated: 50, vetoed: null, status: "Funded", form: "1", hay: "youth mentoring mentors inc" },
    { id: "FL-FY2026-27-2", state: "FL", year: "FY2026-27", name: "Mentor Center", organization: "Center", category: "Youth", sponsor: "B", requested: 300, appropriated: null, vetoed: 300, status: "Vetoed by Governor", form: "2", hay: "mentor center" },
    { id: "DE-FY2027-9", state: "DE", year: "FY2027", name: "Arts Group", organization: "Arts", category: "Arts", sponsor: "", requested: null, appropriated: 75, vetoed: null, status: "Appropriated", form: null, hay: "arts group" },
    { id: "LA-26RS-3", state: "LA", year: "2026 Regular Session", name: "Ward Project", organization: "Ward", category: "Community", sponsor: "", requested: 150, appropriated: 120, vetoed: null, status: "Appropriated (name match)", form: "3", hay: "ward project" },
  ];
  return {
    records: async () => ({ records, byId: new Map(records.map(r => [r.id, r])) }),
    meta: async () => ({ form_years: ["FY2026-27"] }),
    forms: async key => key === "FY2026-27" ? { 1: { organization: "Mentors Inc", description: "x".repeat(20000) } } : { 3: { organization: "Ward", act_lines: ["Act 4 p.140"] } },
    compare: async () => [{ fiscal_year: "FY2026-27", forms_compared: 500, forms_read: 500, by_org_type: [], nonprofit: { counts: { funded: 40 }, features: [{ key: "has_match", funded: 60 }] }, all: { counts: { funded: 90 }, features: [] } }],
    requirements: async () => ({ FL: { title: "Florida" }, DE: { title: "Delaware" }, LA: { title: "Louisiana" } }),
  };
}
