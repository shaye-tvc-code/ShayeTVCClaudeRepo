# Inbox Cleanup Rules

Append-only ledger of rules applied by the daily inbox cleanup routine (see
[`docs/inbox-cleanup-routine.md`](../docs/inbox-cleanup-routine.md) for how
these are applied, the ambiguity/question protocol, and safety notes). This
file holds the *rules themselves* — kept separate from the routine doc
because the rule list is expected to grow over time.

**To add a rule**: tell Claude the new rule in plain language in a Claude
Code session. Claude tests it against the live inbox first, adds a new
numbered entry below with the exact sender/subject/condition/action and the
validation result, then commits. Don't edit a validated rule's logic
without re-testing — append a dated exception instead (see Rule 1 below for
an example of that).

---

## Rule 1 — Cin7 Core ("sin7cor") Xero autosync reports, completed with matching counts

- **Added**: 2026-08-25
- **Sender**: `noreply@post.dearsystems.com` (Cin7 Core, formerly DEAR
  Systems / DEAR Inventory — Shaye refers to this as "sin7cor")
- **Recipient**: `finance@vergecollective.com.au` (an alias delivering into
  `shaye@vergecollective.com.au`'s inbox — search the one mailbox)
- **Subject contains**: "Autosync daily report" (the sender's actual
  wording; also matches loosely-specified "Auto Sync Daily Report")
- **Condition**: body starts with "Xero auto-sync has been completed" AND
  states "Total records processed: N" and "Total successfully processed
  records: M" where N equals M
- **Action**: delete (move to Trash)
- **Exception** (added 2026-08-25, after testing against real emails): if
  the body instead says the sync **failed** ("Xero auto-sync has failed due
  to the following errors...") — even when the record counts turn out
  equal — do **not** delete it. A failed sync can still report equal
  counts (e.g. every record was attempted and technically "processed" but
  flagged with supplier-matching or invoice-export errors), so the "failed"
  wording always overrides the count-equality check.
- **Also covered**: the same sender sometimes uses a different subject,
  "Synchronisation report — ...", specifically for failure notifications.
  These always describe a failure and are never deleted (covered by the
  exception above; they also don't match this rule's subject filter in the
  first place).
- **Validated**: 2026-08-25 manual sweep of the live inbox — 6 "Autosync
  daily report" emails found: 4 completed with matching counts (deleted:
  58/58, 1/1, 9/9, 6/6), 1 completed with mismatched counts 48≠40 (kept —
  condition not met), 1 that said "failed" despite 80/80 counts (kept —
  exception applies). Also found 2 "Synchronisation report" emails (one
  thread with 4 messages), all describing failures — all kept untouched.

## Rule 2 — Asana task update notifications

- **Added**: 2026-08-25
- **Sender**: `no-reply@asana.com`
- **Scope**: any notification email from this address about task/project
  activity — due-date digests ("Monday/Tuesday/... tasks due soon"),
  comments, task/project invites, invite-accepted notices, deleted-project
  confirmations, "you have unread notifications" digests, etc.
- **Action**: delete (move to Trash), no body condition — delete all of
  them
- **Explicitly NOT covered** by this rule (different senders — left alone
  until a rule says otherwise):
  - `customer-service@asana.com` — billing/payment receipts (financial
    record, e.g. "Asana payment confirmation")
  - `learn@go.asana.com` — Asana's own marketing/onboarding emails
- **Validated**: 2026-08-25 manual sweep — 11 matching emails found and
  deleted; 1 payment confirmation and 2 marketing emails from the other
  Asana-affiliated senders above were left untouched.

## Rule 3 — Ooze Studios invoices, forward to Dext

- **Added**: 2026-08-27
- **Subject contains**: "from Ooze Studios Pty Ltd for The Verge Collective
  Pty Ltd" (Xero "invoice is due" notifications; actual sender is
  `messaging-service@post.xero.com`, to `shaye@vergecollective.com.au`)
- **Condition**: none — every matching email qualifies
- **Action**:
  1. Forward the email to `tvcollective@dext.cc` (Superhuman Mail
     `create_or_update_draft` with `type: "forward"`, a short body like
     "Please process for bookkeeping.", then `send_draft`)
  2. Archive it (`update_thread` with `mark_done: true`) — not Trash; this
     is a real financial record Shaye wants kept, just out of the inbox
- **Validated**: 2026-08-27 — found 6 matching emails total; 5 had already
  been manually forwarded to `tvcollective@dext.cc` by Shaye (nothing to
  do — don't re-forward something already forwarded). The 1 unforwarded
  one ("August Invoice 2598", thread `1a03946c9c1d17a7`) was forwarded and
  archived live as the test case — confirmed both actions completed
  correctly.
- **Idempotency note**: before forwarding, check whether the thread
  already contains an outbound message to `tvcollective@dext.cc` (Shaye
  forwards some of these manually before the daily sweep runs) — if so,
  just archive it without forwarding again.

## Rule 4 — PR mailbox inbound: forward to Joseph + Asana subtask

- **Added**: 2026-08-27
- **Trigger**: any email received at `pr@stackersaustralia.com.au`
- **Exception** (confirmed with Shaye 2026-08-27): skip entirely — do not
  forward, subtask, or archive — if Shaye has **already personally
  replied** to the sender herself (a message in the thread sent by her
  directly to the original sender's address, not just a forward to
  Joseph). Real examples that must be left alone: "A little 21st birthday
  wish" and "Travel jewellery boxes" — both personal-favor/wholesale
  threads she chose to handle herself rather than delegate. This is a
  meaningful exception, not an edge case to relax later — the whole point
  of pr@ is that most inbound mail is standard creator/influencer pitches
  she wants routed straight to Joseph, but not everything that lands there
  is one of those.
- **Condition**: none beyond the exception above — every other email
  qualifies, regardless of subject wording (pitches arrive under wildly
  different subjects: "Creator Collaboration – X", "PR & TikTok
  collaboration opportunity", blank subjects, etc.)
- **Action**:
  1. Forward the email to `joseph@stackersau.com.au`
  2. Add a new subtask, named with the **sender's email address**, under
     the open task "Review inbound influencer requests, evaluate, respond"
     (gid `1217860849501378`) in the "2. Marketing" Asana project (gid
     `1217854649159573`). Look the parent task up by name within that
     project first (`search_tasks`) rather than trusting the gid blindly,
     in case it's moved or been recreated; only fall back to the gid above
     if the by-name lookup fails.
  3. Archive the email (`update_thread` with `mark_done: true`)
- **Idempotency note**: same as Rule 3 — if the thread already contains an
  outbound forward to `joseph@stackersau.com.au`, it's already been
  handled (manually or by a prior run); just archive it without forwarding
  or creating a duplicate subtask.
- **Validated**: 2026-08-27 — found ~20 inbound pr@ emails. All but 2 had
  already been manually forwarded to `joseph@stackersau.com.au` by Shaye
  (already archived, nothing to do). The remaining 2 were the exception
  cases above (personal replies, correctly left untouched). No untouched,
  unhandled example existed at test time to exercise the forward+subtask
  path live — the Asana project and parent task were confirmed to exist
  and be open via `search_tasks`, but the first real live run of this
  rule's forward+subtask action is still pending. Keep an eye on the
  first few days this rule actually fires.
