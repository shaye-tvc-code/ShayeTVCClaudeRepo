# Routine: late dispatch sweep (Stackers Australia)

## Setup (for the human creating the routine; the routine ignores this section)

- Repo: https://github.com/shaye-tvc-code/ShayeTVCClaudeRepo, files in `late-dispatch-sweep/`. They must be on the default branch, because scheduled runs check that out.
- Connectors on the routine: **Shopify** and **Google Sheets**. Remove all others. No service account, API token or secret is needed: the routine reads and writes as you, through the connectors.
- Schedule: `CRON_TZ=Australia/Melbourne 0 7 * * *` (every day at 7:00am Melbourne; daylight saving is handled). The routine skips every other day by itself (see step 1), so it does the work every second day.
- Prompt: `Read late-dispatch-sweep/ROUTINE_PROMPT.md and follow the Routine section.`
- Mode: `mode` in `late-dispatch-sweep/config.json` is `live` (writes to the sheets). Set it to `dry_run` to read and log without writing.
- Plain-English explainer for the team: the "Late Dispatch Sweep" tab of the Google Doc "Routines and Automations" (internal, never share with BTK): https://docs.google.com/document/d/1ONyBG2WuVbE0WP13OhEEGIhaj01A9Ie7CfTR_jOxaos/edit . **Whenever this routine or its config changes, update that tab in place (same link) and its "Last updated" date.**
- Pause: open the routine and turn it off (or set `mode` to `dry_run` to keep the logs without writing).

---

# Routine

Work from the repository root. `H` below means `python3 late-dispatch-sweep/sweep.py`. It is an offline helper: it never contacts Shopify or Google, so all reading and writing is done by you through the connectors, exactly as described here.

## Hard rules

- Shopify is **read only**: use only `graphql_query`. Never call a mutation.
- In Google Sheets, write only with `update_spreadsheet`, and only with the request files `H plan` produces, copied exactly. Never edit those files, never write any other cell, never touch the "Register" tab or any tab other than "Late dispatch".
- Don't print anything that looks like a token or credential.
- The BTK Service Register is shared with BTK. Never write any mention of Precision, New3PL or any other 3PL to it (the helper also refuses to plan or verify one; see `never_mention` in `config.json`). If `H plan` or `H verify` reports such a mention, stop as the step says and don't try to work around it.
- If a step fails and these instructions don't say what to do, stop, run `H finish --status failed --note "<what failed>" --push`, and end with that log.

## 1. Is today a run day?

Run `H should-run`. If it prints `SKIP`, reply "Not a run day (every second day)." and stop. Write nothing.

Read `late-dispatch-sweep/config.json` and note `mode` (`dry_run` or `live`).

Create `late-dispatch-sweep/work/` if it doesn't exist and empty it.

## 2. Read Shopify orders (last 30 days)

1. Get the start of the window: `date -u -d '30 days ago' +%Y-%m-%dT%H:%M:%SZ`.
2. Call `graphql_query` with `first: 100`, variables `{"first": 100, "q": "created_at:>='<start>'"}` and this query, and page with `after` = `endCursor` until `hasNextPage` is false:

   ```graphql
   query($first:Int!,$after:String,$q:String){ orders(first:$first, after:$after, query:$q, sortKey:CREATED_AT){ pageInfo{hasNextPage endCursor} nodes{ name createdAt cancelledAt tags displayFulfillmentStatus purchasingEntity{ __typename } fulfillments(first:5){ createdAt status location{ name } } fulfillmentOrders(first:5){ nodes{ status assignedLocation{ name } lineItems(first:30){ nodes{ remainingQuantity } } } } lineItems(first:50){ nodes{ requiresShipping } } } } }
   ```

   If Shopify rejects the query as too costly, use `first: 50` (then 25) instead of 100 and page the same way.

