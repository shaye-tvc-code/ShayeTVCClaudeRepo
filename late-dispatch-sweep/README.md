# Late dispatch sweep (Stackers Australia)

A Claude Code **routine** that finds online orders that missed the Shipping policy promise (dispatch within 2 business days, Melbourne time, holidays from the sheets) and records them in:

| Sheet | Tab | What goes in |
|---|---|---|
| Order Fault Log | Late dispatch | Late orders from every 3PL |
| BTK Service Register | Late dispatch | BTK orders only (shared with BTK). The Register tab is never touched |

It uses your **Shopify** and **Google Sheets** connectors. No service account, API token or secret is needed.

## How it works

- `ROUTINE_PROMPT.md`: the routine's instructions, plus the setup section (schedule, connectors, rollout).
- `sweep.py`: an offline helper (Python standard library only). The routine saves what it reads from Shopify and the sheets into `work/`, and the helper:
  - does the business-day maths (`WORKDAY`/`NETWORKDAYS`, 3PL cut-offs)
  - applies the upsert rules
  - writes the exact sheet edits, each sheet's edits as one all-or-nothing batch, plus an undo batch
  - checks the read-back for formula errors and the BTK B4:B6 counts
  - writes the run log
- `config.json`:
  - `mode`: `dry_run` or `live`
  - the location → 3PL map, cut-offs, sheet IDs, schedule anchor
- `tests/test_sweep.py`: rule tests (`python3 -m pytest late-dispatch-sweep/tests`).
- Logs: `logs/YYYY-MM-DD.txt`, pushed to the `claude/late-dispatch-logs` branch after each run.

## Part-dispatched orders

An order that has had one parcel sent but still has items to come counts as **not yet dispatched** (the customer is still waiting), against the warehouse that holds the rest. When the last parcel goes, an open row gets the last parcel's date. Orders with nothing left to ship keep the original rule: the first parcel's date counts.

## Rules it never breaks

- The BTK Service Register (shared with BTK) never mentions Precision, New3PL or any other 3PL. Precision orders never go in it, it has no 3PL column, and the helper stops the run if a planned write, or the sheet itself, contains a term from `btk_register.never_mention` in `config.json`.
- It never deletes rows. A cancelled order gets "Cancelled in Shopify [date]" in the Fault Log notes, only if they're blank.
- It never changes Notes / follow-up (Fault Log L), BTK comment (BTK J), or a Status a person set to Customer contacted, Raised with 3PL or Closed.
- It writes dates as real dates and the order time as a real time, copying the format and dropdown of the row above.

## Try the helper by hand

With `work/` filled by a routine run (or by you):
```bash
python3 late-dispatch-sweep/sweep.py should-run
python3 late-dispatch-sweep/sweep.py plan                    # prints the plan; never writes to Google
python3 late-dispatch-sweep/sweep.py plan --today 2026-11-10 # "what would that day's run do"
```

## Not in scope yet

Precision's own SLA as a separate late test. There's a `TODO(precision-sla)` in `evaluate()`. The cut-off columns already show how much of each delay sits with the 3PL.
