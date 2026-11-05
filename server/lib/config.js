// Settings come from environment variables, so limits can change without touching code.
const number = (value, fallback) => {
  const parsed = Number(value);
  return value !== undefined && value !== "" && Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
};

export function readConfig(env = process.env) {
  const stripeKey = env.STRIPE_SECRET_KEY || "";
  const live = /^(sk|rk)_live_/.test(stripeKey);
  return {
    siteUrl: (env.SITE_URL || "https://dbn202788.github.io/funding-atlas/").replace(/\/?$/, "/"),
    allowedOrigins: (env.ALLOWED_ORIGINS || "https://dbn202788.github.io").split(",").map(s => s.trim()).filter(Boolean),
    dataUrl: (env.ATLAS_DATA_URL || "https://dbn202788.github.io/funding-atlas/data").replace(/\/$/, ""),
    supabaseUrl: env.SUPABASE_URL || "",
    supabaseSecretKey: env.SUPABASE_SECRET_KEY || "",
    anthropicKey: env.ANTHROPIC_API_KEY || "",
    model: env.ADVISOR_MODEL || "claude-opus-5-5",
    effort: env.ADVISOR_EFFORT || "medium",
    maxTokens: number(env.ADVISOR_MAX_TOKENS, 8000),
    maxRounds: number(env.ADVISOR_MAX_ROUNDS, 6),
    timeoutMs: number(env.ADVISOR_TIMEOUT_SECONDS, 240) * 1000,
    freeQuestions: number(env.FREE_QUESTIONS_PER_MONTH, 3),
    memberQuestions: number(env.MEMBER_QUESTIONS_PER_MONTH, 50),
    monthlyBudgetMicros: Math.round(number(env.MONTHLY_BUDGET_USD, 25) * 1e6),
    questionReserveMicros: Math.round(number(env.QUESTION_RESERVE_USD, 1) * 1e6),
    stripeKey,
    stripeWebhookSecret: env.STRIPE_WEBHOOK_SECRET || "",
    stripePriceId: env.STRIPE_PRICE_ID || "",
    automaticTax: env.STRIPE_AUTOMATIC_TAX === "yes",
    paymentsMode: live ? "live" : "test",
    // A live key does nothing until the owner also turns this on.
    paymentsOpen: Boolean(stripeKey && env.STRIPE_PRICE_ID && (!live || env.ALLOW_LIVE_PAYMENTS === "yes")),
  };
}