3. Write **every** order to `late-dispatch-sweep/work/orders.txt`, one line each, fields separated by ` | `:

   ```
   name | createdAt | cancelledAt | tags | purchasingEntity | shippable | fulfilments | assigned | outstanding
   ```

   - `name`, `createdAt`, `cancelledAt`: exactly as Shopify returns them (`cancelledAt` null → `-`).
   - `tags`: comma-separated, or `-` if none.
   - `purchasingEntity`: its `__typename` (`Customer` or `PurchasingCompany`), or `-` if null.
   - `shippable`: `Y` if any line item has `requiresShipping: true`, otherwise `N`.
   - `fulfilments`: every fulfilment as `createdAt@status@location name`, joined with `;`, or `-` if none.
   - `assigned`: the first non-empty `fulfillmentOrders.nodes[].assignedLocation.name`, or `-`.
   - `outstanding`: items the customer is still waiting for. Add up `remainingQuantity` over every fulfillment order whose `status` is `OPEN`, `IN_PROGRESS`, `SCHEDULED` or `ON_HOLD`. If the total is above 0, write `<total>@<location>`, where location is the `assignedLocation.name` of the first of those fulfillment orders with `remainingQuantity` above 0 (e.g. `1@New3PL`). Otherwise write `-`. This is how part-dispatched orders (one parcel sent, items still to come) are caught: the helper treats them as not yet dispatched.

   Examples:
   ```
   #5034 | 2026-10-31T22:38:36Z | - | - | Customer | Y | 2026-11-05T23:45:50Z@SUCCESS@BTK | BTK | -
   #5087 | 2026-11-04T08:17:07Z | - | - | Customer | Y | - | New3PL | 3@New3PL
   #5092 | 2026-11-05T07:28:49Z | 2026-11-05T07:30:55Z | - | Customer | Y | - | BTK | -
   #5079 | 2026-11-04T00:46:47Z | - | - | Customer | Y | 2026-11-06T03:19:39Z@SUCCESS@BTK | BTK | 1@BTK
   ```

   Copy values character for character; don't round or reformat timestamps. Keep a count of the orders Shopify returned; you will check it in step 5.

## 3. Read both sheets

For each sheet:

| Key | Spreadsheet ID | Range |
|---|---|---|
| `fault_log` | `1B09oeIVXPdwKuBtc4UsSeF5_K5z_czdDgh7gUMw7xGk` | `'Late dispatch'!A1:N` |
| `btk` | `1Ft40Pz5aW5yVD5gsz3lUeRtDvdXiYcDKMS3zhOIXQOg` | `'Late dispatch'!A1:L` |

1. `get_spreadsheet` with `fields: ["revisionId", "sheets.properties.sheetId", "sheets.properties.title", "sheets.properties.gridProperties.rowCount"]`. Find the "Late dispatch" tab. If its `sheetId` isn't the one in `config.json`, stop (failed: "Late dispatch tab id changed").
2. `get_values` for the range above. Save the result as JSON `{"values": [...]}` to `late-dispatch-sweep/work/fault_log.json` or `btk.json`, copying every row and cell exactly (keep empty strings and short rows as returned).

Then write `late-dispatch-sweep/work/meta.json`:
```json
{"fault_log": {"row_count": <rowCount>, "revision_id": "<revisionId>"},
 "btk": {"row_count": <rowCount>, "revision_id": "<revisionId>"}}
```

## 4. Older open orders

Run `H stale`. If it prints `NONE`, go on. Otherwise it prints a search string such as `name:#4801 OR name:#4802`: run the same `graphql_query` with `q` set to that string and append those orders to `orders.txt` in the same format.

## 5. Plan

Run `H plan`. It checks the sheets for existing errors, works out every addition and change, prints the plan, and writes `work/fault_log_requests.json`, `work/btk_requests.json` and matching `*_rollback.json` files.

- If it prints `ERROR:`, stop: `H finish --status failed --note "<the error>" --push`.
- Check that "Orders checked" plus any "older" count equals the number of orders you wrote. If not, fix `orders.txt` and run `H plan` again.

If `mode` is `dry_run`: run `H finish --status dry-run --push`, reply with its output, and stop. **Write nothing to the sheets.**

If the plan ends with `Writes needed: none`, skip to step 7.

## 6. Write (mode `live` only)

For each of `fault_log` then `btk` that is listed after "Writes needed":

- Read `work/<key>_requests.json` and call `update_spreadsheet` with its `spreadsheetId`, `requests` and `writeControl` exactly as in the file. Each call is all-or-nothing.
- If the Fault Log call fails, nothing has changed: stop (failed, with the error).
- If the BTK call fails after the Fault Log call succeeded: call `update_spreadsheet` with `work/fault_log_rollback.json` (its `spreadsheetId` and `requests`) to undo the Fault Log, then stop (failed, with the error and "Fault Log rolled back").
- If the error says the revision doesn't match (someone edited the sheet while the run was working): undo anything already written as above, then go back to step 3 **once** and redo steps 3 to 6. If it happens again, stop (failed: "sheet kept changing during the run").

## 7. Verify

If you wrote anything in step 6:

1. `get_values` both ranges again and save them to `work/fault_log_after.json` and `work/btk_after.json` (same format as step 3).
2. Run `H verify`. If it reports `FAILED`: call `update_spreadsheet` with `work/btk_rollback.json`, then with `work/fault_log_rollback.json` (each only if that sheet was written), then stop (failed, with the verify output and "rolled back").

## 8. Log

Run `H finish --status ok --push`. It writes `late-dispatch-sweep/logs/YYYY-MM-DD.txt` (plan, verification, errors) and pushes it to the `claude/late-dispatch-logs` branch. Reply with its output. If the push fails, say so in your reply; the sheets are still done.
