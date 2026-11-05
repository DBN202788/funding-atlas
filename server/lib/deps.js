// Connects the handlers to the real services, using the keys in the environment.
import Anthropic from "@anthropic-ai/sdk";
import { GoogleGenAI } from "@google/genai";
import { createClient } from "@supabase/supabase-js";
import Stripe from "stripe";
import { readConfig } from "./config.js";
import { runAdvisor } from "./advisor.js";
import { createDataStore } from "./data.js";
import { runGeminiAdvisor } from "./gemini.js";
import { endpoint } from "./http.js";

function supabaseServices(config) {
  const supabase = createClient(config.supabaseUrl, config.supabaseSecretKey, { auth: { persistSession: false, autoRefreshToken: false } });
  const check = ({ data, error }) => {
    if (error) throw new Error(`database: ${error.message}`);
    return data;
  };
  return {
    auth: {
      async userFromToken(token) {
        const { data, error } = await supabase.auth.getUser(token);
        return error || !data.user ? null : { id: data.user.id, email: data.user.email };
      },
    },
    db: {
      membership: async userId => check(await supabase.from("memberships").select("*").eq("user_id", userId).maybeSingle()),
      membershipByCustomer: async customerId => check(await supabase.from("memberships").select("*").eq("stripe_customer_id", customerId).maybeSingle()),
      saveMembership: async (userId, fields) => check(await supabase.from("memberships").upsert({ user_id: userId, ...fields, updated_at: new Date().toISOString() })),
      async questionsUsed(userId, month) {
        const { count, error } = await supabase.from("advisor_runs").select("id", { count: "exact", head: true }).eq("user_id", userId).eq("month", month).neq("status", "failed");
        if (error) throw new Error(`database: ${error.message}`);
        return count;
      },
      async advisorBegin(userId, month, limit, capMicros, reserveMicros) {
        const rows = check(await supabase.rpc("advisor_begin", { p_user: userId, p_month: month, p_limit: limit, p_cap_micros: capMicros, p_reserve_micros: reserveMicros }));
        return { result: rows[0].result, runId: rows[0].run_id };
      },
      advisorFinish: async (runId, outcome) => check(await supabase.rpc("advisor_finish", {
        p_run: runId, p_status: outcome.status, p_cost_micros: outcome.costMicros, p_input: outcome.tokens.input, p_output: outcome.tokens.output,
        p_cache_read: outcome.tokens.cacheRead, p_cache_write: outcome.tokens.cacheWrite, p_tool_calls: outcome.toolCalls, p_model: outcome.model,
      })),
      async deleteUser(userId) {
        const { error } = await supabase.auth.admin.deleteUser(userId);
        if (error) throw new Error(`database: ${error.message}`);
      },
    },
  };
}

/** Picks the AI service named in the settings. No key means the advisor stays off. */
export function advisorFor(config, store) {
  if (config.provider === "gemini") {
    if (!config.geminiKey) return null;
    const client = new GoogleGenAI({ apiKey: config.geminiKey });
    return args => runGeminiAdvisor({ client, store, config, ...args });
  }
  if (!config.anthropicKey) return null;
  const client = new Anthropic({ apiKey: config.anthropicKey });
  return args => runAdvisor({ client, store, config, ...args });
}

let services = null;

export function realDeps() {
  if (services) return services;
  const config = readConfig();
  if (!config.supabaseUrl || !config.supabaseSecretKey) throw new Error("Supabase settings are missing");
  const store = createDataStore({ baseUrl: config.dataUrl });
  services = {
    config,
    ...supabaseServices(config),
    store,
    ask: advisorFor(config, store),
    stripe: config.stripeKey ? new Stripe(config.stripeKey) : null,
  };
  return services;
}

export const route = (handler, options) => endpoint(handler, { getDeps: realDeps, ...options });
