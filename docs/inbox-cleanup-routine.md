# Routine: Daily Inbox Cleanup

This is the full spec executed by the scheduled Claude trigger. It is also
the source of truth for the trigger's prompt — if you change process
behavior here, update the trigger's prompt to match
(`mcp__Claude_Code_Remote__update_trigger` doesn't support editing the
prompt directly; delete and recreate the trigger, or ask Claude to do it).

The actual deletion rules are kept separately in
[`config/inbox-cleanup-rules.md`](../config/inbox-cleanup-rules.md), since
that list is expected to grow over time as Shaye adds more rules — this doc
describes the *process* (schedule, tools, ambiguity handling, safety), not
the individual rules.

## What it does

Every day, a scheduled Claude session sweeps `shaye@vergecollective.com.au`'s
inbox (including mail addressed to aliases like `finance@vergecollective.com.au`,
which deliver into the same mailbox) and applies every rule recorded in
`config/inbox-cleanup-rules.md`. Only mail that clearly matches a rule's
sender/subject/body condition is touched. Anything a rule can't confidently
decide is left alone and flagged to Shaye by email instead of guessed at.

## Trigger and schedule

Fires once daily at **6:15am Australia/Sydney time** (before the working
day starts, after the prior day's mail has fully landed) — `15 20 * * *`
in UTC, assuming AEST (UTC+10). See the DST caveat below.

## Mail tools

Use **Superhuman Mail** (`list_threads` / `get_thread` / `trash_thread`)
as the primary tool for search, inspection, and deletion — this is what
the rules above were tested against, and matches how the other routines in
this repo interact with mail. Fall back to the Gmail connector
(`search_threads` / `get_thread` / `trash_thread`) only if Superhuman
Mail's tools are unavailable in the session; adjust the query syntax
accordingly (see each tool's own docs — Superhuman's filters are
structured fields, Gmail's is a query string).

Use Gmail specifically for label management (`list_labels`,
`create_label`, `label_thread`) in the ambiguity protocol below, since
Superhuman Mail's labels and Gmail's are the same underlying mailbox
labels but Gmail's tools are more precise for creating/looking up a
specific label by ID.

## Process, each run

1. **Check for pending clarification replies first** (see "Handling
   ambiguity" below) — resolve any answered questions before sweeping, so
   a reply that arrived overnight is acted on the same day.
2. **For each rule** in `config/inbox-cleanup-rules.md`: search the inbox
   for threads matching the rule's sender/subject filters
   (`list_threads`), then fetch each match's full body (`get_thread`) to
   evaluate the rule's actual condition — never decide from a snippet
   alone, since conditions like "N equals M" need the real numbers, and
   "the body says failed" needs the real wording.
3. **Where a thread satisfies a rule's delete condition**: `trash_thread`
   it.
4. **Where a thread matches a rule's sender/subject but the body condition
   is unclear** (the expected fields are missing, the wording doesn't
   match either the covered case or a documented exception, the format
   looks different from what the rule describes) — do **not** delete it.
   Flag it for a clarification email instead (below).
5. **Never touch anything not described by an existing rule.** This
   routine only deletes what `config/inbox-cleanup-rules.md` explicitly
   covers. It does not infer new rules, extend an existing rule's scope,
   or delete "similar-looking" mail on its own initiative — that's a
   human decision, made by adding a new dated rule to the config file.

## Handling ambiguity — the email question protocol

This routine runs unattended, so an ambiguous case must never block the
run, guess, or delete speculatively. Instead:

1. Draft and send a plain-text email to `shaye@vergecollective.com.au`
   (Superhuman Mail `create_or_update_draft` + `send_draft`; fall back to
   Gmail `create_draft` + sending it if Superhuman Mail is unavailable).
   Subject: `[Inbox Cleanup] Question: <short description>`. Body:
   explain what was found, which rule it partially matched, and exactly
   what's unclear — quote the relevant email's subject/sender/date and
   the specific field or wording in question.
2. Label that question thread `InboxCleanup/AwaitingReply` (Gmail
   `create_label` once, if it doesn't already exist, then `label_thread`
   on every new question thread) so it can be found again on a later run.
3. Leave the original ambiguous email untouched (not deleted) in the
   inbox in the meantime.
4. **On every subsequent run, before sweeping** (step 1 above): search for
   threads labeled `InboxCleanup/AwaitingReply` and check whether
   `shaye@vergecollective.com.au` has replied in that thread.
   - **If yes**: read the reply and act on it — e.g. delete the held-back
     email if the answer confirms it should be, or leave it if not. If
     the reply describes a new rule to add going forward, don't add it
     directly from this trigger run — reply-confirm back to Shaye that
     you'll add it, and add it to `config/inbox-cleanup-rules.md` in a
     proper reviewable commit (this can happen later in the same
     session, since the trigger session has full repo write access — just
     don't skip recording it in the config file before relying on it in a
     future sweep). Remove the `InboxCleanup/AwaitingReply` label once
     resolved.
   - **If no reply yet**: leave the thread labeled and move on. Don't
     re-ask, don't nag, don't escalate the wait — just check again next
     run.

## Safety

- "Delete" always means moving to **Trash** (`trash_thread`), never a
  permanent/unrecoverable delete. Trash is recoverable, which matters
  since this runs unattended and Shaye isn't watching in real time.
- Never apply a rule to a thread whose sender/subject don't match that
  rule's filters (including its documented exceptions) — no fuzzy or
  "looks similar enough" matching. When in doubt, treat it as ambiguous
  (see above), not as a match.
- Only ever act on rules actually recorded in
  `config/inbox-cleanup-rules.md`. A reply to a question email that says
  "also delete X going forward" is a request to add a rule, not
  authorization to start deleting X immediately in the same run before
  it's recorded.
- `trash_thread` on an already-trashed thread is a no-op in practice (it
  simply won't be found by the next day's inbox search), so there's no
  need for extra idempotency bookkeeping beyond what's described above.

## Known limitation: daylight saving time

Same caveat as the other routines in this repo: the trigger's cron is
defined in UTC assuming AEST (UTC+10) year-round. During AEDT (UTC+11,
roughly early October–early April), the trigger will actually fire at
7:15am Sydney time instead of 6:15am until the cron offset is manually
shifted (subtract one hour from the UTC hour: `15 20 * * *` becomes
`15 19 * * *`, around the first Sunday of October and April each year).

## Validated against a real inbox sweep

Tested 2026-08-25 as a manual sweep (run interactively, not yet via the
trigger) against Shaye's live inbox before the trigger was created — see
the "Validated" notes on each rule in `config/inbox-cleanup-rules.md` for
exact counts and the specific emails that were correctly kept vs. deleted,
including the two edge cases that shaped Rule 1's "failed" exception and
Rule 2's sender-scope boundary (payment receipts and marketing mail from
Asana-affiliated senders are deliberately not touched by "delete all
Asana task updates").
