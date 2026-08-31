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
  - `learn@go.asana.com` — Asana's own marketing/onboarding emails. Note:
    Rule 7 below separately trashes genuinely-promotional Asana mail found
    in the "Other" split — that's a distinct rule with its own scope
    (marketing content generally, not this rule's task-update scope), not
    a relaxation of this exclusion.
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
- **Trigger**: any email received at `pr@stackersaustralia.com.au`, where
  `pr@stackersaustralia.com.au` is the **primary "To:" recipient** (see
  scope clarification below).
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
- **Scope clarification** (confirmed with Shaye 2026-08-29): "any email
  received at pr@stackersaustralia.com.au" means pr@ must be the primary
  **To:** recipient — not merely cc'd. An email where pr@ is only cc'd
  (someone else is the primary "to") does not trigger Rule 4 at all,
  regardless of subject or sender.
  - Example that clarified this: "Web + Desktop License – TT Norms(R) Pro
    Basic Package has been successfully verified and activated." from
    `grigorian@typetype.org`, to `shaye@stackersau.com.au`, cc
    `pr@stackersaustralia.com.au, info@stackersaustralia.com.au` (thread
    `19e96dae1115afb1`, 2026-06-05) — a closing notice in a font-licensing
    dispute Shaye handled personally in two related threads with the same
    sender. Since pr@ was only cc'd here, Rule 4 doesn't apply to this
    thread in the first place; Shaye also confirmed it would separately
    fall under the "already personally replied" exception since it's part
    of a saga she handled directly. Left untouched, no forward/subtask/
    archive.
- **Condition**: none beyond the exception and scope clarification above —
  every other email qualifies, regardless of subject wording (pitches arrive
  under wildly different subjects: "Creator Collaboration – X", "PR & TikTok
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
  cases above (personal replies, correctly left untouched).
  Forward+subtask+archive was then exercised live end-to-end against a
  genuinely untouched email ("Home organisation collaboration x Codie
  Ryan" from `codieryanugc@gmail.com`, thread `1a042818c113ff1b`): forwarded
  to `joseph@stackersau.com.au`, subtask `codieryanugc@gmail.com` created
  under the parent task (gid `1217933138434286`), thread archived — all
  three actions confirmed successful.

## Rule 5 — "Forward Bills to Dext": Meta ads receipts

- **Added**: 2026-08-30
- **Subject contains**: "Your Meta ads receipt" (sender
  `noreply@business-updates.facebook.com`, to `shaye@vergecollective.com.au`)
- **Condition**: none — every matching email qualifies
- **Action**:
  1. Forward the email to `tvcollective@dext.cc`
  2. Mark as done (archive — `update_thread` with `mark_done: true`), not
     Trash — same reasoning as Rule 3, this is a real financial record
- **Idempotency note**: same pattern as Rule 3 — check for an existing
  outbound forward to `tvcollective@dext.cc` in the thread first; if
  already forwarded (Shaye does some of these manually), just archive.
- **Validated**: 2026-08-30 — found 9 matching emails; 8 already manually
  forwarded (one, oddly, to `alwaysforimpact@dext.cc` instead of
  `tvcollective@dext.cc` — a one-off historical exception, not a pattern
  to match on). The 1 unforwarded one (thread `1a0477a2e52ef788`) was
  forwarded to `tvcollective@dext.cc` and archived live — confirmed both
  actions completed correctly.

## Rule 6 — LinkedIn notifications, trash

- **Added**: 2026-08-30
- **Sender**: LinkedIn's notification addresses (`notifications-noreply@linkedin.com`,
  `messages-noreply@linkedin.com`, and other `@linkedin.com` senders)
- **Scope**: threads labeled `CATEGORY_SOCIAL` (Superhuman also tags these
  `[Superhuman]/AI/Social`, referred to as "the Social label") — new
  message alerts, profile-view notices, connection suggestions, "popular
  in your network" recommendations, profile-completion nudges, etc.
- **Action**: delete (move to Trash) — no body condition, delete all of
  them
- **Validated**: 2026-08-30 — found 8 matching threads across the mailbox
  (5 currently sitting in the "Other" split, 3 older ones already out of
  the inbox), all genuine LinkedIn network-activity notifications with no
  real correspondence content — all 8 deleted.

## Rule 7 — Named-sender marketing mail in "Other" split, trash

- **Added**: 2026-08-30
- **Scope**: mail sitting in Superhuman's **"Other" split** (the
  lower-priority/newsletter split, distinct from the main Inbox split)
  from any of these senders: Asana (any address, not just
  `no-reply@asana.com` — e.g. `learn@email1.asana.com`), Qantas Business
  Rewards (`qantasbusinessrewards@loyalty.qantas.com`), Claude Team
  (`no-reply@email.claude.com`), Calendly (`teamcalendly@send.calendly.com`
  and similar), Edible Blooms (`@edibleblooms.co.nz` / `@edibleblooms.com.au`)
- **Condition**: the email must **genuinely look like a promotional-only
  email** — marketing nudges, feature announcements, tips/lifecycle
  emails, seasonal campaigns. This is a judgment call, not a mechanical
  check: read the actual subject/snippet, don't pattern-match on sender
  alone. If a specific email from one of these senders is transactional,
  account-critical, or otherwise not clearly "just marketing" — do not
  trash it under this rule.
- **Action**: delete (move to Trash)
- **If unsure** whether a specific email is genuinely promotional: do not
  guess and do not trash it. Include it in that day's ambiguity/question
  email (see the routine doc's "Handling ambiguity" protocol) so Shaye can
  decide, same as any other uncertain case.
- **Validated**: 2026-08-30 — found 15 threads total in the "Other" split;
  after excluding the 5 LinkedIn ones (Rule 6) and the 1 Meta ads receipt
  (Rule 5), 7 matched this rule and were all judged clearly promotional
  (no ambiguous cases on this pass): "Find your files..." (Asana learn
  email), "Reward your team" (Qantas Business Rewards double-points
  promo), "Two new ways to browse the web in Cowork..." (Claude Team
  product update/marketing), 3 Calendly lifecycle-tip emails, and "Still
  need to find his Father's Day gift?" (Edible Blooms). All 7 deleted.

## Rule 8 — Un-spam misfiled influencer inbound to pr@

- **Added**: 2026-08-30
- **Trigger**: check the Spam folder for mail addressed to
  `pr@stackersaustralia.com.au` that looks like a genuine inbound
  influencer/creator pitch (the same kind of content Rule 4 handles) that
  has been misfiled as spam.
- **Action**: mark it "not spam" (restoring it to the inbox), then let
  Rule 4 process it normally in the same run — this rule only exists to
  un-block Rule 4 for mail the spam filter wrongly intercepted; it doesn't
  duplicate Rule 4's own forward/subtask/archive logic.
- **Note**: don't un-spam indiscriminately — only mail that genuinely
  looks like an inbound creator/influencer pitch to pr@. Real spam stays
  in spam.
- **Validated**: 2026-08-30 — checked Spam for `pr@stackersaustralia.com.au`
  mail (both via Gmail's `in:spam` search and Superhuman's `SPAM` label
  filter); none found at test time, so this rule has not yet had a live
  example to exercise. Keep an eye on its first real match.

## Rule 9 — Flag action-needed mail with the "Shaye" label (last step of the sweep)

- **Added**: 2026-08-30
- **Runs last**, after every other rule in this file — it only looks at
  whatever's actually left in the Inbox once Rules 1–8 have already
  deleted/archived/forwarded what they cover. This rule never deletes,
  archives, or moves anything — it only adds a label, so it's low-risk by
  nature.
- **Label**: `Shaye` — this label already existed before this rule (Shaye
  uses it manually to track her own action items; there's also a
  `Shaye/In Motion` sub-label for things actively in progress, which this
  rule does not set — only the top-level `Shaye` label).
- **Scope**: threads currently in the Inbox where the **most recent
  message was not sent by Shaye** (`shaye@vergecollective.com.au` /
  `shaye@stackersau.com.au`) and reading that message shows it's
  genuinely awaiting **her personal** reply, decision, or input.
- **Condition** — read the actual last message, don't pattern-match on
  labels alone. Superhuman's own `[Superhuman]/AI/Respond` label is a
  useful signal to prioritize candidates, but it is not sufficient by
  itself — plenty of `AI/Respond`-labeled threads don't qualify (see
  exclusions below), and it's not necessary either (a thread can qualify
  without it).
  - **Include**: someone is asking her a direct question, requesting a
    decision or approval only she can give, or needs specific information
    only she has (legal/company details, a strategic call, a yes/no on a
    proposal), and no one has answered it yet.
  - **Exclude — someone else owns the next step**: she's only cc'd for
    visibility while a teammate (typically Joseph) is the one actually
    expected to act or reply. Being cc'd on an active thread does not by
    itself mean she needs to act.
  - **Exclude — already resolved**: the last message confirms/closes
    something rather than asking something (e.g. "yes, done", "added as
    requested") — nothing outstanding for her to do.
  - **Exclude — automated, not a person asking something**: read
    receipts/"has read your message" notifications, Google Chat
    "messaged you while away" or "mentioned you in a space" alerts,
    calendar accept/decline notices, moderator/spam-digest emails,
    subscription/payment confirmation notices, and similar automated
    mail. These aren't a person waiting on her.
  - **Exclude**: anything already carrying the `Shaye` label (idempotent —
    don't reprocess or duplicate).
  - **If genuinely unsure**: default to **not** labeling. Unlike the
    deletion rules, this one doesn't need to escalate uncertain cases to
    the daily question email — mislabeling is low-stakes and she can
    always remove the label — but keep the label meaningful by being
    conservative rather than over-applying it.
- **Validated**: 2026-08-30 — reviewed the current Inbox (~50 threads) and
  applied `Shaye` to 4 genuine matches:
  - "Re: [#7788519] Re: WrkPod New Concierge Client" (thread
    `1a0330400e975af1`) — recruiter laid out a sourcing strategy and asked
    Shaye to confirm it before proceeding
  - "Need a few details to resubmit the Cloudflare report on the fake
    Stackers site" (thread `1a03b7859ce3ac8a`) — Joseph needs specific
    company/legal details only Shaye can provide
  - "Re: Document shared with you: ...LC Design Strateigc Sync..." (thread
    `19eb74fde9c80795`) — Joseph asked Shaye for product dimensions
  - "Point of Contact?" (thread `1a03c3f1a5ba7488`) — Belinda (BTK) asked
    Shaye a direct question about Shopify visibility and rates
  
  Correctly left unlabeled, confirming the exclusion boundaries: "Celebrating
  25 Years — Stackers Travel Jewellery Box" and "Flag — Mispacked Order
  #4217" (both cc-only to Shaye, Joseph owns the next step), "[Stackers
  Finance] Pallas - Agreement Cancellation" (automated confirmation of an
  already-completed cancellation), "[Stackers Orders] Moderator's spam
  report" (auto-discards on its own, and the held message was itself
  spam), and "Set 2s" (Shaye's original question was already answered,
  nothing outstanding).
