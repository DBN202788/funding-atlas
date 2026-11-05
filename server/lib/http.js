// Shared request handling: browser access rules, sign-in checks and error replies.
export class HttpError extends Error {
  constructor(status, code, extra = {}) {
    super(code);
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

export const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

function corsHeaders(request, config) {
  const origin = request.headers.get("origin");
  if (!origin || !config.allowedOrigins.includes(origin)) return { vary: "Origin" };
  return { "access-control-allow-origin": origin, "access-control-allow-headers": "authorization, content-type", "access-control-allow-methods": "GET, POST, OPTIONS", "access-control-max-age": "600", vary: "Origin" };
}

function withHeaders(response, headers) {
  for (const [key, value] of Object.entries(headers)) response.headers.set(key, value);
  return response;
}

/** Wraps a handler as the object Vercel expects. getDeps is swapped for fakes in tests. */
export function endpoint(handler, { methods, browser = true, getDeps }) {
  return {
    async fetch(request) {
      let config = null;
      try {
        const deps = await getDeps();
        config = deps.config;
        const cors = browser ? corsHeaders(request, config) : {};
        if (browser && request.method === "OPTIONS") return withHeaders(new Response(null, { status: 204 }), cors);
        if (!methods.includes(request.method)) return withHeaders(json({ error: "method_not_allowed" }, 405), cors);
        try {
          return withHeaders(await handler(request, deps), cors);
        } catch (error) {
          if (error instanceof HttpError) return withHeaders(json({ error: error.code, ...error.extra }, error.status), cors);
          console.error("request failed", new URL(request.url).pathname, error.name, error.message);
          return withHeaders(json({ error: "server_error" }, 500), cors);
        }
      } catch (error) {
        console.error("server setup failed", error.message);
        return json({ error: "not_configured" }, 503);
      }
    },
  };
}

export async function requireUser(request, deps) {
  const header = request.headers.get("authorization") || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!token) throw new HttpError(401, "sign_in_required");
  const user = await deps.auth.userFromToken(token);
  if (!user) throw new HttpError(401, "sign_in_required");
  return user;
}

export async function readJson(request, maxBytes = 64_000) {
  const text = await request.text();
  if (text.length > maxBytes) throw new HttpError(413, "too_large");
  try {
    return JSON.parse(text || "{}");
  } catch {
    throw new HttpError(400, "bad_request");
  }
}

export const monthKey = (date = new Date()) => date.toISOString().slice(0, 7);
