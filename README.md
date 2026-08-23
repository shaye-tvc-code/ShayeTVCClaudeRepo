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
- **Monthly sales target**: stored in [`config/monthly-sales-targets.json`](config/monthly-sales-targets.json)
  in this repo, keyed by `YYYY-MM`. Tell Claude the number for a new month
  and it'll commit the update to that file. If the current month has no
  entry, the report calls that out instead of guessing.
- **Email delivery**: sends via Superhuman Mail (`create_or_update_draft` +
  `send_draft`), which must stay enabled for the session/chat the trigger
  runs in. Falls back to creating a Gmail draft addressed to
  shaye@vergecollective.com.au if Superhuman's tools aren't available.

## Known limitation: daylight saving time

The trigger's cron schedule is defined in UTC (the underlying scheduler
does not support IANA timezones). It's set assuming AEST (UTC+10). During
AEDT (UTC+11, roughly early October–early April), the report will actually
land at 7am Sydney time instead of 6am until the cron offset is manually
shifted (subtract one hour from the UTC hour, e.g. `0 20 * * 1-4` becomes
`0 19 * * 1-4`, around the first Sunday of October and April each year).

---

# BTK 3PL Weekly Invoice Automation

Automated weekly processing of the BTK Logistics 3PL invoice for Stackers
Australia (The Verge Collective, ABN 56 691 572 817).

## What it does

Each week, BTK Logistics' invoice arrives as a Xero "invoice is due" email
with a tax invoice PDF and a supporting Excel workbook attached, addressed
to `finance@stackersau.com.au`. It's often late (observed as late as
~5–8pm AEST some Mondays). Starting Monday 2pm AEST, a scheduled Claude
session checks for it, retrying hourly if it hasn't arrived yet (giving up
and alerting by Tuesday 5pm AEST). Once found, it:

1. Downloads both attachments (via Superhuman Mail — see
   [How it's implemented](#how-its-implemented)).
2. Parses and validates the invoice data against the workbook, tying every
   line back to the invoice subtotal (see
   [`docs/btk-3pl-invoice-automation-routine.md`](docs/btk-3pl-invoice-automation-routine.md)
   for the full spec, including the eComm/wholesale split logic and rate
   card checks).
3. Builds a Xero-format journal spreadsheet (`.xlsx`).
4. Updates the running `BTK_Weekly_Charge_Tracker.xlsx`.
5. Sends a notification email summarizing the week (fulfilment metrics,
   any rate card flags) — see the attachment limitation below for why this
   isn't the two fully-automated, attachment-carrying emails the original
   spec described.

Any invoice line that doesn't tie to the workbook, or that deviates from
the rate card, is flagged (orange for review, red for a confirmed billing
error) rather than silently corrected — see Step 8 of the routine doc for
the full error-handling table.

Validated end-to-end against a real invoice (INV-0981, W.E. 12 Jul 2026):
the computed total tied exactly to the PDF subtotal and matched a
manually-built journal for the same week line for line. Details in the
routine doc's "Validated against a real invoice" section.

## Known limitation: no attachment support on send

Right now, nothing available in this environment can send an email with a
real file attachment: Gmail's `create_draft` claims an `attachments` field
but its description says attachments aren't actually supported yet, and
Superhuman Mail's draft tool has no attachments parameter at all. Since
Dext needs real attached files (not a link) to ingest a bill, the
automation currently stops short of the original spec's "send two emails
with attachments" and instead builds the files, then sends one
notification-only email telling Shaye what's ready — she does the final
attach-and-send step by hand. If a connector update adds real attachment
support later, this should revert to the original fully-automated
two-email flow (Steps 6–7 in the routine doc).

## How it's implemented

This repo holds the **spec**, not application code. The actual work runs
as a scheduled Claude Code trigger (a "Routine") that, on each firing,
spins up a fresh Claude session with the full instructions in
[`docs/btk-3pl-invoice-automation-routine.md`](docs/btk-3pl-invoice-automation-routine.md)
and live access to the Superhuman Mail connector and file tooling needed to
build the journal and tracker spreadsheets. There's no separate server or
script to deploy — updating the behavior means editing that doc and
updating the trigger's prompt to match.

## Prerequisites / setup

- **Superhuman Mail connector must stay authorized** for the account that
  receives `finance@stackersau.com.au` mail (an alias of
  `shaye@vergecollective.com.au`). The Gmail connector cannot download
  attachment bytes, so the trigger relies on Superhuman Mail instead — if
  it expires, the trigger can't fetch the invoice or its attachments.
- **Rate cards**: stored in
  [`config/btk-rate-cards.json`](config/btk-rate-cards.json), keyed by
  effective date (pre- and post-1 July 2026). Update this file when BTK's
  pricing changes rather than editing the routine doc.
- **Wholesale customer list**: stored in
  [`config/btk-wholesale-customers.json`](config/btk-wholesale-customers.json).
  This is a secondary, informational heuristic only — the authoritative
  B2B identifier is always the `ADMIN FEE - B2B` flag in the workbook
  itself. Expand this list as new wholesale accounts are encountered.
- **Local file paths**: the routine reads/writes
  `~/stackers/btk/journals/` and `~/stackers/btk/BTK_Weekly_Charge_Tracker.xlsx`
  in the environment the trigger runs in — these are not part of this
  repo, and persist only because the trigger reuses the same environment
  each week rather than a throwaway one.
- **Email delivery**: sent via Superhuman Mail
  (`create_or_update_draft` + `send_draft`) — notification-only, no
  attachments (see the limitation above).

## Known limitation: daylight saving time

Same caveat as the Shopify routine above: the trigger's cron is defined in
UTC assuming AEST (UTC+10) year-round. During AEDT (UTC+11, roughly early
October–early April), the trigger will actually fire at 3pm Sydney/Melbourne
time instead of 2pm until the cron offset is manually shifted (subtract one
hour from the UTC hour, e.g. `0 4 * * 1` becomes `0 3 * * 1`, around the
first Sunday of October and April each year).
