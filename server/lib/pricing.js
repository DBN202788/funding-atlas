// Dollars per million tokens, which is also millionths of a dollar per token.
// Check these against https://claude.com/pricing when changing models.
const PRICES = {
  "claude-opus-5-5": { input: 4, output: 20, cacheRead: 0.2, cacheWrite: 5 },
  "claude-opus-5": { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25 },
  "claude-opus-4-8": { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25 },
  "claude-opus-4-7": { input: 5, output: 25, cacheRead: 0.5, cacheWrite: 6.25 },
  "claude-sonnet-5-5": { input: 2, output: 10, cacheRead: 0.2, cacheWrite: 2.5 },
  "claude-sonnet-5": { input: 2, output: 10, cacheRead: 0.2, cacheWrite: 2.5 },
  "claude-haiku-5-5": { input: 0.1, output: 0.5, cacheRead: 0.01, cacheWrite: 0.125 },
  // Google's paid rate. On Google's free tier this is an estimate, and nothing is actually charged.
  "gemini-3.8-flash": { input: 0.75, output: 3.75, cacheRead: 0, cacheWrite: 0 },
};
// A model missing from the table is priced high, so the spending limit errs on the safe side.
const UNKNOWN = { input: 10, output: 50, cacheRead: 1, cacheWrite: 12.5 };

function entryCost(entry, fallbackModel) {
  const price = PRICES[entry.model || fallbackModel] || UNKNOWN;
  return (entry.input_tokens || 0) * price.input + (entry.output_tokens || 0) * price.output
    + (entry.cache_read_input_tokens || 0) * price.cacheRead + (entry.cache_creation_input_tokens || 0) * price.cacheWrite;
}

/** Cost of one API response in millionths of a dollar. A fallback lists each attempt separately. */
export function usageCostMicros(usage, model) {
  if (!usage) return 0;
  const parts = usage.iterations && usage.iterations.length ? usage.iterations : [usage];
  return Math.ceil(parts.reduce((total, entry) => total + entryCost(entry, model), 0));
}

/** Upper limit for a response that was cut off before its final token count arrived. */
export function unfinishedCostMicros(usage, model, maxTokens) {
  return usageCostMicros({ ...(usage || {}), iterations: undefined, output_tokens: maxTokens }, model);
}
