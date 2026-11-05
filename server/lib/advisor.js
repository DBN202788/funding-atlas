// Answers one question: asks the model, runs the tools it wants, and repeats until it answers.
import Anthropic from "@anthropic-ai/sdk";
import { unfinishedCostMicros, usageCostMicros } from "./pricing.js";
import { RULES } from "./prompt.js";
import { runTool, TOOLS } from "./tools.js";

const FALLBACK_BETA = "server-side-fallback-2026-07-01";
const JSON_RETRIES = 2;
const MAX_HISTORY = 6;
const MAX_TURN_CHARS = 4000;
export const MAX_QUESTION_CHARS = 8000;

/** Only plain text from earlier turns is kept, so a client can't add tool results or instructions. */
export function cleanHistory(history) {
  if (!Array.isArray(history)) return [];
  const turns = history.slice(-MAX_HISTORY)
    .filter(t => t && (t.role === "user" || t.role === "assistant") && typeof t.content === "string" && t.content.trim())
    .map(t => ({ role: t.role, content: t.content.slice(0, MAX_TURN_CHARS) }));
  while (turns.length && turns[0].role !== "user") turns.shift();
  return turns;
}

function errorCode(error) {
  if (error instanceof Anthropic.AuthenticationError || error instanceof Anthropic.PermissionDeniedError) return "server_config";
  if (error instanceof Anthropic.BadRequestError || error instanceof Anthropic.NotFoundError) return "server_config";
  if (error instanceof Anthropic.RateLimitError) return "provider_busy";
  if (error instanceof Anthropic.APIConnectionError) return "provider_unreachable";
  if (error instanceof Anthropic.APIError) return "provider_error";
  return "server_error";
}

/** After a fallback mid-answer, only text from before the switch may be sent back to the API. */
export function echoContent(content) {
  const lastSwitch = content.map(b => b.type).lastIndexOf("fallback");
  return content.filter((block, index) => index >= lastSwitch || block.type === "text");
}

const answerText = message => message.content.filter(b => b.type === "text").map(b => b.text).join("");

/**
 * Returns { status, code, text, truncated, costMicros, tokens, toolCalls, model }.
 * status: answered, stopped (the person cancelled), refused or failed.
 */
export async function runAdvisor({ client, store, config, question, history = [], signal, emit = () => {} }) {
  const messages = [...cleanHistory(history), { role: "user", content: question }];
  const timeout = AbortSignal.timeout(config.timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const result = { status: "failed", code: null, text: "", truncated: false, costMicros: 0, toolCalls: 0, model: config.model, tokens: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
  let sentText = false, jsonRetries = 0, round = 0;

  const addUsage = message => {
    result.costMicros += usageCostMicros(message.usage, message.model);
    result.model = message.model || result.model;
    result.tokens.input += message.usage?.input_tokens || 0;
    result.tokens.output += message.usage?.output_tokens || 0;
    result.tokens.cacheRead += message.usage?.cache_read_input_tokens || 0;
    result.tokens.cacheWrite += message.usage?.cache_creation_input_tokens || 0;
  };

  while (round < config.maxRounds) {
    // Near a limit, the model must answer with what it already has.
    const lastRound = round === config.maxRounds - 1 || result.costMicros >= config.questionReserveMicros / 2;
    const stream = client.beta.messages.stream({
      model: config.model,
      max_tokens: config.maxTokens,
      betas: [FALLBACK_BETA],
      fallbacks: "default",
      cache_control: { type: "ephemeral" },
      output_config: { effort: config.effort },
      system: RULES,
      tools: TOOLS,
      ...(lastRound ? { tool_choice: { type: "none" } } : {}),
      messages,
    }, { signal: combined });
    stream.on("text", delta => {
      sentText = true;
      emit({ type: "text", text: delta });
    });

    let message;
    try {
      message = await stream.finalMessage();
      jsonRetries = 0;
    } catch (error) {
      const unfinished = stream.currentMessage;
      if (error instanceof Anthropic.APIUserAbortError || combined.aborted) {
        result.costMicros += unfinished ? unfinishedCostMicros(unfinished.usage, unfinished.model, config.maxTokens) : 0;
        if (signal?.aborted) result.status = "stopped";
        else result.code = "timeout";
        return result;
      }
      if (error instanceof Anthropic.APIError) {
        result.code = errorCode(error);
        console.error("advisor API error", error.status, error.requestID);
        return result;
      }
      // Tool input that can't be read at all: ask again, a limited number of times.
      result.costMicros += unfinished ? unfinishedCostMicros(unfinished.usage, unfinished.model, config.maxTokens) : 0;
      if (jsonRetries++ < JSON_RETRIES) continue;
      result.code = "server_error";
      console.error("advisor stream error", error.name);
      return result;
    }
    addUsage(message);
    round += 1;

    if (message.stop_reason === "refusal") {
      result.status = "refused";
      result.code = "refused";
      return result;
    }
    const toolUses = message.content.filter(b => b.type === "tool_use");
    if (message.stop_reason === "pause_turn") {
      messages.push({ role: "assistant", content: echoContent(message.content) });
      continue;
    }
    if (!toolUses.length || message.stop_reason === "max_tokens") {
      result.text = answerText(message);
      result.truncated = message.stop_reason === "max_tokens";
      result.status = result.text.trim() ? "answered" : "failed";
      if (!result.text.trim()) result.code = result.truncated ? "too_long" : "empty";
      return result;
    }

    messages.push({ role: "assistant", content: echoContent(message.content) });
    emit({ type: "status", text: "Searching the records…" });
    if (sentText) {
      sentText = false;
      emit({ type: "reset" });
    }
    result.toolCalls += toolUses.length;
    messages.push({ role: "user", content: await Promise.all(toolUses.map(block => runTool(block, store))) });
  }
  result.code = "too_many_steps";
  return result;
}
