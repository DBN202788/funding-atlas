// Runs the whole member experience on this computer with no accounts and no real money:
// the built website, the real server code and database rules, and stand-ins for sign-in email,
// Stripe's pages and the AI model. Usage: python -I pipeline/site.py, then node server/tests/fake-stack.js
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import Anthropic from "@anthropic-ai/sdk";
import Stripe from "stripe";
import { createDataStore } from "../lib/data.js";
import { advisor, checkout, deleteAccount, health, me, portal, stripeWebhook } from "../lib/handlers.js";
import { endpoint } from "../lib/http.js";
import { addUser, makeDatabase, PROJECT_ROOT, pgDb, streamMessage, testConfig } from "./helpers.js";

const PORT = Number(process.env.PORT || 8767);
const ORIGIN = `http://localhost:${PORT}`;
const DIST = path.join(PROJECT_ROOT, "dist");
const WEB_DATA = path.join(PROJECT_ROOT, "web", "data");
const WEBHOOK_SECRET = "whsec_fake";
const TYPES = { ".html": "text/html; charset=utf-8", ".json": "application/json", ".csv": "text/csv", ".txt": "text/plain" };

const pg = await makeDatabase();
const db = pgDb(pg);
const users = new Map(), tokens = new Map(), pendingCodes = new Map();
let lastLink = null;
const subscriptions = new Map();
const realStripe = new Stripe("sk_test_fake");

const fakeStripeApi = {
  webhooks: realStripe.webhooks,
  customers: { create: async () => ({ id: `cus_${randomUUID()}` }) },
  subscriptions: {
    retrieve: async id => subscriptions.get(id),
    list: async ({ customer }) => ({ data: [...subscriptions.values()].filter(s => s.customer === customer) }),
    cancel: async id => { subscriptions.get(id).status = "canceled"; },
  },
  checkout: { sessions: { create: async params => {
    const session = randomUUID();
    checkouts.set(session, params);
    return { url: `${ORIGIN}/dev/checkout?session=${session}` };
  } } },
  billingPortal: { sessions: { create: async params => ({ url: `${ORIGIN}/dev/portal?customer=${params.customer}&back=${encodeURIComponent(params.return_url)}` }) } },
};
const checkouts = new Map();

const config = testConfig({ siteUrl: `${ORIGIN}/`, allowedOrigins: [ORIGIN], stripeWebhookSecret: WEBHOOK_SECRET, freeQuestions: 2, memberQuestions: 50 });
const deps = {
  config, db, stripe: fakeStripeApi,
  store: createDataStore({ baseUrl: "http://data.local", fetchImpl: async url => {
    const file = path.join(WEB_DATA, url.split("/").pop());
    return fs.existsSync(file) ? new Response(fs.readFileSync(file)) : new Response("", { status: 404 });
  } }),
  anthropic: new Anthropic({ apiKey: "fake", baseURL: `${ORIGIN}/fake-anthropic`, maxRetries: 0 }),
  auth: { userFromToken: async token => tokens.get(token) || null },
};
const routes = {
  "/api/me": [me, "GET"], "/api/advisor": [advisor, "POST"], "/api/checkout": [checkout, "POST"], "/api/portal": [portal, "POST"],
  "/api/delete-account": [deleteAccount, "POST"], "/api/health": [health, "GET"],
};

/** Stand-in model: searches once with the real tools, then answers from what they returned. */
function fakeModel(res, body) {
  const last = body.messages.at(-1);
  if (typeof last.content === "string") {
    const words = last.content.toLowerCase().match(/[a-z]{5,}/g) || [];
    const query = ["mentor", "housing", "veteran", "arts", "food"].find(w => words.some(x => x.startsWith(w))) || "";
    streamMessage(res, { content: [{ type: "tool_use", id: `toolu_${randomUUID()}`, name: "search_funding", input: { query, state: "FL", fiscal_year: "FY2026-27" } }], stop_reason: "tool_use", usage: { input_tokens: 1500, output_tokens: 120 } });
    return;
  }
  const found = JSON.parse(last.content[0].content);
  const top = found.records.slice(0, 3).map(r => `- **${r.name}**: ${r.budgeted == null ? "no budget amount" : `$${r.budgeted.toLocaleString("en-US")}`} [${r.id}]`).join("\n");
  const text = `Stand-in model answer, not real AI. The search found ${found.total_matches} records.\n${top}\nAn invented citation looks like this: [FL-FY2026-27-99999].`;
  setTimeout(() => streamMessage(res, { content: [{ type: "text", text }], usage: { input_tokens: 4000, output_tokens: 300, cache_read_input_tokens: 1500 } }), 400);
}

