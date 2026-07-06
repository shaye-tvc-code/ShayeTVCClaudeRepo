# Stackers eComm Sales Run Rate

Automated recurring report on Stackers' monthly Shopify sales run rate,
emailed to shaye@vergecollective.com.au.

## What it does

Every **Tuesday, Wednesday, Thursday, and Friday at 6am (Australia/Sydney)**,
and every **Monday at 6am (Australia/Sydney)**, a scheduled Claude session:

1. Pulls net sales data from Shopify (via the Shopify MCP connector).
2. Computes the month's run rate against target (see [`docs/shopify-sales-runrate-routine.md`](docs/shopify-sales-runrate-routine.md)
   for the full spec and formulas).
3. Emails the summary to shaye@vergecollective.com.au.

On Tuesday–Friday runs, the "since last report" commentary (Step 4) covers
the single prior calendar day. On Monday runs, it covers Saturday and Sunday
combined, since there is no report over the weekend. The cumulative
month-to-date figures (Steps 1–3) always reflect every day up to the most
recently completed trading day, so weekend sales are never missed from the
running total.

## How it's implemented

This repo holds the **spec**, not application code. The actual work runs as
a scheduled Claude Code trigger (a "Routine") that, on each firing, spins up
a fresh Claude session with the full instructions in
[`docs/shopify-sales-runrate-routine.md`](docs/shopify-sales-runrate-routine.md)
and live access to the Shopify and email MCP connectors already authorized
on the account. There's no separate server or script to deploy — updating
the behavior means editing that doc and updating the trigger's prompt to
match.

## Prerequisites / setup

- **Shopify connector must stay authorized.** If it expires, the trigger's
  run will fail to pull sales data. Re-authorize via claude.ai connector
  settings.
- **Monthly sales target**: stored as a Shopify shop metafield,
  namespace `custom`, key `monthly_sales_target` (numeric, in the store's
  currency). Update this once at the start of each month in
  Shopify Admin → Settings → Custom data → Shop metafields. If it's
  missing, the report will call that out instead of guessing.
- **Email delivery**: the routine sends via whichever connector is
  available and capable of sending on the account (e.g. Superhuman Mail),
  falling back to creating a Gmail draft addressed to
  shaye@vergecollective.com.au if no send-capable connector is enabled.
  Enable the preferred connector in this chat's connector settings.

## Known limitation: daylight saving time

The trigger's cron schedule is defined in UTC (the underlying scheduler
does not support IANA timezones). It's set assuming AEST (UTC+10). During
AEDT (UTC+11, roughly early October–early April), the report will actually
land at 7am Sydney time instead of 6am until the cron offset is manually
shifted. Two reminder triggers are scheduled for the DST transition dates
to prompt that one-hour adjustment.
