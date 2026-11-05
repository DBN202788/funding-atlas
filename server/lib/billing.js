// Stripe subscriptions. Stripe holds card details; this server only ever sees status.
// past_due stays active while Stripe retries the card. It becomes canceled or unpaid if that fails.
const ACTIVE = new Set(["active", "trialing", "past_due"]);
const TERMINAL = new Set(["canceled", "unpaid", "incomplete_expired"]);
const SUBSCRIPTION_EVENTS = new Set(["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted", "customer.subscription.paused", "customer.subscription.resumed"]);

export const isActive = membership => Boolean(membership && ACTIVE.has(membership.status));

export function membershipFields(subscription) {
  const item = subscription.items?.data?.[0];
  const periodEnd = item?.current_period_end;
  return {
    status: subscription.status,
    stripe_customer_id: typeof subscription.customer === "string" ? subscription.customer : subscription.customer?.id,
    stripe_subscription_id: subscription.id,
    price_id: item?.price?.id || null,
    current_period_end: periodEnd ? new Date(periodEnd * 1000).toISOString() : null,
    cancel_at_period_end: Boolean(subscription.cancel_at_period_end),
  };
}

/** Always reads the current subscription from Stripe, so events arriving out of order can't leave old data. */
export async function syncSubscription({ stripe, db }, subscriptionId) {
  const subscription = await stripe.subscriptions.retrieve(subscriptionId);
  const customerId = typeof subscription.customer === "string" ? subscription.customer : subscription.customer?.id;
  const userId = subscription.metadata?.supabase_user_id || (await db.membershipByCustomer(customerId))?.user_id;
  if (!userId) {
    console.error("subscription has no matching member", subscription.id);
    return null;
  }
  const existing = await db.membership(userId);
  // Ending an old subscription must not cancel a newer one.
  if (existing?.stripe_subscription_id && existing.stripe_subscription_id !== subscription.id && isActive(existing) && TERMINAL.has(subscription.status)) return existing;
  const fields = membershipFields(subscription);
  await db.saveMembership(userId, fields);
  return fields;
}

/** Applies a verified webhook event. Safe to receive the same event twice. */
export async function applyEvent(deps, event) {
  if (event.type === "checkout.session.completed") {
    const session = event.data.object;
    if (session.mode === "subscription" && session.subscription) await syncSubscription(deps, session.subscription);
  } else if (SUBSCRIPTION_EVENTS.has(event.type)) {
    await syncSubscription(deps, event.data.object.id);
  }
}

async function customerFor({ stripe, db }, user, membership) {
  if (membership?.stripe_customer_id) return membership.stripe_customer_id;
  const customer = await stripe.customers.create({ email: user.email, metadata: { supabase_user_id: user.id } });
  await db.saveMembership(user.id, { stripe_customer_id: customer.id });
  return customer.id;
}

/** Returns { url } for Stripe's checkout page, or { alreadyMember: true } if they already pay. */
export async function createCheckout(deps, user) {
  const { stripe, db, config } = deps;
  const membership = await db.membership(user.id);
  if (isActive(membership)) return { alreadyMember: true };
  const customer = await customerFor(deps, user, membership);
  // The webhook may not have arrived yet, so ask Stripe too. This prevents paying twice.
  const existing = await stripe.subscriptions.list({ customer, status: "all", limit: 10 });
  const current = existing.data.find(s => ACTIVE.has(s.status));
  if (current) {
    await syncSubscription(deps, current.id);
    return { alreadyMember: true };
  }
  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    customer,
    client_reference_id: user.id,
    line_items: [{ price: config.stripePriceId, quantity: 1 }],
    subscription_data: { metadata: { supabase_user_id: user.id } },
    success_url: `${config.siteUrl}?checkout=done#account`,
    cancel_url: `${config.siteUrl}?checkout=cancelled#account`,
    ...(config.automaticTax ? { automatic_tax: { enabled: true }, customer_update: { address: "auto" } } : {}),
  });
  return { url: session.url };
}

/** Stripe's own page for changing a card, seeing receipts or cancelling. */
export async function createPortal({ stripe, db, config }, user) {
  const membership = await db.membership(user.id);
  if (!membership?.stripe_customer_id) return null;
  const session = await stripe.billingPortal.sessions.create({ customer: membership.stripe_customer_id, return_url: `${config.siteUrl}#account` });
  return { url: session.url };
}

/** Stops future charges before an account is deleted. */
export async function cancelSubscriptions({ stripe, db }, userId) {
  const membership = await db.membership(userId);
  if (!stripe || !membership?.stripe_customer_id) return;
  const subscriptions = await stripe.subscriptions.list({ customer: membership.stripe_customer_id, status: "all", limit: 10 });
  for (const subscription of subscriptions.data) {
    if (!TERMINAL.has(subscription.status)) await stripe.subscriptions.cancel(subscription.id);
  }
}
