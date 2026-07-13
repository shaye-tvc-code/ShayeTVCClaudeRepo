# Routine: Stackers eComm Sales Run Rate

This is the full spec executed by the scheduled Claude trigger. It is also
the source of truth for the trigger's prompt — if you change behavior here,
update the trigger's prompt to match (`mcp__Claude_Code_Remote__update_trigger`
doesn't support editing the prompt directly; delete and recreate the trigger,
or ask Claude to do it).

## Schedule

| Trigger | Cron (local, Australia/Sydney) | Reporting window for Step 4 |
|---|---|---|
| Weekday | Tue, Wed, Thu, Fri @ 6:00am | Week-to-date (since Monday) and month-to-date trends |
| Weekly  | Mon @ 6:00am | Full preceding week (Monday–Sunday), framed as a weekly recap |

Cumulative figures (Steps 1–3) always cover month-to-date through the most
recently completed trading day, regardless of which trigger fired — for the
weekly run this is the Sunday just completed.

## Inputs

- **Net sales**: from Shopify, via `run-analytics-query` (ShopifyQL) or the
  Shopify Admin API analytics/orders data. Use `net_sales` semantics
  (gross sales minus discounts, returns, and refunds), not gross sales.
- **Monthly target**: `config/monthly-sales-targets.json` in this repo
  (shaye-tvc-code/ShayeTVCClaudeRepo), read via the GitHub MCP tools
  (`get_file_contents`). Keyed by `YYYY-MM`, values in the currency given
  by the file's `currency` field. If there's no entry for the current
  month, state this explicitly in the email instead of fabricating a
  number, and skip Steps 1–3's target-relative math. New months' targets
  get added by the user telling Claude the number in chat, which commits
  it to this file — there is no Shopify metafield involved.
- **Top products / AOV / inventory**: from Shopify analytics, orders, and
  `get-inventory-levels`.

## Step 1 — 📈 Run Rate

Report two numbers side by side:
- Cumulative net sales for the month to date.
- Cumulative net sales required to date to be on pace for the month's
  target, i.e. `target * (days_elapsed / days_in_month)`, where
  `days_elapsed` is the number of calendar days from the 1st through the
  most recently completed trading day (inclusive).

## Step 2 — 🏆 Are we going to beat our target?

If the current run rate (`cumulative_to_date / days_elapsed * days_in_month`)
projects to meet or exceed the target, forecast the projected month-end
total and the surplus over target (amount and %). Skip this section's
verdict (don't force a beat/miss framing) if the target is unknown.

## Step 3 — ⚠️ Are we going to fall short of our target?

If the projected month-end total is below target, report the shortfall
(amount and %), and compute the new required daily net sales rate for the
remaining days in the month to still hit the target:
`(target - cumulative_to_date) / days_remaining`.

Only one of Step 2 or Step 3 applies for a given run (whichever the
forecast supports) — don't include both a "beat" and "fall short" verdict
in the same email.

## Step 4 — 🔍 Emerging Patterns

The window depends on which trigger fired:
- **Weekday runs**: week-to-date (since the most recent Monday, through
  yesterday) and month-to-date. A single day's numbers are noisy and not
  very meaningful on their own — report trends over WTD and MTD instead
  of yesterday in isolation.
- **Weekly run (Monday)**: "this week" means the Monday–Sunday period
  immediately preceding the run — i.e. last Monday through this past
  Sunday, inclusive. Worked example: for the report delivered Monday 13
  July, "this week" = Monday 6 July through Sunday 12 July (inclusive).
  Frame the section as a weekly recap ("this week", "the week of [date
  range]") — not single-day language. State the week's own Mon–Sun date
  range explicitly; it may differ from the month-to-date range used in
  the email subject if the week spans a month boundary.

For either window, cover:
- Top selling products (by net sales and/or units) over the window.
- Flag any product that is normally a top seller (appears in the trailing
  30-day or prior-month top sellers list) but is out of stock — check via
  `get-inventory-levels`.
- Note if a usually-top-selling product has slowed (lower rank or velocity
  vs. its trailing baseline).
- Compare average order value (AOV = net sales / order count) for the
  window against a trailing baseline (e.g. trailing 30 days) and comment
  on whether it's up, down, or flat.

## Output: email

Send (or draft, if no send-capable email connector is available).

Formatting conventions:
- Open with: `⭐ Stackers eComm Sales run rate – [date range]` (date range =
  the month-to-date range covered, e.g. "1–15 July 2026" — this convention
  is the same across all trigger variants).
- `---` dividers between each of the four sections.
- **Bold headers** for each section, each prefixed with its emoji
  (📈 Run Rate, 🏆 target-beat section, ⚠️ target-shortfall section,
  🔍 Emerging Patterns).
- Use these emojis consistently throughout the body wherever referencing
  that section's topic, not just in headers.
- Close with something in the spirit of "Let's get after it! 🚀" — vary the
  sign-off to reflect the day's actual outcome (e.g. more subdued/rallying
  if behind pace, more celebratory if ahead).

## Recipients

- **All runs**: to `shaye@vergecollective.com.au`.
- **Weekly run only**: also cc `Colin From Accounts <colin@vergecollective.com.au>`
  and `Jesse <jesse.o@oozestudios.com.au>`.
  The weekday runs stay Shaye-only.

## Delivery mechanism

1. **Primary: Superhuman Mail.** Call `create_or_update_draft` with
   `type: "new"`, the recipients from above (`to` always includes Shaye;
   add `cc` for Colin and Jesse on the weekly run only), the subject line
   from above, and `body` set to the exact HTML-formatted report (use the
   `body` field, not `instructions`, so the AI writer doesn't rewrite the
   tone/formatting — dividers, bold headers, and emoji placement must be
   exact). Then call `send_draft` with the returned `draft_id` to actually
   send it.
2. **Fallback: Gmail draft.** If Superhuman Mail's tools aren't available
   in the session, create a Gmail draft (`mcp__Gmail__create_draft`) to
   the same recipients as above with the same subject and body, and note
   in the session output that it was drafted, not sent, and why (Gmail's
   connector here has no send action).
