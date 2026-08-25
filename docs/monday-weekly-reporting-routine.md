# Routine: Monday Weekly Reporting — Data Capture

Full spec for a weekly data-capture routine covering Stackers Australia's
eComm and B2B channels. Unlike the [run-rate routine](shopify-sales-runrate-routine.md),
this one does not draft or send an email — it captures a wide set of
metrics from multiple sources into a single weekly record.

**Status: spec only.** No trigger has been created for this yet — see
[Prerequisites](#prerequisites) for what's outstanding.

## Schedule (once live)

Runs once a week, Monday morning, Australia/Adelaide time. Adelaide is
`ACST` (UTC+9:30) outside daylight saving and `ACDT` (UTC+10:30) during it
(early October–early April) — same caveat as the run-rate routine's
Sydney cron: the underlying scheduler runs on a fixed UTC offset, so the
cron expression must be shifted by an hour around each DST transition or
the run will land an hour off local time.

## Prerequisites

Confirmed available in this account:
- **Superhuman Mail** (or Gmail) — connected, used for the BTK invoice gate
  check. Superhuman Mail's `query_email_and_calendar` is the more reliable
  way to locate the invoice email by content rather than guessing the exact
  subject line — see Step 2.
- **Shopify** — connected, used for Step 3.
- **Google Drive** — connected, used to write the output row (Step 8).

Not yet confirmed — the routine should still run without these (each
missing source records `MISSING` with a reason per the Rules below), but
the report will be incomplete until they're sorted:
- **Klaviyo** — connector installed but not authenticated in this account.
- **Meta Ads Manager / Google Ads** — no browser-automation connector
  (e.g. Claude in Chrome, Playwright MCP) is configured. Both surfaces
  need a logged-in browser session; there's no lightweight API path for
  either.
- **Cin7 Core** — no API credentials (`CIN7_ACCOUNT_ID` / `CIN7_API_KEY`)
  configured for the session. Browser fallback to Cin7 Core → Sales is
  possible but needs the same browser-automation connector as above.

## Step 1 — Compute the reporting week

- Expected to run on a Monday (Australia/Adelaide). If the trigger fires
  on any other day, stop and report that this task only runs on Mondays.
- **Reporting week** = the previous Monday through yesterday (Sunday),
  inclusive. **Week ending** = yesterday's date.
- Example: run on Monday 13 July 2026 → reporting week is Monday 6 July –
  Sunday 12 July 2026, week ending Sunday 12 July 2026.
- State the computed dates at the top of the output and use them
  consistently in every step below.

## Step 2 — GATE CHECK: BTK 3PL weekly invoice email

Do this **first**, before any other step.

**Finding the email.** There is no "BTK 3PL Weekly Invoice Automation"
email — a one-off CartonCloud automation with that description existed
briefly in January 2026 and was discontinued. The real weekly source is
the Xero invoice-due notice, sent Monday mornings:
- **Subject:** `[Stackers Finance] Invoice #INV-XXXX from BTK Logistics (3PL) Pty Ltd is due`
- **From:** `messaging-service@post.xero.com`
- **Attachment:** `invoice_XXXX_summary_.xlsx` — this is the file to parse,
  not the email body or the PDF.

Search by content (e.g. Superhuman Mail's `query_email_and_calendar`, or a
subject/sender search for `BTK Logistics` + `is due`) rather than an exact
subject match — the invoice number changes every week. Confirm the
attachment's `Cover` sheet `Period:` row covers the reporting week from
Step 1 before trusting the file; BTK invoice numbers aren't reliably
sequential with calendar weeks.

- **Not present** → stop immediately. Output exactly:
  `ABORTED: BTK weekly invoice email not yet received as of [timestamp]. Re-run once it arrives.`
  Do not proceed to any other step — this gate exists to avoid burning a
  run that can't complete.
- **Present** → download the `invoice_XXXX_summary_.xlsx` attachment and
  extract the figures below, keeping eComm and B2B strictly separate.

**Identifying B2B vs eComm.** The workbook does not pre-split channels —
everything must be derived from the `Sale Orders` sheet. A row is a
**B2B** order if its `Reference` or `Charge Description` column contains
the literal text `B2B` (BTK tags these orders, e.g. `ADMIN FEE - B2B`);
every other row is **eComm**. In a typical week there are far more eComm
rows than B2B rows (recent samples: 50 eComm to 1 B2B).

**Pick & pack + packaging.** BTK invoices these as a single combined
line — there is no separate "packaging" total anywhere in the workbook.
To split them, parse each order's `Charge Description` text (column E of
`Sale Orders`) into two buckets:
- *Packaging materials* — lines matching `Paper Filler`, any carton/box
  SKU line (e.g. `07.C46265.0x`, `NNN x NNN x NNNmm`), `Large Carton`,
  `Labelling Fees`, an envelope line, `Consumables`, `Fragile Label`.