/** Stand-in for the part of Supabase's data service the page uses, with the real access rules applied. */
async function savedItems(req, res, url, body, user) {
  const send = (status, data) => { res.writeHead(status, { "content-type": "application/json" }); res.end(data === undefined ? "" : JSON.stringify(data)); };
  await pg.query(`set request.jwt.sub = '${user ? user.id : ""}'`);
  await pg.query(`set role ${user ? "authenticated" : "anon"}`);
  try {
    const single = (req.headers.accept || "").includes("vnd.pgrst.object");
    const id = url.searchParams.get("id")?.replace("eq.", "");
    let rows;
    if (req.method === "GET") rows = (await pg.query("select * from saved_items order by updated_at desc limit 100")).rows;
    else if (req.method === "POST") rows = (await pg.query("insert into saved_items (title, data, updated_at) values ($1, $2, $3) returning *", [body.title, JSON.stringify(body.data), body.updated_at])).rows;
    else if (req.method === "PATCH") rows = (await pg.query("update saved_items set title = $1, data = $2, updated_at = $3 where id = $4 returning *", [body.title, JSON.stringify(body.data), body.updated_at, id])).rows;
    else if (req.method === "DELETE") rows = (await pg.query("delete from saved_items where id = $1 returning *", [id])).rows;
    send(req.method === "DELETE" ? 200 : 200, single ? rows[0] : rows);
  } catch (error) {
    send(403, { message: error.message });
  } finally {
    await pg.query("reset role");
  }
}

function sessionFor(user) {
  const token = `fake.${randomUUID()}.token`;
  tokens.set(token, user);
  return { access_token: token, refresh_token: randomUUID(), expires_in: 3600, expires_at: Math.floor(Date.now() / 1000) + 3600, token_type: "bearer", user: { id: user.id, email: user.email, aud: "authenticated", role: "authenticated", app_metadata: {}, user_metadata: {}, created_at: new Date().toISOString() } };
}

async function auth(req, res, url, body) {
  const send = (status, data) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(data)); };
  if (url.pathname === "/auth/v1/otp") {
    let user = [...users.values()].find(u => u.email === body.email);
    if (!user) {
      user = { id: randomUUID(), email: body.email };
      users.set(user.id, user);
      await addUser(pg, user.id, user.email);
    }
    const code = randomUUID();
    pendingCodes.set(code, user);
    lastLink = `${url.searchParams.get("redirect_to")}?code=${code}`;
    return send(200, {});
  }
  if (url.pathname === "/auth/v1/token") {
    if (url.searchParams.get("grant_type") === "pkce") {
      const user = pendingCodes.get(body.auth_code);
      return user ? send(200, sessionFor(user)) : send(400, { error_code: "bad_code", msg: "bad code" });
    }
    return send(400, { msg: "unsupported" });
  }
  if (url.pathname === "/auth/v1/user") {
    const user = tokens.get((req.headers.authorization || "").slice(7));
    return user ? send(200, sessionFor(user).user) : send(401, { msg: "no user" });
  }
  if (url.pathname === "/auth/v1/logout") { res.writeHead(204); return res.end(); }
  return send(404, { msg: "not found" });
}

async function sendSignedEvent(event) {
  const payload = JSON.stringify(event);
  const header = await realStripe.webhooks.generateTestHeaderStringAsync({ payload, secret: WEBHOOK_SECRET });
  return endpoint(stripeWebhook, { methods: ["POST"], browser: false, getDeps: () => deps }).fetch(new Request(`${ORIGIN}/api/stripe-webhook`, { method: "POST", headers: { "stripe-signature": header }, body: payload }));
}

const page = (res, html) => { res.writeHead(200, { "content-type": "text/html; charset=utf-8" }); res.end(`<!doctype html><meta charset="utf-8"><body style="font:16px system-ui;margin:40px">${html}`); };

