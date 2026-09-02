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

Previous days' report emails still sitting in the inbox get archived by
the separate Daily Inbox Cleanup routine (Rule 10 — see below), not by
this routine itself.

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

# Daily Inbox Cleanup

Automated daily sweep of `shaye@vergecollective.com.au`'s inbox (and
aliases like `finance@vergecollective.com.au` that deliver into it) that
deletes email matching a growing set of rules Shaye defines over time.

## What it does

Every morning, a scheduled Claude session checks the inbox against every
rule recorded in [`config/inbox-cleanup-rules.md`](config/inbox-cleanup-rules.md)
and deletes (moves to Trash) anything that matches. It only acts on
explicitly recorded rules — it never guesses at new ones. When a rule's
condition can't be confidently evaluated against a specific email (the
format looks different than expected, an exception might apply but the
wording is unclear), the routine leaves that email alone and emails Shaye
a question instead — she can reply by email and the next day's run picks
up the answer, so nothing requires coming back into Claude Code. See
[`docs/inbox-cleanup-routine.md`](docs/inbox-cleanup-routine.md) for the
full process spec, including that question/reply protocol and the safety
rules (deletions are always to Trash, never permanent).

Current rules (see the config file for full detail, including tested
edge cases):

1. **Cin7 Core ("sin7cor") Xero autosync reports** to `finance@` — deleted
   only when the sync **completed** and the processed/successful record
   counts match; sync **failures** are always kept, even if their counts
   happen to match.
2. **Asana task update notifications** (`no-reply@asana.com`) — deleted
   in full. Asana billing receipts and marketing email from other Asana
   senders are a different thing and are left alone.

## How it's implemented

This repo holds the **spec**, not application code. The actual work runs
as a scheduled Claude Code trigger (a "Routine") that, on each firing,
spins up a fresh Claude session with the full instructions in
[`docs/inbox-cleanup-routine.md`](docs/inbox-cleanup-routine.md) and live
access to the mail MCP connectors already authorized on the account.
Adding a new cleanup rule means telling Claude in a session — it tests the
rule against the live inbox, records it in
[`config/inbox-cleanup-rules.md`](config/inbox-cleanup-rules.md), and
commits; the trigger picks it up on its next daily firing without needing
its own prompt changed (unless the *process* changes, not just the
ruleset).

## Prerequisites / setup

- **Superhuman Mail connector must stay authorized** for
  `shaye@vergecollective.com.au` — this is the primary tool used for
  search, reading, and trashing mail. Falls back to the Gmail connector if
  Superhuman Mail's tools are unavailable in a given run.
- **Gmail connector** is used specifically for label management (creating
  and checking the `InboxCleanup/AwaitingReply` label used by the
  question/reply protocol).
- **Rules**: stored in
  [`config/inbox-cleanup-rules.md`](config/inbox-cleanup-rules.md) as an
  append-only, dated ledger — each entry records the sender/subject/body
  condition, the action, and how it was validated against the real inbox.

## Known limitation: daylight saving time

Same caveat as the Shopify routine above: the trigger's cron is defined in
UTC assuming AEST (UTC+10) year-round. During AEDT (UTC+11, roughly early
October–early April), the trigger will actually fire at 7:15am
Sydney time instead of 6:15am until the cron offset is manually shifted
(subtract one hour from the UTC hour, e.g. `15 20 * * *` becomes
`15 19 * * *`, around the first Sunday of October and April each year).
