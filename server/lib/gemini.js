// The same advisor as advisor.js, using Google's Gemini API. Set ADVISOR_PROVIDER=gemini to use it.
import { ApiError, FunctionCallingConfigMode } from "@google/genai";
import { cleanHistory } from "./advisor.js";
import { unfinishedCostMicros, usageCostMicros } from "./pricing.js";
import { RULES } from "./prompt.js";
import { runTool, TOOLS } from "./tools.js";

const DECLARATIONS = [{ functionDeclarations: TOOLS.map(t => ({ name: t.name, description: t.description, parametersJsonSchema: t.input_schema })) }];
// Free-tier models are often overloaded, so wait and try again, then try another model.
const RETRYABLE = new Set([429, 500, 502, 503, 504]);
const MAX_RETRIES = 3;
const REFUSALS = new Set(["SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE", "IMAGE_SAFETY", "OTHER"]);

function errorCode(error) {
  if (!(error instanceof ApiError)) return "provider_unreachable";
  if ([400, 401, 403, 404].includes(error.status)) return "server_config";
  if (error.status === 429 || error.status === 503) return "provider_busy";
  return "provider_error";
}

const pause = (ms, signal) => new Promise(resolve => {
  const timer = setTimeout(resolve, ms);
  signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
});

const usageFrom = meta => meta ? { input_tokens: meta.promptTokenCount || 0, output_tokens: (meta.candidatesTokenCount || 0) + (meta.thoughtsTokenCount || 0) } : null;

/** Same arguments and result as runAdvisor in advisor.js. */
export async function runGeminiAdvisor({ client, store, config, question, history = [], signal, emit = () => {} }) {
  const contents = [...cleanHistory(history).map(t => ({ role: t.role === "assistant" ? "model" : "user", parts: [{ text: t.content }] })), { role: "user", parts: [{ text: question }] }];
  const timeout = AbortSignal.timeout(config.timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const result = { status: "failed", code: null, text: "", truncated: false, costMicros: 0, toolCalls: 0, model: config.model, tokens: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
  const models = [...new Set([config.model, ...(config.fallbackModels || [])])];
  const retryDelay = config.retryDelayMs ?? 1500;
  let sentText = false, round = 0, retries = 0, modelIndex = 0;

  while (round < config.maxRounds) {
    const model = models[modelIndex];
    // Near a limit, the model must answer with what it already has.
    const lastRound = round === config.maxRounds - 1 || result.costMicros >= config.questionReserveMicros / 2;
    const parts = [];
    let usage = null, finishReason = null, blocked = false;
    try {
      const stream = await client.models.generateContentStream({
        model,
        contents,
        config: {
          systemInstruction: RULES,
          tools: DECLARATIONS,
          maxOutputTokens: config.maxTokens,
          abortSignal: combined,
          ...(lastRound ? { toolConfig: { functionCallingConfig: { mode: FunctionCallingConfigMode.NONE } } } : {}),
        },
      });
      for await (const chunk of stream) {
        if (chunk.promptFeedback?.blockReason) blocked = true;
        usage = usageFrom(chunk.usageMetadata) || usage;
        const candidate = chunk.candidates?.[0];
        if (candidate?.finishReason) finishReason = candidate.finishReason;
        for (const part of candidate?.content?.parts || []) {
          parts.push(part);
          if (part.text && !part.thought) {
            sentText = true;
            emit({ type: "text", text: part.text });
          }
        }
      }
    } catch (error) {
      if (combined.aborted) {
        result.costMicros += unfinishedCostMicros(usage, model, config.maxTokens);
        if (signal?.aborted) result.status = "stopped";
        else result.code = "timeout";
        return result;
      }
      console.error("advisor API error", error.status || error.name, model);
      if (error instanceof ApiError && RETRYABLE.has(error.status) && !parts.length && retries < MAX_RETRIES) {
        retries += 1;
        emit({ type: "status", text: "The AI service is busy, so the advisor is trying again…" });
        await pause(retryDelay, combined);
        // Thought signatures belong to one model, so a question only changes model before its first tool call.
        if (round === 0 && retries >= 2 && modelIndex < models.length - 1) modelIndex += 1;
        continue;
      }
      result.code = errorCode(error);
      return result;
    }
    retries = 0;
    result.model = model;
    result.costMicros += usageCostMicros(usage, model);
    result.tokens.input += usage?.input_tokens || 0;
    result.tokens.output += usage?.output_tokens || 0;
    round += 1;

    if (blocked || REFUSALS.has(finishReason)) {
      result.status = "refused";
      result.code = "refused";
      return result;
    }
    const calls = parts.filter(part => part.functionCall);
    if (!calls.length || finishReason === "MAX_TOKENS") {
      result.text = parts.filter(part => part.text && !part.thought).map(part => part.text).join("");
      result.truncated = finishReason === "MAX_TOKENS";
      result.status = result.text.trim() ? "answered" : "failed";
      if (!result.text.trim()) result.code = result.truncated ? "too_long" : "empty";
      return result;
    }

    // Parts go back exactly as received, because they can carry Gemini's thought signatures.
    contents.push({ role: "model", parts });
    emit({ type: "status", text: "Searching the records…" });
    if (sentText) {
      sentText = false;
      emit({ type: "reset" });
    }
    result.toolCalls += calls.length;
    const answers = await Promise.all(calls.map(async ({ functionCall: call }) => {
      const done = await runTool({ id: call.id, name: call.name, input: call.args || {} }, store);
      return { functionResponse: { ...(call.id ? { id: call.id } : {}), name: call.name, response: done.is_error ? { error: done.content } : { output: done.content } } };
    }));
    contents.push({ role: "user", parts: answers });
  }
  result.code = "too_many_steps";
  return result;
}
