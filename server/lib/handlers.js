// One function per web address. Everything external arrives through deps, so tests can use stand-ins.
import { MAX_QUESTION_CHARS } from "./advisor.js";
import { applyEvent, cancelSubscriptions, createCheckout, createPortal, isActive } from "./billing.js";
import { DataUnavailable } from "./data.js";
import { HttpError, json, monthKey, readJson, requireUser } from "./http.js";

const questionLimit = (config, membership) => isActive(membership) ? config.memberQuestions : config.freeQuestions;

/** Who is signed in, their plan, and how many questions they've used this month. */
export async function me(request, deps) {
  const user = await requireUser(request, deps);
  const membership = await deps.db.membership(user.id);
  return json({
    email: user.email,
    member: isActive(membership),
    status: membership?.status || "none",
    periodEnd: membership?.current_period_end || null,
    cancelAtPeriodEnd: Boolean(membership?.cancel_at_period_end),
    canManageBilling: Boolean(membership?.stripe_customer_id),
    questionsUsed: await deps.db.questionsUsed(user.id, monthKey()),
    questionLimit: questionLimit(deps.config, membership),
    memberQuestions: deps.config.memberQuestions,
    advisorProvider: deps.config.provider,
    paymentsOpen: deps.config.paymentsOpen,
    paymentsMode: deps.config.paymentsMode,
  });
}

const BEGIN_REFUSALS = { user_limit: 403, busy: 429, budget_reached: 503 };

/** Streams an answer as one JSON object per line: text, reset, status, then done or error. */
export async function advisor(request, deps) {
  const { config, db } = deps;
  const user = await requireUser(request, deps);
  if (!deps.ask) throw new HttpError(503, "advisor_not_configured");
  const body = await readJson(request);
  const question = typeof body.question === "string" ? body.question.trim() : "";
  if (!question) throw new HttpError(400, "question_required");
  if (question.length > MAX_QUESTION_CHARS) throw new HttpError(413, "question_too_long", { limit: MAX_QUESTION_CHARS });

  try {
    await deps.store.records();
  } catch (error) {
    if (error instanceof DataUnavailable) throw new HttpError(503, "data_unavailable");
    throw error;
  }

  const membership = await db.membership(user.id);
  const limit = questionLimit(config, membership);
  const month = monthKey();
  const begun = await db.advisorBegin(user.id, month, limit, config.monthlyBudgetMicros, config.questionReserveMicros);
  if (begun.result !== "ok") throw new HttpError(BEGIN_REFUSALS[begun.result] || 500, begun.result, { limit, member: isActive(membership) });

  const encoder = new TextEncoder();
  const abort = new AbortController();
  let open = true;
  const stream = new ReadableStream({
    async start(controller) {
      const emit = event => {
        if (!open) return;
        try {
          controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
        } catch {
          open = false;
        }
      };
      let outcome = { status: "failed", code: "server_error", costMicros: 0, toolCalls: 0, model: config.model, tokens: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
      try {
        outcome = await deps.ask({ question, history: body.history, signal: abort.signal, emit });
      } catch (error) {
        console.error("advisor failed", error.name, error.message);
      }
      try {
        await db.advisorFinish(begun.runId, outcome);
      } catch (error) {
        // The reserve stays counted, which errs toward spending less.
        console.error("could not record advisor usage", error.message);
      }
      if (outcome.status === "answered") {
        const used = await db.questionsUsed(user.id, month).catch(() => null);
        emit({ type: "done", text: outcome.text, truncated: outcome.truncated, questionsUsed: used, questionLimit: limit });
      } else if (outcome.status !== "stopped") {
        emit({ type: "error", code: outcome.code || "server_error" });
      }
      if (open) controller.close();
    },
    cancel() {
      open = false;
      abort.abort();
    },
  });
  return new Response(stream, { headers: { "content-type": "application/x-ndjson", "cache-control": "no-store", "x-accel-buffering": "no" } });
}

export async function checkout(request, deps) {
  const user = await requireUser(request, deps);
  if (!deps.stripe || !deps.config.paymentsOpen) throw new HttpError(403, "payments_not_open");
  const result = await createCheckout(deps, user);
  if (result.alreadyMember) throw new HttpError(409, "already_member");
  return json({ url: result.url });
}

export async function portal(request, deps) {
  const user = await requireUser(request, deps);
  if (!deps.stripe) throw new HttpError(403, "payments_not_open");
  const result = await createPortal(deps, user);
  if (!result) throw new HttpError(404, "no_billing_account");
  return json(result);
}

/** Only accepts events signed by Stripe. An error reply makes Stripe send the event again. */
export async function stripeWebhook(request, deps) {
  if (!deps.stripe || !deps.config.stripeWebhookSecret) throw new HttpError(503, "payments_not_configured");
  const raw = await request.text();
  let event;
  try {
    event = await deps.stripe.webhooks.constructEventAsync(raw, request.headers.get("stripe-signature") || "", deps.config.stripeWebhookSecret);
  } catch {
    throw new HttpError(400, "bad_signature");
  }
  await applyEvent(deps, event);
  return json({ received: true });
}

/** Deletes the person's account and saved work, after stopping any future charges. */
export async function deleteAccount(request, deps) {
  const user = await requireUser(request, deps);
  const body = await readJson(request);
  if (body.confirm !== "DELETE") throw new HttpError(400, "confirmation_required");
  await cancelSubscriptions(deps, user.id);
  await deps.db.deleteUser(user.id);
  return json({ deleted: true });
}

/** For checking a setup. Says which parts have settings, and never shows the settings. */
export async function health(request, deps) {
  return json({
    signIn: Boolean(deps.auth),
    database: Boolean(deps.db),
    advisor: Boolean(deps.ask),
    advisorProvider: deps.config.provider,
    payments: Boolean(deps.stripe),
    webhook: Boolean(deps.config.stripeWebhookSecret),
    paymentsOpen: deps.config.paymentsOpen,
    paymentsMode: deps.config.paymentsMode,
    model: deps.config.model,
  });
}
