# Routine: BTK 3PL Weekly Invoice Automation

This is the full spec executed by the scheduled Claude trigger. It is also
the source of truth for the trigger's prompt — if you change behavior here,
update the trigger's prompt to match (`mcp__Claude_Code_Remote__update_trigger`
doesn't support editing the prompt directly; delete and recreate the trigger,
or ask Claude to do it).

Entity: Stackers Australia (The Verge Collective), ABN 56 691 572 817.

## Trigger and schedule

Fires **every Monday at 2:00pm AEST** (04:00 UTC — see the DST caveat at the
bottom of this doc's counterpart in the README). BTK's invoice email is
frequently late (observed arrivals as late as ~5–8pm AEST on a Monday), so
the trigger doesn't just run once and give up:

1. At 2:00pm AEST, search for this week's invoice email (see
   [Mail search](#mail-search) below).
2. **If found**: run the full pipeline in one pass — fetch, parse, validate,
   build the Xero journal, update the tracker, then notify (see
   [Step 6](#step-6--notify-bookkeeping-the-dext-pipeline) and
   [Step 7](#step-7--send-email-2-finance-summary)). Don't skip steps or
   pause for confirmation mid-run unless a critical data error is found
   that can't be resolved programmatically (see
   [Error handling](#step-8--error-handling)).
3. **If not found**: re-check **every hour** (use `send_later` to schedule
   the next check into the same session, so retry state isn't lost). Give
   up and email a plain-text heads-up to `finance@stackersau.com.au` if the
   invoice still hasn't arrived by **5:00pm AEST the following Tuesday**
   (~27 hours / 27 checks after the first one), then stop until next
   Monday's firing.
4. **Idempotency**: before processing, check whether a journal file for
   this invoice number already exists in `JOURNAL_OUTPUT_DIR` — if so, this
   invoice has already been handled this week; skip re-processing rather
   than duplicating work.

## Mail search

**Use Superhuman Mail, not the Gmail connector, for search and attachment
access.** The Gmail connector available in this environment can list
attachment filenames but has no tool to download attachment bytes — only
Superhuman Mail's `get_attachment` can. `finance@stackersau.com.au` is an
alias that delivers into the same mailbox as the primary Superhuman
account, so no extra account linking should be needed; if `list_threads`
turns up nothing, confirm via `list_accounts` that the right account is
still linked.

The email is a Xero-generated "invoice is due" notification (from
`messaging-service@post.xero.com`, relayed through a Google Group so it can
display with `finance@stackersau.com.au` as the apparent sender) with the
subject always starting `[Stackers Finance] Invoice #INV-`. Since several
other suppliers' Xero notifications share that `[Stackers Finance]` subject
prefix, disambiguate on the supplier name too:

```
mcp__Superhuman_Mail__list_threads
  subject_contains: "BTK Logistics"
  has_attachment: true
  start_date: <this week's Monday, 00:00 local>
```

From the matching thread's first message, download two attachments via
`get_attachment` (each returns a `download_url` — fetch it with `curl`,
these are pre-signed URLs valid for 1 hour and don't need further auth):

- The **PDF invoice** — filename contains `INV-` or `Invoice`
- The **Excel workbook** — filename ends in `.xlsx` and contains a BTK
  reference number (e.g. `invoice_2063_summary`)

If either attachment is missing, send a plain-text alert to
`finance@stackersau.com.au` and stop.

## File locations

```
JOURNAL_OUTPUT_DIR  = ~/stackers/btk/journals/
TRACKER_PATH        = ~/stackers/btk/BTK_Weekly_Charge_Tracker.xlsx
JOURNAL_FILENAME    = Xero_Journal_{INV_REF}_Stackers_WE_{WE_DATE}.xlsx
```

## Step 1 — Parse the Excel workbook

Open the workbook with `openpyxl`. Sheet names may vary slightly week to
week — match by closest string:

| Sheet | Purpose |
|---|---|
| Cover | Invoice totals summary |
| Warehouse Summary | Daily breakdown of all WH charges |
| Sale Orders | Per-order detail and charge descriptions |
| Consignments and Manifests | Per-consignment freight (connote level) |
| Consignment Data | Consignment metadata |
| Storage Summary | Authoritative storage charges |
| Purchase Orders | Inbound PO charges (present some weeks) |

### 1a — Invoice metadata (from the PDF)

Parse with `pypdf` (prefer this over `pdfplumber` — `pdfplumber` pulls in
`cryptography`/`cffi`, which failed to import in testing; `pypdf`'s
`extract_text()` worked cleanly and was sufficient for this invoice's
layout):

- Invoice number (e.g. `INV-0981`)
- BTK reference number (e.g. `2063`)
- Week ending date (e.g. `12/07/26` → format as `12Jul2026`)
- Invoice date
- All line items with their amounts
- Subtotal ex GST — this is the **cover total**; every downstream check
  must tie to this

### 1b — Storage (from Storage Summary tab)

- Row 4 onward: Description = pallet count or label, Charge = dollar amount
- Pallet storage = row where description is a number (the pallet count)
- B2B floor storage = row where description contains `FLOOR STORAGE`
- Sum all rows → must equal the Cover sheet storage line

### 1c — Orders and items (from Sale Orders tab)

- Read all rows where column B (index 1) is a numeric Sale Order ID
- **B2B orders** = rows where column E (index 4) contains the string
  `ADMIN FEE - B2B` — this is the sole authoritative B2B identifier (see
  [`config/btk-wholesale-customers.json`](../config/btk-wholesale-customers.json)
  for a secondary, informational customer-name heuristic — never let it
  override this flag)
- **eComm orders** = all other rows
- Record: total orders, B2B order count, eComm order count
- For each B2B order, extract item count using regex `Unit - \((\d+) @`
  from the charge description
- Sum all item counts: total items, B2B items, eComm items = total − B2B

### 1d — Wholesale freight (from Consignments and Manifests tab)

- For each B2B order reference (column B / index 1), find the matching
  consignment row
- The freight charge is in the **last non-null column** of that row (column
  count varies week to week — do not assume a fixed index)
- Sum all B2B consignment charges → `ws_freight`
- eComm freight = PDF freight line − ws_freight

### 1e — P&P and packaging pools (from Warehouse Summary tab)

Locate rows by their label in column A (index 0).

**P&P pool** (sum these rows):
- `Outbound Order` — flat per-order fees
- `Item Outbound` — tiered per-item fees
- `Labelling Fees` — outbound carrier labels

**Packaging pool** (sum these rows):
- `Paper Filler - Per Packing` and all variants
- All box/carton SKU rows (e.g. `260X190X22`, `270X195X165`,
  `370X260X150`, `265 x 195 x 265mm`, `Large Carton`, `380X270X180`, etc.)
- `A5 Invoice Enclosed Envelope`
- `Consumables - Packing Slip`
- `Fragile Label`
- `Pallet Wrapping` / `Pallet Wrap`
- `Pick and despatch rate pallet` / `Despatch Rate per Pallet`
- `Standard Pallet Supply`

**B2B admin fee**: row labelled `ADMIN FEE - B2B` — sum all numeric values.

**Verification**: P&P pool + Packaging pool + B2B admin fee must equal the
PDF Pick and Pack line (±$0.01 tolerance). If there is a Purchase Orders
tab, its charges may be bundled into the P&P invoice line — add them to the
pool if needed to reconcile.

**Urgent Order Charge**: a row with this label may appear in WH Summary.
Do not include it in the P&P pool unless it's needed to make the P&P line
reconcile. In recent weeks this row appears in WH but is not billed —
treat it as informational only if the pool already ties without it.

### 1f — Splits

- **P&P split**: by item count ratio. eComm P&P = pool × (eComm items ÷
  total items). WS P&P = pool − eComm P&P. Both rounded to 2 decimals.
- **Packaging split**: by order count ratio. eComm Pkg = pool × (eComm
  orders ÷ total orders). WS Pkg = pool − eComm Pkg. Both rounded to 2
  decimals.

### 1g — Other charges from the PDF

Scan all PDF invoice lines for items not covered by the standard WH
categories:

| PDF line contains | Journal category / channel | Flag |
|---|---|---|
| `Underpaid postage` / `Undercharged postage` | Freight Out / eComm | orange |
| `Labour` / `Stocktake` / `Adhoc labour` | Other 3PL Costs / no channel | verify rate vs. rate card |
| `Rubbish removal` / `Dunnage removal` | Other 3PL Costs / no channel | orange |
| `Phone line` | Other 3PL Costs / no channel | — |
| `Chep` / `Loscam` / `Delay days` | Other 3PL Costs / no channel | orange; verify $0.23/pallet/day (post-July) |
| `Fragile label` (separate PDF line, not in WH) | Packaging / eComm | — |
| Express shipping surcharge | Freight Out / Wholesale | flag if no rate card basis |
| Inbound Order fee (Purchase Orders section of WH) | Other 3PL Costs / no channel | — |
| Inbound container / putaway / pallet charges | Inbound / Receiving section | see Step 3 |

## Step 2 — Rate card verification

Determine which rate card applies based on invoice date: **before 1 July
2026** uses the pre-July rates; **1 July 2026 onward** uses the new card.
Both cards live in
[`config/btk-rate-cards.json`](../config/btk-rate-cards.json) — read that
file rather than hardcoding rates in code, so future rate changes are a
one-line config edit rather than a spec rewrite.

For each line, compare the implied rate to the rate card:

- Storage: total charge ÷ pallet count — flag if not within $0.01
- Admin fee: flag if not within $0.01 (post-July = $52.375, single line or
  split WMS + admin, both correct)
- Order fee: WH Outbound Order total ÷ total orders — flag if not within
  $0.01
- Labour: rate stated on PDF — flag if not $60.00 (pre-July) or $62.85
  (post-July)
- Chep/Loscam: rate stated on PDF — flag if not $0.22/$0.23 per
  pallet/day
- B2B admin: rate per instance — flag if not $3.00/$3.1425

Use **orange flag rows** for deviations that need review. Use **red flag
rows** for confirmed errors or charges with no rate card basis.

## Step 3 — Build the Xero journal (.xlsx)

Use `openpyxl`. Write hardcoded float values (not formulas) for all
amounts. Format unit prices to 4 decimal places (`$#,##0.0000`), amounts to
2 decimal places (`$#,##0.00`).

### Colour scheme

```python
NAVY      = "1F3864"   # header background
NAVY2     = "2E4A7A"   # summary strip
L_BLUE    = "DDEEFF"   # eComm rows
L_GREEN   = "DDFFDD"   # wholesale rows
L_GREY    = "F2F2F2"   # storage / admin rows
L_PURP    = "EDE7F6"   # inbound / receiving rows
ORANGE    = "FFE5B4"   # flag rows (orange)
RED_BG    = "FFD0D0"   # flag rows (red/error)
GREEN_CHK = "E2EFDA"   # verification pass rows
YELLOW    = "FFFF00"   # totals row
```

### Sheet structure

- **Row 1**: Dark navy, merged A:I — `{INV_REF} | W.E. {WE_DATE} |
  Stackers Australia (The Verge Collective) | BTK 3PL Xero Journal`
- **Row 2**: Navy2, merged A:I — `Invoice #: {PDF_INV_NO} | Period:
  {period} | Total ex GST: ${total} | Total Orders: {n} | eComm: {n} |
  Wholesale B2B: {n} | {rate card note if applicable}`
- **Row 3**: Spacer (height 6)
- **Row 4**: Column headers (dark navy, white bold): Description | Qty |
  Unit Price | Account | Tax Rate | CAC | Channel | Amount AUD | Notes /
  Source
- **Rows 5+**: Data rows (see below)

### Journal lines, in order

Each line: description, qty, unit_price, account, `"GST on Expenses"`,
`""`, channel, amount, notes.

1. **Pallet Storage** — `3PL Storage` / no channel / L_GREY
2. **B2B Floor Storage** (if present) — `3PL Storage` / no channel / L_GREY
3. **Invoice Administration Fee** — `Other 3PL Costs` / no channel / L_GREY
4. **Outbound Freight — eComm** — `Freight Out` / eComm / L_BLUE
5. **Outbound Freight — Wholesale** (if B2B orders exist) — `Freight Out` /
   Wholesale / L_GREEN
6. **Express Shipping Surcharge** (if on PDF) — `Freight Out` / Wholesale /
   L_GREEN + orange flag
7. **Pick + Pack — eComm** — `Pick + Pack` / eComm / L_BLUE — qty = eComm
   items
8. **Pick + Pack — Wholesale** (if B2B) — `Pick + Pack` / Wholesale /
   L_GREEN — qty = B2B items
9. **Packaging — eComm** — `Packaging` / eComm / L_BLUE — qty = eComm
   orders
10. **Packaging — Wholesale** (if B2B) — `Packaging` / Wholesale / L_GREEN
    — qty = B2B orders
11. **Inbound / Receiving lines** (if present, each as a separate row) —
    `Other 3PL Costs` / no channel / L_PURP: Container Unload; Putaway
    (pallets — may include inbound order fee and inbound labelling);
    Pallet (empty pallet purchase); Cartage & Dehire
12. **B2B Admin Fee** — `Other 3PL Costs` / Wholesale / L_GREEN
13. **Inbound Order Fee** (if present, not bundled into above) — `Other
    3PL Costs` / no channel / L_GREY
14. **Labour / Stocktake** lines (one row per distinct PDF line item) —
    `Other 3PL Costs` / no channel / L_GREY
15. **Underpaid Postage** (if present) — `Freight Out` / eComm / ORANGE +
    orange flag
16. **Rubbish Removal** (if present) — `Other 3PL Costs` / no channel /
    ORANGE + orange flag
17. **Phone Line** (if present) — `Other 3PL Costs` / no channel / L_GREY
18. **Chep/Loscam Delay Days** (if present) — `Other 3PL Costs` / no
    channel / ORANGE + orange flag
19. **Fragile Labels ad hoc** (if on PDF only) — `Packaging` / eComm /
    L_GREY

After all data rows:

- Green verification row: storage check result
- Green info row: Urgent Order Charge note, if applicable
- **Totals row** (yellow): `TOTAL` right-aligned in col A, hardcoded sum in
  col H
- **Verification row** (yellow): text cell showing `✓ TIES — $X,XXX.XX` or
  `⚠ DIFFERENCE: $X.XX`

### Final check

Sum all Amount AUD cells in the journal. Compare to the PDF subtotal ex
GST. If the difference is > $0.01, raise an exception — do not send the
email. Log the discrepancy and stop.

### Formatting

Column widths: A=56, B=8, C=13, D=20, E=20, F=7, G=12, H=14, I=65. Freeze
panes at A5.

## Step 4 — Update the weekly charge tracker

Load `BTK_Weekly_Charge_Tracker.xlsx` with openpyxl.

### Column layout

- Column A = Line Description
- Separator columns (width 1.2, grey fill) at B, F, J, N, … (every 4th
  column after A)
- Each week occupies 3 columns: Qty | Unit Price | Amount AUD
- **Most recent week always in columns C, D, E**
- Historical weeks follow to the right, oldest last

### To insert a new week

1. Insert 4 new columns at position 3 (after column B)
2. Set widths: C=8, D=12, E=12, F=1.2
3. Fill separator column F with grey (`D9D9D9`)
4. Write week header in C4:E4 (merged): `{INV_REF} | W.E. {WE_DATE}`
5. Write sub-headers in row 5: Qty / Unit Price / Amount AUD
6. Write data values for the new week in column C (qty), D (unit price), E
   (amount)
7. Update the TOTAL row (last data row) with the new week's hardcoded
   cover total in column E

### Row mapping (match by description in column A)

Write the new week's values into these rows:

| Description | Qty | Unit Price | Amount |
|---|---|---|---|
| Pallet Storage | pallet count | rate | amount |
| B2B Floor Storage | unit count | rate | amount (blank if absent) |
| Invoice Administration Fee | 1 | rate | amount |
| Outbound Freight — eComm | eComm consignment count | avg | amount |
| Outbound Freight — Wholesale | B2B consignment count | avg | amount (blank if absent) |
| Express Shipping Surcharge — TJX | 1 | amount | amount (blank if absent) |
| Pick + Pack — eComm | eComm items | avg | amount |
| Pick + Pack — Wholesale | B2B items | avg | amount (blank if absent) |
| Packaging — eComm | eComm orders | avg | amount |
| Packaging — Wholesale | B2B orders | avg | amount (blank if absent) |
| Container Unload | 1 | amount | amount (blank if absent) |
| Putaway | pallet count | avg | amount (blank if absent) |
| Pallet | 1 | amount | amount (blank if absent) |
| Cartage & Dehire | count | avg | amount (blank if absent) |
| B2B Admin Fee | order count | rate | amount (blank if absent) |
| Inbound Order Fee | 1 | amount | amount (blank if absent) |
| Chep Pallet Delay Days | pallet count | avg | amount (blank if absent) |
| Underpaid / Undercharged Postage | 1 | amount | amount (blank if absent) |
| Rubbish Removal | 1 | amount | amount (blank if absent) |
| Phone Line | 1 | amount | amount (blank if absent) |
| Fragile Labels (ad hoc) | count | 0.20/0.42 | amount (blank if absent) |
| Labour — Ad Hoc | total hrs | rate | amount (blank if absent) |

For blank cells: leave the cell empty (`None`). Apply the same row
background colour as neighbouring weeks for that row. Save the tracker to
the same path.

## Step 5 — Compile weekly insights

### eComm metrics

- Pick + Pack cost for the week: `pp_ecomm`
- Pick + Pack average per order: `pp_ecomm ÷ ecomm_orders`
- Average items per eComm order: `ecomm_items ÷ ecomm_orders`
- Packaging cost for the week: `pkg_ecomm`
- Packaging average per order: `pkg_ecomm ÷ ecomm_orders`
- Postage (freight) cost for the week: `ecomm_freight`
- Postage average per order: `ecomm_freight ÷ ecomm_orders`
- Average weight per order: from Consignment Data tab — sum all eComm
  consignment weights ÷ eComm order count. If weight data is unavailable,
  write "N/A"
- eComm orders fulfilled: `ecomm_orders`

### B2B metrics (if any B2B orders this week)

- Pick + Pack cost for the week: `pp_ws`
- Pick + Pack average per order: `pp_ws ÷ b2b_orders`
- Average items per B2B order: `b2b_items ÷ b2b_orders`
- Packaging cost for the week: `pkg_ws`
- Packaging average per order: `pkg_ws ÷ b2b_orders`
- Postage (freight) cost for the week: `ws_freight`
- Postage average per order: `ws_freight ÷ b2b_orders`
- Average weight per order: same method as eComm, B2B consignments only
- B2B orders fulfilled: `b2b_orders`

### Rate card flags

Collect all flag rows generated during Step 2. Summarise each flagged
item, the charged amount, expected amount, difference, and whether it's an
orange (review) or red (error) flag.

## Known limitation: no tool can send attachments

As of this writing, nothing in this environment can send an email *with a
real attachment*: Gmail's `create_draft` accepts an `attachments` field in
its schema but its own description says attachments aren't actually
supported yet; Superhuman Mail's `create_or_update_draft` has no
attachments parameter at all (`send_draft` only sends whatever's already on
the draft). Since Dext needs actual attached files to ingest a bill (a
link doesn't work for its OCR pipeline), Steps 6 and 7 below are adjusted
accordingly: the automation builds the files and gets them to the fixed
paths below, then sends **one notification email with no attachments**
telling Shaye what's ready. A human does the final "attach the files and
hit send" step — a few clicks, not a rewrite of the whole pipeline. If a
future connector update adds real attachment support to either tool,
Steps 6/7 should revert to sending both emails directly with attachments
as originally specified below.

## Step 6 — Notify: bookkeeping / Dext pipeline

Originally specified as a direct send, this is currently a **notification
only** (see the limitation above) — no attachments are sent.

- **To**: `finance@stackersau.com.au` (send to Shaye, not directly to Dext,
  since she must attach and forward the real files herself)
- **Subject**: `BTK 3PL Invoice ready to send — {PDF_INV_NO} | W.E.
  {WE_DATE} | Stackers Australia`
- **No attachments** — reference the fixed file paths instead

Body (plain text or simple HTML):

```
Hi,

This week's BTK Logistics 3PL invoice has been processed and is ready to
send to the bookkeeping pipeline. The Xero journal has been verified
against the BTK supporting workbook and ties to the invoice subtotal — no
attachments could be sent automatically (known tool limitation, see the
routine doc), so please attach these three files and forward to
tvcollective@dext.cc and tracey@jog.com.au (cc finance@stackersau.com.au):

  1. Invoice {PDF_INV_NO}.pdf (original, from the BTK email)
  2. {the BTK workbook .xlsx} (original, from the BTK email)
  3. {JOURNAL_OUTPUT_DIR}/Xero_Journal_{INV_REF}_Stackers_WE_{WE_DATE}.xlsx

Invoice #:      {PDF_INV_NO}
Period:         {period}
Total ex GST:   ${cover_total}
Total inc GST:  ${cover_total_inc_gst}
Due date:       {due_date}

{IF flags exist:}
⚠ Rate card flags this week:
{list each flag as: - [LINE]: charged ${x}, rate card ${y}, difference ${z}}

{IF no flags:}
✓ All charges verified against rate card — no discrepancies.

Regards,
Stackers Finance Automation
```

If any red (confirmed error) flag exists, prefix the subject with `⚠
ACTION REQUIRED — `.

## Step 7 — Send email 2: finance summary

- **To**: `finance@stackersau.com.au`
- **Subject**: `BTK 3PL Weekly Summary — W.E. {WE_DATE} | ${cover_total} ex
  GST`
- **No attachment** (known tool limitation, see above) — mention that the
  updated tracker is at `TRACKER_PATH` instead of attaching it

Body (HTML): a summary with an eComm fulfilment table, a B2B/wholesale
fulfilment table (only if `b2b_orders > 0`), a rate card check section
(clean pass message, or a flag table if any flags exist), an "Other
Notable Items" bullet list for irregular charges (labour, postage
adjustments, inbound, Chep hire, rubbish removal, etc.), and a storage
section reporting pallets on hand and the change vs. the prior week (plus
B2B floor storage unit count, if present). Close with a footer noting the
summary was generated automatically from the BTK invoice and should be
verified against source documents before posting to Xero.

In practice, Steps 6 and 7 can be combined into a single notification email
rather than two separate sends, since neither carries attachments — use
judgement, but don't drop any of the content above.

## Step 8 — Error handling

| Condition | Action |
|---|---|
| Invoice email not found by the Tuesday 5pm AEST cutoff | Email alert to finance@, stop until next Monday |
| Attachment missing from the invoice email | Email alert to finance@, stop |
| PDF subtotal not parseable | Email alert, stop |
| Journal total ≠ PDF subtotal (>$0.01) | Email alert with diff, stop — do not send the bookkeeping notification |
| Tracker file not found | Create a new tracker from scratch using current week only, warn in finance email |
| Rate card flag detected | Include in both emails, do not stop — pay as invoiced |
| Red flag (billing error) detected | Mark as urgent in subject line of the bookkeeping notification: prefix `⚠ ACTION REQUIRED —` |
| B2B weight data unavailable | Write "N/A" in summary table, continue |
| Journal file for this invoice number already exists | Already processed this week — skip silently, don't re-notify |

## Known data quirks

These are recurring patterns to handle proactively — do not flag as
errors:

- **Urgent Order Charge** row appears in WH Summary but is often not
  billed. Check whether the P&P pool ties without it; if yes, skip it and
  add a green info note in the journal.
- **Purchase Orders tab** may contain an Inbound Order Fee that is bundled
  into the P&P invoice line. Include it in the pool reconciliation.
- **B2B references** are not always obvious from the delivery name. Rely
  exclusively on `ADMIN FEE - B2B` in column E of Sale Orders as the
  authoritative B2B identifier.
- **Storage Summary** is authoritative over WH Summary for storage. If
  they disagree, use whichever ties to the Cover sheet total and flag the
  discrepancy.
- **Consignment & Manifests column count** varies. The freight total is
  always in the **last non-null column** of the row, not a fixed column
  index.
- **Admin fee post-July 2026**: $52.375 may appear as a single line or as
  two lines ($36.6625 WMS + $15.7125 admin). Both are correct — accept
  either format.
- **Item outbound bundling pre-July 2026**: the $1.62 first-item rate is a
  confirmed bundled rate (pick + label + fragile). Do not flag as
  overcharge.
- **Inbound labelling and inbound order fee**: when present, fold into the
  Putaway journal line if they are ancillary to a container receipt. Break
  them out as a separate Inbound Order Fee row if they appear without a
  container receipt.
- **Wholesale customer identification**: the authoritative signal is
  always `ADMIN FEE - B2B` in the workbook (see 1c). The named-customer and
  reference-pattern list in
  [`config/btk-wholesale-customers.json`](../config/btk-wholesale-customers.json)
  is informational only, for spotting new accounts — expand it as new
  wholesale customers are encountered.

## Validated against a real invoice

Tested end-to-end against the real INV-0981 / BTK ref 2063 / W.E. 12 Jul
2026 invoice: parsing, rate card checks, and journal construction produced
a total that tied exactly to the PDF subtotal ($3,233.30, $0.00
difference) with no rate card flags, and matched — line for line, amount
for amount — the journal Shaye had already built by hand for the same
invoice. One data-quality note from that run, unrelated to this spec's
logic: the Consignment Data tab can contain an obviously-wrong weight (e.g.
734kg where 0.734kg was clearly meant), which will skew the Step 5 average
weight metric. Nothing in this spec asks for outlier correction, so the
routine doesn't attempt it — just be aware the weight metric can be noisy.

## Python dependencies

```
pip install openpyxl pypdf
```

`openpyxl` with `data_only=True` is sufficient for reading the workbook
(no `pandas` needed). No Gmail/Google API libraries are needed — mail
access goes through the Superhuman Mail MCP tools plus `curl` for
attachment download URLs (see [Mail search](#mail-search)).
