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
inbox (including mail addressed to aliases like `finance@vergecollective.com.au`
and `pr@stackersaustralia.com.au`, which deliver into the same mailbox) and
applies every rule recorded in `config/inbox-cleanup-rules.md`. Only mail
that clearly matches a rule's sender/subject/body condition is touched.
Anything a rule can't confidently decide is left alone and flagged to Shaye
by email instead of guessed at.

Rules aren't limited to deleting mail — a rule's action can be any
combination of: delete (Trash), archive, forward to another address, and/or
create an Asana subtask. See `config/inbox-cleanup-rules.md` for what each
current rule actually does.

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

**Forwarding**: Superhuman Mail `create_or_update_draft` with
`type: "forward"`, `thread_id` set to the source thread, and a short
`body` (the server appends the original message automatically — see that
tool's own description). Then `send_draft` with the returned `draft_id` to
actually send it — creating the draft alone does nothing.

**Archiving**: Superhuman Mail `update_thread` with `mark_done: true`.
This is distinct from deleting — archived mail stays fully intact and
searchable, just out of the inbox view. Use archive (never Trash) for
rules whose whole point is routing/filing real mail, not disposing of it.

**Asana**: used only by rules that say so (currently Rule 4). Look up the
target parent task by name within its project (`search_tasks`, filtered
to the project's gid) rather than trusting a hardcoded gid — projects and
tasks can be renamed or recreated. Create the subtask with `create_tasks`,
setting `parent` to the looked-up task's gid. The Asana connector must be
enabled on whatever trigger/session runs this routine — if the connector
or its tools are unavailable, treat that the same as any other tool
failure under Failure handling (below): don't skip the email silently,
email Shaye a heads-up.

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
3. **Where a thread satisfies a rule's condition**: perform that rule's
   action(s) in full — e.g. forward-then-archive, or forward-then-subtask-
   then-archive; a rule isn't done until every action it specifies has
   happened, not just the first one. Before forwarding, check whether the
   thread already contains an outbound message to the rule's forward
   target (Shaye sometimes handles one manually before the sweep runs) —
   if so, skip the forward (and any subtask creation) and just apply the
   remaining action(s), e.g. archive.
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

## Failure handling

This is an unattended run — nobody is watching the session output, so a
tool failure that just stops the run silently is invisible to Shaye. If a
rule's action can't be completed for any reason (a required connector not
authorized/expired, a tool still unavailable after a reasonable retry, the
Asana project/task not found, the run interrupted mid-way), do not skip it
silently and do not guess. Send a short failure-notification email to
`shaye@vergecollective.com.au` (Superhuman Mail primary, Gmail draft
fallback), subject `⚠️ Inbox Cleanup – run failed ([date])`, explaining
what was attempted and what specifically failed, then stop that rule (other
rules that aren't affected by the same failure can still proceed). Don't
send a failure email for something that resolved on its own after a normal
retry within the same run.

## Safety

- "Delete" always means moving to **Trash** (`trash_thread`), never a
  permanent/unrecoverable delete. Trash is recoverable, which matters
  since this runs unattended and Shaye isn't watching in real time.
  "Archive" is a separate, even gentler action (below) — never conflate
  the two.
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
- Forwarding is one-way and can't be unsent after the undo window — double
  check the forward target matches the rule exactly before sending, and
  always check for an existing outbound forward in the thread first (see
  Process step 3) so a rule never double-forwards or double-creates a
  subtask for the same email.
- A rule's documented exception (e.g. Rule 4's "skip if Shaye already
  personally replied") is exactly as binding as its main condition — check
  it every time, not just when it seems likely to apply.

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

Rules 3 and 4 were added and tested 2026-08-27, this time against the live
trigger's mailbox rather than a pre-trigger manual sweep — see their
"Validated" notes in `config/inbox-cleanup-rules.md`. Rule 3's
forward-then-archive action was exercised live end-to-end successfully.
Rule 4's forward+subtask+archive action has not yet had a live untouched
email to run against (Shaye had already manually handled or personally
replied to everything currently in the pr@ mailbox) — its Asana target
was confirmed to exist, but keep an eye on its first few real firings.
