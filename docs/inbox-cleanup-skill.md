# Daily Inbox Cleanup — Skill

Self-contained instructions for a scheduled Claude trigger that sweeps
`shaye@vergecollective.com.au`'s inbox (including mail addressed to aliases
like `finance@vergecollective.com.au` and `pr@stackersaustralia.com.au`,
which deliver into the same mailbox) and applies the rules below. Only mail
that clearly matches a rule's condition is touched; anything a rule can't
confidently decide is left alone and flagged by email instead of guessed
at.

This file is a clean, current-state consolidation of
`docs/inbox-cleanup-routine.md` + `config/inbox-cleanup-rules.md` — same
behavior, without the dated change-log/validation narrative of those two.
Use it as the single prompt/source for a new Routine. Those two files
remain the historical record for the original "Daily Inbox sweep" Routine;
update whichever file(s) your actual trigger points to when a rule changes.

## Schedule

Three times daily. Pick times in the mailbox owner's local timezone and
convert to UTC for the cron expression — the scheduler only supports a
fixed UTC offset, not an IANA timezone, so the offset must be manually
shifted by hand each time daylight saving starts/ends in that timezone.

## Tools

- **Superhuman Mail** (`list_threads` / `get_thread` / `trash_thread` /
  `update_thread` / `create_or_update_draft` / `send_draft`) — primary tool
  for search, inspection, deletion, archiving, forwarding, and labeling.
  Fall back to the **Gmail** connector only if Superhuman Mail's tools are
  unavailable.
- **Gmail** — label management (`list_labels` / `create_label` /
  `label_thread`) for the ambiguity protocol, and Spam (`search_threads`
  with `in:spam`, `unmark_thread_spam`).
- **Superhuman splits**: `list_threads` with the `split` parameter scopes a
  search to a saved view (e.g. "Other") — not the same thing as a label.
  Use `list_splits` to confirm a split's exact name first if unsure.
- **Forwarding**: `create_or_update_draft` with `type: "forward"`,
  `thread_id` set to the source thread, a short `body` (the original
  message is appended automatically). Then `send_draft` with the returned
  `draft_id` — creating the draft alone sends nothing.
- **Archiving**: `update_thread` with `mark_done: true`. Distinct from
  deleting — archived mail stays intact and searchable, just out of the
  inbox view. Use archive (never Trash) for rules whose point is
  routing/filing real mail, not disposing of it.
- **Deleting**: `trash_thread` — always Trash, never a permanent delete.
  Trash is recoverable, which matters since this runs unattended.
