# Late dispatch sweep (Stackers Australia)

Finds online orders that missed the Shipping policy promise (dispatch within
2 business days, Melbourne time, VIC public holidays excluded) and records them in:

| Sheet | Tab | What goes in |
|---|---|---|
| Order Fault Log (`1B09oeIV…`) | Late dispatch | Late orders from every 3PL |
| BTK Service Register (`1Ft40Pz5…`) | Late dispatch | BTK orders only. The Register tab is never touched |

Shopify is read only. In the sheets the script only appends rows and fills
specific blank cells. It never deletes rows, never changes Notes / follow-up
(Fault Log I) or BTK comment (BTK H), and never changes a Status a person set
to Customer contacted, Raised with 3PL or Closed.

## Files

- `sweep.py`: the script (business rules are in `evaluate()` and `plan_sheet()`)
- `config.json`: sheet IDs, location → 3PL map, Shopify API version, schedule anchor
- `requirements.txt`: `google-auth`, `requests`, `tzdata`
- `tests/test_sweep.py`: rule tests, run with `pytest`
- `../.github/workflows/late-dispatch-sweep.yml`: the schedule
- `logs/YYYY-MM-DD.txt`: run log written by each real run (not committed; on
  GitHub Actions it's kept as a run artifact for 90 days)

## Run it by hand

```bash
cd late-dispatch-sweep
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export SHOPIFY_ADMIN_TOKEN=...                     # never commit this
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
.venv/bin/python sweep.py --whoami                 # service account email to share the sheets with
.venv/bin/python sweep.py --dry-run                # prints the plan, writes nothing
.venv/bin/python sweep.py                          # real run: write, verify, log
```

## Safety

- All reading and planning happens before the first write. Each spreadsheet is
  updated in one atomic `batchUpdate`.
- Before writing, the script stops if a sheet already shows `#REF!`, `#VALUE!`,
  `#NAME?` or `#N/A`, if the BTK counts in B4:B6 don't match the rows, or if an
  order number appears twice.
- After writing, it reads both tabs back and repeats those checks. If anything
  fails, it undoes this run's changes (restores the old cell values and clears
  the rows it added), writes the error to the log and exits with code 1.
- The Shopify token is read from the environment only. It is never printed or written to a file.

## Schedule and pausing

GitHub Actions runs `.github/workflows/late-dispatch-sweep.yml` (from the
default branch) every second day at 07:00 Melbourne time. Daylight saving is
handled; see the comment at the top of the workflow.

Repository settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Secret | `SHOPIFY_ADMIN_TOKEN` | Custom app Admin API token (`read_orders`, `read_fulfillments`, `read_locations`) |
| Secret | `GOOGLE_SERVICE_ACCOUNT_JSON` | The whole service account key JSON |
| Variable | `LATE_DISPATCH_MODE` | `dry-run` (the default if unset), `live`, or `off` |

- **Pause:** set `LATE_DISPATCH_MODE` to `off`, or go to Actions → Late dispatch
  sweep → ⋯ → Disable workflow.
- **Run now:** Actions → Late dispatch sweep → Run workflow, then pick dry-run or live.

## Not in scope yet

Precision's own SLA (orders received by 06:00 dispatched the same business day).
There's a `TODO(precision-sla)` in `evaluate()` where that check would go.
