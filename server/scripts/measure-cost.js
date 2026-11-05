// Measures what real questions cost, to help set prices and limits. ON A PAID KEY THIS SPENDS REAL MONEY.
// Put ANTHROPIC_API_KEY or GEMINI_API_KEY in server/.env, then run: npm run measure-cost
// Try other settings with ADVISOR_PROVIDER, ADVISOR_MODEL and ADVISOR_EFFORT in the same file.
// On Gemini's free tier, the costs shown are estimates at Google's paid rate.
import { readConfig } from "../lib/config.js";
import { createDataStore } from "../lib/data.js";
import { advisorFor } from "../lib/deps.js";

const QUESTIONS = [
  "What youth mentoring projects were funded in Florida in FY2026-27, and how much did they get?",
  "Which after-school programs were vetoed in Florida in FY2026-27?",
  "What does a first-time Delaware nonprofit need to have ready before applying?",
  "How much do Delaware's arts and history groups typically receive in FY2027?",
  "What did the 18th Ward request in Louisiana in 2026, and what did the acts provide?",
  "What do funded Florida nonprofit applications have in common?",
];

const config = readConfig();
const store = createDataStore({ baseUrl: config.dataUrl });
const ask = advisorFor(config, store);
if (!ask) throw new Error("Add the key for the AI service you're measuring to server/.env.");
const costs = [];
console.log(`Model ${config.model}, effort ${config.effort}`);
for (const question of QUESTIONS) {
  const started = Date.now();
  const result = await ask({ question });
  costs.push(result.costMicros);
  console.log(`$${(result.costMicros / 1e6).toFixed(4)}  ${((Date.now() - started) / 1000).toFixed(0)}s  ${result.toolCalls} searches  ${result.status}${result.code ? ` (${result.code})` : ""}  ${question}`);
}
costs.sort((a, b) => a - b);
const total = costs.reduce((sum, cost) => sum + cost, 0);
console.log(`Average $${(total / costs.length / 1e6).toFixed(4)}, highest $${(costs.at(-1) / 1e6).toFixed(4)}, total spent $${(total / 1e6).toFixed(4)}`);