async function dev(req, res, url) {
  if (url.pathname === "/dev/last-link") { res.writeHead(200, { "content-type": "text/plain" }); return res.end(lastLink || ""); }
  if (url.pathname === "/dev/checkout") {
    const id = url.searchParams.get("session"), session = checkouts.get(id);
    return page(res, `<h1>Stand-in Stripe checkout</h1><p>No real money moves.</p><p><a id="pay" href="/dev/pay?session=${id}">Pay (test)</a></p><p><a id="cancel" href="${session.cancel_url}">Cancel</a></p>`);
  }
  if (url.pathname === "/dev/pay") {
    const session = checkouts.get(url.searchParams.get("session"));
    const sub = { id: `sub_${randomUUID()}`, status: "active", customer: session.customer, cancel_at_period_end: false, metadata: session.subscription_data.metadata, items: { data: [{ current_period_end: Math.floor(Date.now() / 1000) + 2592000, price: { id: session.line_items[0].price } }] } };
    subscriptions.set(sub.id, sub);
    // Stripe's confirmation arrives separately, slightly after the person returns.
    setTimeout(() => sendSignedEvent({ id: "evt", type: "checkout.session.completed", data: { object: { mode: "subscription", subscription: sub.id } } }), 1500);
    res.writeHead(302, { location: session.success_url });
    return res.end();
  }
  if (url.pathname === "/dev/portal") {
    const customer = url.searchParams.get("customer"), back = url.searchParams.get("back");
    return page(res, `<h1>Stand-in Stripe billing page</h1><p><a id="cancelsub" href="/dev/cancel?customer=${customer}&back=${encodeURIComponent(back)}">Cancel membership (test)</a></p><p><a href="${back}">Back</a></p>`);
  }
  if (url.pathname === "/dev/cancel") {
    const sub = [...subscriptions.values()].find(s => s.customer === url.searchParams.get("customer"));
    sub.status = "canceled";
    await sendSignedEvent({ id: "evt", type: "customer.subscription.deleted", data: { object: { id: sub.id } } });
    res.writeHead(302, { location: url.searchParams.get("back") });
    return res.end();
  }
  res.writeHead(404);
  res.end();
}

function staticFile(res, url) {
  if (url.pathname === "/data/site-config.json") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ apiUrl: ORIGIN, supabaseUrl: ORIGIN, supabaseKey: "fake-public-key", priceLabel: "price to be decided", termsUrl: "", privacyUrl: "", refundUrl: "" }));
  }
  const relative = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
  const file = path.resolve(DIST, relative);
  if (!file.startsWith(DIST) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
}

async function toWebResponse(response, res) {
  res.writeHead(response.status, Object.fromEntries(response.headers));
  if (!response.body) return res.end();
  const reader = response.body.getReader();
  res.on("close", () => reader.cancel().catch(() => {}));
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    res.write(value);
  }
  res.end();
}

http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, ORIGIN);
    if (url.pathname.startsWith("/dev/")) return await dev(req, res, url);
    let raw = "";
    if (!["GET", "HEAD"].includes(req.method)) for await (const chunk of req) raw += chunk;
    const body = raw && (req.headers["content-type"] || "").includes("json") ? JSON.parse(raw) : {};
    if (url.pathname === "/fake-anthropic/v1/messages") return fakeModel(res, body);
    if (url.pathname.startsWith("/auth/v1/")) return await auth(req, res, url, body);
    if (url.pathname === "/rest/v1/saved_items") return await savedItems(req, res, url, body, tokens.get((req.headers.authorization || "").slice(7)));
    const route = routes[url.pathname];
    if (route) {
      const abort = new AbortController();
      res.on("close", () => { if (!res.writableEnded) abort.abort(); });
      const response = await endpoint(route[0], { methods: [route[1]], getDeps: () => deps }).fetch(new Request(ORIGIN + req.url, { method: req.method, headers: req.headers, body: raw || undefined, signal: abort.signal }));
      return await toWebResponse(response, res);
    }
    return staticFile(res, url);
  } catch (error) {
    console.error(error);
    if (!res.headersSent) res.writeHead(500);
    res.end();
  }
}).listen(PORT, () => console.log(`Member test site running at ${ORIGIN}`));