- **Labeling**: `update_thread` with `add_labels: [...]` (needs the
  thread's `last_message_id`).
- **Asana** (`search_tasks` / `create_tasks`): used only by rules that say
  so. Look up a target parent task by name within its project rather than
  trusting a hardcoded gid — projects/tasks can be renamed or recreated;
  only fall back to a hardcoded gid if the by-name lookup fails.

## Process, each run

1. **Check for pending clarification replies first** (see "Handling
   ambiguity" below) — resolve any answered questions before sweeping, so
   a reply that arrived since the last run is acted on this run.
2. **For each rule below** (everything except the final labeling rule):
   search the inbox (or the relevant split/folder, per that rule) for
   matching threads, then fetch each match's full body to evaluate the
   rule's actual condition — never decide from a snippet alone. Some rules
   depend on another rule's output within the same run (e.g. an un-spam
   rule feeding a forwarding rule) — run any such prerequisite rule first,
   not in a fixed order.
3. **Where a thread satisfies a rule's condition**: perform that rule's
   action(s) in full — a rule isn't done until every action it specifies
   has happened (e.g. forward-then-archive, or forward-then-subtask-then-
   archive), not just the first one. Before forwarding, check whether the
   thread already contains an outbound message to the rule's forward
   target (the mailbox owner sometimes handles one manually before the
   sweep runs) — if so, skip the forward (and any subtask creation) and
   just apply the remaining action(s).
4. **Where a thread matches a rule's sender/subject but the condition is
   unclear** (expected fields missing, wording doesn't match the covered
   case or a documented exception, format looks different than described)
   — do **not** act on it. Flag it for a clarification email instead
   (below).
5. **Never touch anything not described by a rule below.** This routine
   only acts on what's explicitly covered — it does not infer new rules,
   extend a rule's scope, or act on "similar-looking" mail on its own
   initiative. That's a human decision, made by adding a new rule here.
6. **Last step, always**: apply the action-needed label rule (last rule
   below) over whatever's left in the Inbox after every other rule has
   run. This step only adds a label — never deletes/archives/forwards/
   moves anything — so it's safe even if an earlier rule hit a failure.

## Rules

### 1. Xero autosync reports (Cin7 Core)

- **Sender**: `noreply@post.dearsystems.com`, **to** `finance@vergecollective.com.au`
- **Subject contains**: "Autosync daily report" (also matches "Auto Sync
  Daily Report")
- **Delete** (Trash) if either:
  - body starts "Xero auto-sync has been completed" AND "Total records
    processed: N" equals "Total successfully processed records: M", or
  - body reads "Xero auto-sync has been completed. No records processed."
    (no counts at all, and not "failed")
- **Never delete** if the body says the sync **failed**, even if the
  counts happen to be equal — "failed" always overrides the count check.
- The same sender also uses a "Synchronisation report — ..." subject for
  failure notifications — these don't match this rule's subject filter
  and are never deleted.

### 2. Asana task update notifications

- **Sender**: `no-reply@asana.com` → delete (Trash) all task/project
  activity notifications, no condition.
- **Not covered** (left alone): `customer-service@asana.com` (billing
  receipts), `learn@go.asana.com` (Asana's own marketing — see Rule 7 for
  marketing mail generally).

### 3. Xero vendor invoices for The Verge Collective → Dext

- **Sender**: `messaging-service@post.xero.com`
- **Subject contains**: "for THE VERGE COLLECTIVE PTY LTD" (case-
  insensitive — vendor name varies, e.g. "Invoice INV-1234 from Ooze
  Studios Pty Ltd for THE VERGE COLLECTIVE PTY LTD"; match on the "for..."
  part, not the vendor), or ends "...for The Verge Collective for AUD N"
  (remittance-advice subject variant without "Pty Ltd").
- **Default action** (a "you have a new bill" invoice notification):
  forward to `tvcollective@dext.cc` (skip if the thread already has an
  outbound forward there), then archive (never Trash — real financial
  record).
- **Exception — remittance advice**: if the body instead reads "Here's
  your remittance advice for payment of AUD ..." (confirming a payment
  *already made by* a customer/debtor, not a bill VCC owes), forward to
  `tracey@jog.com.au` instead of Dext, then archive (same idempotency
  check).

### 4. PR mailbox inbound → forward to Joseph + Asana subtask

- **Trigger**: `pr@stackersaustralia.com.au` is the primary **To:**
  recipient, or is in **Bcc** on a message addressed To: the sender's own
  address. Being only **cc'd** does not trigger this rule.
- **Exception**: skip entirely (no forward, subtask, or archive) if the
  mailbox owner has already personally replied to the sender herself from
  her own address — not just forwarded to Joseph.
- **Action**:
  1. Forward to `joseph@stackersau.com.au` (skip if already forwarded)
  2. Add a subtask named with the sender's email address under the open
     task "Review inbound influencer requests, evaluate, respond" in the
     "2. Marketing" Asana project (look up both by name first)
  3. Archive (never Trash)

### 5. Meta ads receipts → Dext

- **Sender**: `noreply@business-updates.facebook.com`
- **Subject contains**: "Your Meta ads receipt"
- **Action**: forward to `tvcollective@dext.cc` (skip if already
  forwarded), then archive.

### 6. LinkedIn notifications → trash

- **Sender**: `notifications-noreply@linkedin.com`, `messages-noreply@linkedin.com`,
  or other `@linkedin.com` senders, labeled `CATEGORY_SOCIAL`
- **Action**: delete (Trash), no condition.

### 7. Named-sender marketing mail in the "Other" split → trash

- **Scope**: Superhuman's "Other" split, from Asana (any address), Qantas
  Business Rewards (`qantasbusinessrewards@loyalty.qantas.com`), Claude
  Team (`no-reply@email.claude.com`), Calendly
  (`teamcalendly@send.calendly.com` and similar), Edible Blooms
  (`@edibleblooms.co.nz` / `@edibleblooms.com.au`)
- **Condition**: must genuinely look promotional-only (marketing nudges,
  feature announcements, lifecycle tips, seasonal campaigns) — a judgment
  call based on actual content, not sender alone. Not clearly "just
  marketing" → don't trash it.
- **Action**: delete (Trash)
- **If unsure**: don't guess — include it in the day's ambiguity/question
  email instead.

### 8. Un-spam misfiled pr@ inbound

- **Trigger**: mail in Spam addressed to `pr@stackersaustralia.com.au`
  that looks like a genuine inbound influencer/creator pitch.
- **Action**: mark "not spam" (restores to inbox), then let Rule 4 process
  it normally in the same run. Don't un-spam indiscriminately — real spam
  stays in spam.

### 9. Flag action-needed mail with the owner's personal label (runs LAST)

- Runs after every other rule — only looks at whatever's left in the
  Inbox. Never deletes, archives, forwards, or moves anything — only adds
  a label.
- **Label**: `Shaye` (an existing label the mailbox owner uses manually;
  there's also a `Shaye/In Motion` sub-label this rule does not set).
- **Scope**: Inbox threads where the most recent message was **not** sent
  by the mailbox owner, and reading it shows it's genuinely awaiting her
  **personal** reply, decision, or input.
- Read the actual last message — don't pattern-match on labels alone.
  Superhuman's `[Superhuman]/AI/Respond` label is a useful signal, not
  sufficient or necessary on its own.
  - **Include**: someone asks her a direct question, requests a
    decision/approval only she can give, or needs information only she
    has — and no one has answered it yet.
  - **Exclude**: she's only cc'd while a teammate owns the next step;
    the thread is already resolved (last message confirms/closes
    something); automated mail (read receipts, chat "while away" alerts,
    calendar notices, moderator/spam digests, payment confirmations);
    anything already labeled `Shaye`.
  - **If genuinely unsure**: default to **not** labeling — low-stakes,
    reversible, no need to escalate to the question email for this rule.

### 10. Archive previous days' automated report emails (adapt per routine)

- **Trigger**: threads whose subject starts with a known automated-report
  prefix for this mailbox (e.g. "⭐ Stackers eComm Sales run rate") sent by
  another scheduled routine on this account.
- **Condition**: the thread is from an earlier calendar day than today
  **and** has already been read (`is_unread` is false) — check read status
  before archiving, not just the date. Never archive today's report, and
  never archive an earlier report that's still unread (leave it for the
  owner to see first; re-check it on a later run once it's been read).
- **Action**: archive (`mark_done: true`), never Trash.
- **Never apply** to that routine's own failure-notification subject
  (e.g. "⚠️ ... run failed") — those must stay visible until seen.

## Handling ambiguity — the email question protocol

An ambiguous case must never block the run, guess, or act speculatively.

1. Draft and send a plain-text email to the mailbox owner (Superhuman Mail
   `create_or_update_draft` + `send_draft`; Gmail `create_draft` as
   fallback). Subject: `[Inbox Cleanup] Question: <short description>`.
   Body: what was found, which rule it partially matched, and exactly
   what's unclear — quote the relevant email's subject/sender/date and the
   specific field or wording in question.
2. Label that question thread `InboxCleanup/AwaitingReply` (create the
   label once if it doesn't exist) so it can be found again later.
3. Leave the original ambiguous email untouched in the inbox meanwhile.
4. **On every subsequent run, before sweeping**: search for threads
   labeled `InboxCleanup/AwaitingReply` and check whether the owner has
   replied.
   - **If yes**: read the reply and act on it. If the reply describes a
     new rule going forward, don't apply it silently in the same run from
     memory — add it as a new rule in this file in a proper reviewable
     commit first, then apply it. Remove the `InboxCleanup/AwaitingReply`
     label once resolved.
   - **If no reply yet**: leave it labeled and move on — don't re-ask,
     nag, or escalate the wait.

## Failure handling

This is an unattended run — a tool failure that stops the run silently is
invisible to the owner. If a rule's action can't be completed for any
reason (connector not authorized/expired, a tool unavailable after a
reasonable retry, an Asana project/task not found, the run interrupted
mid-way): don't skip it silently and don't guess. Send a short failure-
notification email to the mailbox owner (Superhuman Mail primary, Gmail
draft fallback), subject `⚠️ Inbox Cleanup – run failed ([date])`,
explaining what was attempted and what specifically failed, then stop that
rule — other unaffected rules can still proceed. Don't send a failure
email for something that resolved on its own after a normal retry within
the same run.

## Safety

- "Delete" always means **Trash**, never a permanent/unrecoverable delete.
  "Archive" is a separate, gentler action — never conflate the two.
- Never apply a rule to a thread whose sender/subject don't match that
  rule's filters (including its exceptions) — no fuzzy/"close enough"
  matching. When in doubt, treat it as ambiguous, not a match.
- Only ever act on rules actually recorded in this file. A reply to a
  question email proposing a new rule is a request to add it, not
  authorization to start acting on it immediately before it's recorded.
- Forwarding is one-way and can't be unsent after the undo window — double
  check the forward target matches the rule exactly, and always check for
  an existing outbound forward first so a rule never double-forwards or
  double-creates a subtask for the same email.
- A rule's documented exception is exactly as binding as its main
  condition — check it every time, not just when it seems likely to apply.
- **When committing a rule change back to this file**: write it with a
  normal file-editing tool and verify the diff is plain, readable markdown
  before committing — never pipe the content through any encoding step.

## Adapting this file for a new mailbox/routine

1. Replace every mailbox address, alias, forward target, Asana
   project/task name, and label above with the new mailbox's real values.
2. Drop any rule that doesn't apply (e.g. Rules 1/3/10 are specific to
   this business's Xero/sales-report setup) and add new ones in the same
   format: trigger condition → action → exceptions.
3. Set the schedule and start with a manual sweep against the live inbox
   before turning on an unattended trigger — test every rule (including
   its exceptions) against real mail first, the same way each rule above
   was originally validated.
4. Point the new Routine's prompt at this file (read via GitHub MCP tools)
   the same way the existing "Daily Inbox sweep" Routine points at
   `docs/inbox-cleanup-routine.md` + `config/inbox-cleanup-rules.md` — or
   paste this file's content directly into the Routine's prompt if the
   trigger can't read the repo.
