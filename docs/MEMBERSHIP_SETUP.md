# Turning on accounts, the advisor and paid memberships

The code is built and tested, but it stays hidden until these steps are done. Until then, the public site works as it does today.

**You do the sign-ups and enter every password, card, bank detail and secret key yourself.** Never paste a secret key into a chat, an email or a file in this project.

## What each service does and costs

Prices were checked on November 5, 2026. Confirm them when you sign up.

| Service | What it does | Cost |
|---|---|---|
| [Vercel](https://vercel.com/pricing) | Runs the server | The free plan is for personal, non-commercial use, so paid memberships need Pro at $20 a month, which includes a $20 usage credit |
| [Supabase](https://supabase.com/pricing) | Sign-in, member records, saved drafts | A free plan is available for testing, but third-party guides say free projects pause after about a week without activity. Reports put Pro at $25 a month. Confirm on the pricing page |
| [Resend](https://resend.com/pricing) or another email service | Sends sign-in emails | Check its pricing. Supabase's built-in email is limited to [2 messages an hour, to your own team only](https://supabase.com/docs/guides/auth/auth-smtp), so real users need this |
| [Anthropic](https://console.anthropic.com) | Advisor answers | Pay per use. Measure this in step 5 |
| [Stripe](https://stripe.com/pricing) | Payments | No monthly fee on pay-as-you-go. 2.9% + 30¢ per US card payment, plus 0.7% for subscriptions |

## Step 1: Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Open **SQL Editor**, paste the contents of `server/supabase/schema.sql`, and run it once.
3. Under **Authentication > URL Configuration**, set **Site URL** to `https://dbn202788.github.io/funding-atlas/` and add the same address to **Redirect URLs**.
4. Under **Authentication > Emails > SMTP Settings**, connect your email service. You can skip this for your first test, which can only send to your own team's addresses.
5. Under **Project Settings > API**, find the project URL, the public key and the secret key. You'll use them in steps 3 and 4.

## Step 2: Anthropic

1. Create an account at [console.anthropic.com](https://console.anthropic.com) and add a small amount of credit.
2. **Set a monthly spend limit in the Console.** This is your backstop in case anything in this project goes wrong.
3. Create an API key for this project.

## Step 3: Vercel

1. Create an account at [vercel.com](https://vercel.com), and import the `DBN202788/funding-atlas` repository.
2. Set **Root Directory** to `server`.
3. Under **Settings > Environment Variables**, add the values listed in `server/.env.example`. Leave the Stripe values out for now.
4. Deploy, then open `https://YOUR-PROJECT.vercel.app/api/health`. It shows which parts are set up, and never shows keys.

Use Vercel's free plan only while testing. Move to Pro before charging anyone.

## Step 4: Connect the website

Tell me the Vercel address, the Supabase project URL and the Supabase **public** key, and I'll put them in `web/data/site-config.json`. The public key is meant to be visible, because the database's access rules protect the data. Never share the secret key.

The **Sign in** and **Ask the advisor** tabs then appear on the public site.

## Step 5: Measure the real cost

Do this before choosing a price. It spends a small amount of real money.

1. Create a file named `server/.env` containing `ANTHROPIC_API_KEY=` and your key. Git ignores this file.
2. Run:

```bash
cd server && npm run measure-cost
```

It asks six typical questions and prints the cost of each. To compare settings, add `ADVISOR_MODEL` or `ADVISOR_EFFORT` to the same file. After launch, the `advisor_cost_by_month` view in Supabase shows real averages, and it never stores question text.

Use those numbers to set `MEMBER_QUESTIONS_PER_MONTH`, `FREE_QUESTIONS_PER_MONTH`, `MONTHLY_BUDGET_USD` and your price.

## Step 6: Stripe in test mode

1. Create an account at [stripe.com](https://stripe.com). Stay in **test mode**, where no real money moves.
2. Create a product with a monthly price, and copy its price ID into Vercel as `STRIPE_PRICE_ID`.
3. Copy the test secret key into Vercel as `STRIPE_SECRET_KEY`.
4. Under **Developers > Webhooks**, add `https://YOUR-PROJECT.vercel.app/api/stripe-webhook`, with these events:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `customer.subscription.paused`
   - `customer.subscription.resumed`
5. Copy the webhook's signing secret into Vercel as `STRIPE_WEBHOOK_SECRET`.
6. Under **Settings > Billing > Customer portal**, turn on the portal, including card updates and cancellation.
7. Redeploy on Vercel, then subscribe using [Stripe's test card](https://docs.stripe.com/testing). Check that you become a member, that cancelling in Manage billing works, and that a failed test card doesn't give access.

## Step 7: Before charging real money

These are for you to decide, ideally with a qualified professional:

- [ ] Terms of service, privacy policy and refund policy, with their addresses in `site-config.json`. The upgrade button won't appear in live mode without terms and privacy addresses.
- [ ] The privacy policy says that questions and drafts are sent to Anthropic to be answered, that payments go through Stripe, and what's stored.
- [ ] Sales tax. If you need it, set up Stripe Tax and set `STRIPE_AUTOMATIC_TAX=yes`.
- [ ] Whether the "Legislative Funding Atlas" name is available.
- [ ] A support email address shown on the site.
- [ ] Your final price and limits, based on step 5.

To go live, create the same product in Stripe's live mode with its own webhook, replace the three Stripe values with the live ones, and set `ALLOW_LIVE_PAYMENTS=yes`. A live key does nothing until that last setting is on.

## Spending limits in the code

- **Each person:** a monthly question limit, set by plan.
- **Everyone together:** `MONTHLY_BUDGET_USD` stops all advisor questions when reached. Money is set aside before each question, so simultaneous questions can't overrun it.
- **Each question:** at most 6 steps and 4 minutes. The advisor must answer once half of `QUESTION_RESERVE_USD` is spent.
- **Your backstop:** the spend limit you set in the Anthropic Console.

A question that the server loses track of, such as during a crash, keeps its full reserve counted until the month ends. That can stop the advisor slightly early, but it can't overspend.

## Trying it without any accounts

This runs everything on your computer with stand-ins for sign-in email, Stripe and the AI model:

```bash
python -I pipeline/site.py
```

```bash
cd server && npm install && npm run test-site
```

Then open `http://localhost:8767`. The sign-in link is at `http://localhost:8767/dev/last-link`.

## What has and hasn't been tested

**Tested on this computer:** the database rules in a real Postgres, the advisor with the real Anthropic library against a stand-in, the Stripe signature check with the real Stripe library, and the full flow in a browser.

**Not tested until the accounts exist:** real AI answers and their cost, Supabase's own services, real Stripe pages and webhooks, and running on Vercel. Test each of these in steps 3 to 6.