- *Pick & pack proper* — everything else (`Flat fee for processing Sale
  Order`, `Charges for Outbound Units`, `Urgent Order Charge`, any
  `ADMIN FEE` line).

  Sum each bucket per channel (`charge` column total, split into the two
  buckets above); `orders fulfilled` = row count per channel; `units/order`
  = average of the unit count parsed from each row's `Unit - (N @ ...)`
  text. Sanity-check: eComm total + B2B total for each bucket must equal
  the workbook's own totals (`Sale Orders: NN` cell in `Warehouse Summary`,
  matching `Charges for Pick and Pack` on the `Cover` sheet) — if they
  don't reconcile to the cent, something was mis-parsed; flag it rather
  than reporting the numbers.

**Postage.** Use the `Consignments and Manifests` sheet, not
`Consignment Data` (the latter's `Fee Total`/`Total` columns are usually
zero for non-B2B rows). Each row there is one order; split eComm/B2B the
same way as above (by `Reference`), sum `Total` per channel for postage
cost, and average `# WGT (kg)` per channel for average weight/order. This
must reconcile to the `Cover` sheet's `Charges for Freight` total and to
`Consignments and Manifests`' own `TOTAL CHARGES` cell.

**Storage.** Single combined figure — `Cover` sheet `Charges for Storage`
(also on the `Storage Summary` sheet). Not split by channel; BTK doesn't
track storage per channel.

Sanity-check that the invoice's `Period:` on the `Cover` sheet matches the
reporting week from Step 1. If the dates don't match, flag it prominently
but still record the figures with a warning attached.

## Step 3 — Shopify

For the reporting week (Monday through Sunday inclusive), fetch:
- Total orders sold
- Total net sales
- Total shipping charges

Use Shopify's analytics definitions (net sales = gross sales − discounts
− returns, excluding shipping and tax). Confirm the date range applied
matches Step 1 exactly before recording anything.

## Step 4 — Meta Ads Manager (browser)

Open the saved report at Ads Manager account `2379700205613473`
(business `109826206389166`, saved report `120248859716940287`),
substituting the reporting week into `time_range` as
`YYYY-MM-DD_YYYY-MM-DD` (start = reporting Monday, end = reporting
Sunday). After the page loads, verify the date picker in the UI shows
exactly Monday–Sunday of the reporting week — if it doesn't, set it
manually before reading any numbers. Then fetch:
- ROAS results total
- Total amount spent

## Step 5 — Google Ads (browser)

Open the Google Ads overview for account OCID `7794011646`. Set the
date range in the top-right date picker to the reporting week
(Monday–Sunday inclusive). Verify the displayed range before reading
anything. Then fetch:
- Actual ROAS total (conversion value ÷ cost)
- Total cost

## Step 6 — Klaviyo

For the reporting week (Monday–Sunday inclusive, Australia/Adelaide
timezone), fetch:
- **Attributed revenue** using the **Shopify Placed Order** metric
  (Klaviyo-attributed conversions across campaigns + flows).
- Compute the **ex-GST figure**: attributed revenue ÷ 1.1. Report both
  the raw and ex-GST figures, clearly labelled.

## Step 7 — Cin7 Core

Prefer the Cin7 Core API. Fetch all sales **invoiced** during the
reporting week (filter on invoice date, not order date), **excluding any
sale where the customer name contains "Shopify"**. Report:
- Number of invoices
- Total sales value **excluding tax**

If the API call fails or credentials aren't configured, fall back to the
browser (Cin7 Core → Sales → filter by invoice date range → exclude
Shopify customers) and note that the fallback was used.

## Step 8 — Output: Google Sheet

Append one row to the **Stackers Weekly Reporting** sheet:
`https://docs.google.com/spreadsheets/d/1CKuu03eYqRdXnyT2w0fQ-8d_F7-Xg6UmY6dCCW4YgTA/edit`
(in the *Weekly Sales Reports* Drive folder). One row per week ending
date, one column per metric — the header row already lists every column
in the same grouping as Steps 2–7 (Shopify, eComm, B2B, storage, Meta,
Google Ads, Klaviyo, Cin7), plus `Week Ending`, `Reporting Week`, `Run
Timestamp`, and a trailing `Notes/Flags` column for anything flagged
during capture (e.g. BTK date mismatch, fallback used, missing figures
and their reasons).

Before appending, check whether a row for this `Week Ending` date already
exists — don't create a duplicate if the routine is re-run after an
earlier successful capture for the same week.

## Rules (apply throughout)

- Never invent, estimate, or interpolate a figure. `MISSING` + a one-line
  reason is always the correct answer for anything that can't be
  retrieved — record it and continue to the next source rather than
  stopping the whole run.
- Always verify the on-screen/queried date range equals the reporting
  week before capturing a number. URL parameters are not trusted — for
  browser steps, the visible UI state is the source of truth.
- Finish with a summary line:
  `X of Y metrics captured. Missing: [list or "none"]. Flags: [list or "none"].`
