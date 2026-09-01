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
  it to this file — there is no Shopify metafield involved. The file may
  also have a `stretch_targets` array of `YYYY-MM` keys and a
  `seasonalized_targets` object of `YYYY-MM` → number — see "Stretch
  targets" below for how these change Steps 1–3.
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

## Stretch targets

If the current month's `YYYY-MM` key appears in the config file's
`stretch_targets` array, the target was set deliberately above a
realistic level for a reason unrelated to actual sales performance (e.g.
to justify securing a full stock shipment) — it isn't meant to be hit,
and framing it as a shortfall would be misleading. When this applies:

- **Step 1**: still report both figures as normal, but label the target
  itself as a stretch target (e.g. "the $92,000 stretch target") so the
  "required to date" figure isn't misread as a realistic pace
  expectation. If the same `YYYY-MM` key also appears in
  `seasonalized_targets`, also state that real underlying number in this
  section — e.g. "the real target, seasonalised from a $100k/month
  average, is $36,000" — so the email always surfaces the genuine goal
  alongside the inflated one, not just the stretch figure on its own.
- **Steps 2–3**: skip the shortfall framing entirely — don't report a
  shortfall percentage or a "required daily rate to catch up" figure.
  Still report the projected month-end total for reference, but state
  plainly that the target is a deliberate stretch goal (not one the
  business expects to hit) rather than something being "fallen short of."
  If a `seasonalized_targets` entry exists for the month, you may note
  how the projection compares to that real number instead (still without
  a formal shortfall %/catch-up-rate framing).
- **Step 4** is unaffected.

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
   send it. Both the Shopify and Superhuman Mail connectors are configured
   to always allow their respective actions (analytics queries; draft
   creation and sending) without a manual approval prompt.
2. **Fallback: Gmail draft.** If Superhuman Mail's tools aren't available
   in the session, create a Gmail draft (`mcp__Gmail__create_draft`) to
   the same recipients as above with the same subject and body, and note
   in the session output that it was drafted, not sent, and why (Gmail's
   connector here has no send action).

## Failure handling

These are unattended, scheduled runs — nobody is watching the session
output, so a run that just stops and reports failure only in the
transcript is invisible to Shaye. The email inbox is the only channel
that reliably reaches her, so any genuine failure must be reported there,
not just left in the session.

If the run cannot be completed for any reason — Shopify authorization has
expired, a required tool remains unavailable after a reasonable retry, the
session gets interrupted or restarted mid-run, the monthly target is
missing (see Inputs above; this one still sends the rest of the report,
just without target-relative math), or any other blocker — do not
fabricate numbers to fill the gap. Instead:

1. Report only what could actually be verified; state plainly what
   couldn't be completed and why.
2. Send a short failure-notification email **to `shaye@vergecollective.com.au`
   only** (no cc, regardless of which trigger fired), via the same
   delivery mechanism above (Superhuman primary, Gmail draft fallback).
   Subject: `⚠️ Stackers eComm Sales run rate – run failed ([date])`.
   Body: what was attempted, what specifically failed (e.g. "Shopify
   authorization appears expired", "the GitHub repo could not be read
   after retrying", "the session was interrupted before the report could
   be sent"), and whether any partial data was gathered.
3. This applies even if the failure happens partway through — e.g. the
   numbers were fully computed but the send itself failed, or a draft was
   created but never sent. Send the failure email rather than leaving the
   run silently incomplete.
4. Exception: don't send a failure email for something that resolved on
   its own within the same run after a normal retry (e.g. a transient MCP
   disconnect that reconnected a moment later) — only for a genuine
   blocker that actually prevented the report from being delivered.
