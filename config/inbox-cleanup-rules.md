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
