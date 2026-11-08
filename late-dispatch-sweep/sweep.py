#!/usr/bin/env python3
"""Late dispatch sweep for Stackers Australia.

Finds Shopify online orders that missed the "dispatch within 2 business days"
promise and upserts them into two Google Sheets:

  * Order Fault Log      -> tab "Late dispatch" (internal, every 3PL)
  * BTK Service Register -> tab "Late dispatch" (shared with BTK, BTK only)

Shopify is read only. The sheets are only ever appended to or have specific
blank cells filled in; people's notes, comments and manual statuses are never
changed and rows are never deleted.

Usage:
  python sweep.py --dry-run        # print the plan, write nothing
  python sweep.py                  # real run: write, verify, log
  python sweep.py --whoami         # print the service account email

Environment:
  SHOPIFY_ADMIN_TOKEN              Admin API token (read_orders, read_fulfillments,
                                   read_locations). Never printed or stored.
  GOOGLE_APPLICATION_CREDENTIALS   Path to the service account key JSON.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SHEETS_EPOCH = dt.date(1899, 12, 30)
ERROR_MARKERS = ("#REF!", "#VALUE!", "#NAME?", "#N/A")

FL_OPEN = "Open – overdue"          # en dash, matches the sheet's dropdown
FL_LATE = "Dispatched late"
FL_PROTECTED = {"Customer contacted", "Raised with 3PL", "Closed"}
BTK_OPEN = "Not yet dispatched"
BTK_LATE = "Dispatched late"


class SweepError(Exception):
    """A failure that should stop the run with a clear message."""


# --------------------------------------------------------------------------
# Dates and business days
# --------------------------------------------------------------------------

def to_local_date(ts: str | None, tz: ZoneInfo) -> dt.date | None:
    """Shopify UTC ISO timestamp -> local (Melbourne) calendar date."""
    if not ts:
        return None
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t.astimezone(tz).date()


def is_business_day(d: dt.date, holidays: set[dt.date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def workday(start: dt.date, days: int, holidays: set[dt.date]) -> dt.date:
    """Same result as Google Sheets =WORKDAY(start, days, holidays) for days > 0."""
    d = start
    left = days
    while left > 0:
        d += dt.timedelta(days=1)
        if is_business_day(d, holidays):
            left -= 1
    return d


def networkdays(start: dt.date, end: dt.date, holidays: set[dt.date]) -> int:
    """Same as =NETWORKDAYS(start, end, holidays) for start <= end."""
    if start > end:
        return -networkdays(end, start, holidays)
    n = 0
    d = start
    while d <= end:
        if is_business_day(d, holidays):
            n += 1
        d += dt.timedelta(days=1)
    return n


def date_to_serial(d: dt.date) -> int:
    return (d - SHEETS_EPOCH).days


def serial_to_date(n: float) -> dt.date:
    return SHEETS_EPOCH + dt.timedelta(days=int(n))


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%a %d %b %Y", "%d %b %Y", "%d %B %Y", "%a %d %B %Y")


def parse_sheet_date(v) -> dt.date | None:
    """A cell value (serial number or text) -> date. Blank -> None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return serial_to_date(v)
    s = str(v).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise SweepError(f"Could not read {s!r} as a date")


def long_date(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"     # 8 November 2026


def short_date(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"     # 8 Nov 2026


# --------------------------------------------------------------------------
# Shopify
# --------------------------------------------------------------------------

ORDER_FIELDS = """
  name
  createdAt
  cancelledAt
  tags
  displayFulfillmentStatus
  purchasingEntity { __typename }
  fulfillments(first: 5) { createdAt status location { name } }
  fulfillmentOrders(first: 5) { nodes { assignedLocation { name } } }
  lineItems(first: 50) { nodes { requiresShipping } }
"""

ORDERS_QUERY = """
query Orders($first: Int!, $after: String, $q: String) {
  orders(first: $first, after: $after, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { %s }
  }
}
""" % ORDER_FIELDS


class Shopify:
    def __init__(self, store: str, api_version: str, token: str):
        import requests  # local import so tests don't need it
        self._requests = requests
        self.url = f"https://{store}/admin/api/{api_version}/graphql.json"
        self.headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    def _post(self, query: str, variables: dict) -> dict:
        for attempt in range(6):
            try:
                r = self._requests.post(self.url, headers=self.headers,
                                        json={"query": query, "variables": variables}, timeout=60)
            except self._requests.RequestException as e:
                if attempt == 5:
                    raise SweepError(f"Shopify request failed: {e.__class__.__name__}") from None
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 401 or r.status_code == 403:
                raise SweepError(f"Shopify refused the token (HTTP {r.status_code}). "
                                 "Check SHOPIFY_ADMIN_TOKEN and the app's scopes.")
            if r.status_code != 200:
                raise SweepError(f"Shopify returned HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            errs = body.get("errors")
            if errs:
                codes = {(e.get("extensions") or {}).get("code") for e in errs if isinstance(e, dict)}
                if "THROTTLED" in codes:
                    time.sleep(2 ** attempt)
                    continue
                if "MAX_COST_EXCEEDED" in codes:
                    raise _CostTooHigh()
                msg = "; ".join(e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errs)
                raise SweepError(f"Shopify GraphQL error: {msg}")
            return body["data"]
        raise SweepError("Shopify kept throttling or failing after 6 attempts")

    def orders(self, search: str, page_size: int) -> list[dict]:
        out: list[dict] = []
        after = None
        size = page_size
        while True:
            try:
                data = self._post(ORDERS_QUERY, {"first": size, "after": after, "q": search})
            except _CostTooHigh:
                if size <= 10:
                    raise SweepError("Shopify query cost too high even at 10 orders per page")
                size //= 2
                continue
            conn = data["orders"]
            out.extend(conn["nodes"])
            if not conn["pageInfo"]["hasNextPage"]:
                return out
            after = conn["pageInfo"]["endCursor"]


class _CostTooHigh(Exception):
    pass


# --------------------------------------------------------------------------
# Order evaluation
# --------------------------------------------------------------------------

@dataclass
class Order:
    number: int
    name: str
    order_date: dt.date
    due: dt.date
    dispatched: dt.date | None
    cancelled: dt.date | None
    location: str | None
    tpl: str | None
    late: bool
    excluded: str | None = None    # reason, if excluded


def order_number(name_or_cell) -> int | None:
    if name_or_cell is None or name_or_cell == "":
        return None
    if isinstance(name_or_cell, (int, float)):
        return int(name_or_cell)
    digits = re.sub(r"\D", "", str(name_or_cell))
    return int(digits) if digits else None


def map_3pl(location: str | None, mapping: dict[str, str]) -> tuple[str | None, bool]:
    """Returns (3PL name, was_mapped)."""
    if not location:
        return None, False
    if location in mapping:
        return mapping[location], True
    low = {k.lower(): v for k, v in mapping.items()}
    if location.lower() in low:
        return low[location.lower()], True
    return location, False


def evaluate(raw: dict, holidays: set[dt.date], today: dt.date, tz: ZoneInfo,
             mapping: dict[str, str], promise_days: int) -> Order:
    num = order_number(raw["name"])
    odate = to_local_date(raw["createdAt"], tz)
    due = workday(odate, promise_days, holidays)

    good_fulfils = [f for f in (raw.get("fulfillments") or [])
                    if (f.get("status") or "SUCCESS") not in ("CANCELLED", "ERROR", "FAILURE")]
    good_fulfils.sort(key=lambda f: f["createdAt"])
    dispatched = to_local_date(good_fulfils[0]["createdAt"], tz) if good_fulfils else None

    location = None
    if good_fulfils and good_fulfils[0].get("location"):
        location = good_fulfils[0]["location"]["name"]
    if not location:
        for fo in (raw.get("fulfillmentOrders") or {}).get("nodes", []):
            loc = fo.get("assignedLocation") or {}
            if loc.get("name"):
                location = loc["name"]
                break
    tpl, _ = map_3pl(location, mapping)

    if dispatched:
        late = dispatched > due
    else:
        late = today > due

    tags = {t.strip().lower() for t in (raw.get("tags") or [])}
    pe = (raw.get("purchasingEntity") or {}).get("__typename")
    excluded = None
    if raw.get("cancelledAt"):
        excluded = "cancelled"
    elif not any(li.get("requiresShipping") for li in (raw.get("lineItems") or {}).get("nodes", [])):
        excluded = "no shippable items"
    elif "test" in tags:
        excluded = "test tag"
    elif pe == "PurchasingCompany" or "b2b" in tags:
        excluded = "B2B"

    # TODO(precision-sla): Precision's own SLA (orders received by 6:00am are
    # dispatched the same business day) is a second, stricter test. When it is
    # in scope, add a `precision_late` flag here (cut-off 06:00 Melbourne on
    # the order's createdAt) and a separate destination; do not change `late`.

    return Order(number=num, name=raw["name"], order_date=odate, due=due, dispatched=dispatched,
                 cancelled=to_local_date(raw.get("cancelledAt"), tz), location=location,
                 tpl=tpl, late=late, excluded=excluded)



# --------------------------------------------------------------------------
# Google Sheets
# --------------------------------------------------------------------------

def col_letter(idx: int) -> str:
    return chr(ord("A") + idx)


class Sheets:
    BASE = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self, key_path: str):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        try:
            creds = service_account.Credentials.from_service_account_file(
                key_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        except FileNotFoundError:
            raise SweepError(f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file") from None
        self.email = creds.service_account_email
        self.s = AuthorizedSession(creds)

    def _req(self, method: str, url: str, **kw) -> dict:
        for attempt in range(5):
            r = self.s.request(method, url, timeout=60, **kw)
            if r.status_code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 403:
                raise SweepError(f"Google refused access (403). Share the sheet with {self.email} "
                                 "as Editor.")
            if r.status_code != 200:
                raise SweepError(f"Google Sheets HTTP {r.status_code}: {r.text[:400]}")
            return r.json()
        raise SweepError("Google Sheets kept failing after 5 attempts")

    def sheet_props(self, sid: str, tab: str) -> dict:
        meta = self._req("GET", f"{self.BASE}/{sid}",
                         params={"fields": "sheets.properties(sheetId,title,gridProperties)"})
        for sh in meta["sheets"]:
            if sh["properties"]["title"] == tab:
                return sh["properties"]
        raise SweepError(f"Tab {tab!r} not found in spreadsheet {sid}")

    def values(self, sid: str, rng: str, render: str) -> list[list]:
        data = self._req("GET", f"{self.BASE}/{sid}/values/{rng}",
                         params={"valueRenderOption": render,
                                 "dateTimeRenderOption": "SERIAL_NUMBER"})
        return data.get("values", [])

    def number_formats(self, sid: str, rng: str) -> list[list[dict | None]]:
        data = self._req("GET", f"{self.BASE}/{sid}", params={
            "ranges": rng, "includeGridData": "true",
            "fields": "sheets.data.rowData.values.userEnteredFormat.numberFormat"})
        rows = data["sheets"][0]["data"][0].get("rowData", [])
        return [[(c.get("userEnteredFormat") or {}).get("numberFormat") for c in r.get("values", [])]
                for r in rows]

    def batch_update(self, sid: str, requests: list[dict]) -> None:
        if requests:
            self._req("POST", f"{self.BASE}/{sid}:batchUpdate", json={"requests": requests})


# --------------------------------------------------------------------------
# Sheet model and planning
# --------------------------------------------------------------------------

@dataclass
class Layout:
    key: str                 # "fault_log" or "btk_register"
    label: str
    spreadsheet_id: str
    tab: str
    first_data_row: int
    last_col: int            # last column we write, 0-based (I=8 / H=7)
    order_col: int = 0
    date_col: int = 1
    dispatched_col: int = 3
    status_col: int = 7
    notes_col: int | None = 8
    tpl_col: int | None = 6
    holiday_col: int = 10    # K


def fault_log_layout(c: dict) -> Layout:
    return Layout("fault_log", "Order Fault Log", c["spreadsheet_id"], c["tab"],
                  c["first_data_row"], last_col=8, status_col=7, notes_col=8, tpl_col=6)


def btk_layout(c: dict) -> Layout:
    # BTK column H is "BTK comment": never written, so there is no notes column.
    return Layout("btk_register", "BTK Service Register", c["spreadsheet_id"], c["tab"],
                  c["first_data_row"], last_col=7, status_col=6, notes_col=None, tpl_col=None)


@dataclass
class SheetState:
    layout: Layout
    sheet_id: int
    row_count: int
    rows: dict[int, list]            # sheet row number -> FORMULA-render values (A..K)
    by_order: dict[int, int]         # order number -> sheet row
    last_data_row: int               # last row with anything in A..last_col (header row if none)
    holidays: set[dt.date]
    a2: str | None = None            # BTK only
    b_formats: dict[int, dict | None] = field(default_factory=dict)
    d_formats: dict[int, dict | None] = field(default_factory=dict)


def build_state(layout: Layout, sheet_id: int, row_count: int, formula_rows: list[list],
                a2: str | None = None) -> SheetState:
    """formula_rows starts at first_data_row (FORMULA render)."""
    rows, by_order, holidays = {}, {}, set()
    last = layout.first_data_row - 1
    for i, r in enumerate(formula_rows):
        n = layout.first_data_row + i
        r = list(r) + [""] * (11 - len(r))
        rows[n] = r
        if len(r) > layout.holiday_col and r[layout.holiday_col] not in ("", None):
            try:
                holidays.add(parse_sheet_date(r[layout.holiday_col]))
            except SweepError as e:
                raise SweepError(f"{layout.label}: holiday cell K{n}: {e}") from None
        if any(v not in ("", None) for v in r[: layout.last_col + 1]):
            last = n
        num = order_number(r[layout.order_col])
        if num is not None:
            if num in by_order:
                raise SweepError(f"{layout.label}: order {num} appears twice "
                                 f"(rows {by_order[num]} and {n}); fix before running")
            by_order[num] = n
    return SheetState(layout, sheet_id, row_count, rows, by_order, last, holidays, a2)


@dataclass
class CellChange:
    row: int
    col: int
    old: object
    new: object
    kind: str            # "date" | "text"
    why: str


@dataclass
class Plan:
    layout: Layout
    appends: list[Order] = field(default_factory=list)
    changes: list[CellChange] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)     # informational, nothing written


def plan_sheet(state: SheetState, orders: dict[int, Order], today: dt.date) -> Plan:
    L = state.layout
    p = Plan(L)
    is_btk = L.key == "btk_register"

    # 1. Existing rows.
    for num, row in sorted(state.by_order.items(), key=lambda kv: kv[1]):
        o = orders.get(num)
        if o is None:
            continue
        r = state.rows[row]
        status = str(r[L.status_col] or "")
        if o.cancelled:
            if L.notes_col is not None:
                if str(r[L.notes_col] or "").strip() == "":
                    p.changes.append(CellChange(row, L.notes_col, r[L.notes_col],
                                                f"Cancelled in Shopify {short_date(o.cancelled)}",
                                                "text", f"order {num} cancelled"))
            else:
                p.notes.append(f"Order #{num} (row {row}) was cancelled in Shopify on "
                               f"{short_date(o.cancelled)}; left unchanged (no notes column).")
            continue
        if r[L.dispatched_col] in ("", None) and o.dispatched:
            p.changes.append(CellChange(row, L.dispatched_col, "", o.dispatched, "date",
                                        f"order {num} now fulfilled"))
            if o.dispatched <= o.due:
                p.notes.append(f"Order #{num} (row {row}) dispatched {short_date(o.dispatched)}, "
                               "on time by our calculation; Status not changed.")
                continue
            if is_btk:
                if status == BTK_OPEN:
                    p.changes.append(CellChange(row, L.status_col, status, BTK_LATE, "text",
                                                f"order {num} now dispatched"))
            else:
                if _norm_dash(status) == _norm_dash(FL_OPEN):
                    p.changes.append(CellChange(row, L.status_col, status, FL_LATE, "text",
                                                f"order {num} now dispatched"))

    # 2. New late orders.
    for num in sorted(orders):
        o = orders[num]
        if num in state.by_order or o.excluded or not o.late:
            continue
        if is_btk and o.tpl != "BTK":
            continue
        p.appends.append(o)

    # 3. BTK A2 "Status as at ..." date.
    if is_btk and state.a2 is not None:
        new = re.sub(r"Status as at [^.]*\.\s*$", f"Status as at {long_date(today)}.", state.a2)
        if new == state.a2 and "Status as at" not in state.a2:
            p.notes.append("A2 has no 'Status as at ...' sentence; left unchanged.")
        elif new != state.a2:
            p.changes.append(CellChange(2, 0, state.a2, new, "text", "status date"))
    return p


def _norm_dash(s: str) -> str:
    return re.sub(r"\s*[-–—]\s*", " - ", s.strip())


def new_row_values(L: Layout, n: int, o: Order) -> list[dict]:
    """userEnteredValue cells A..last_col for a new row n."""
    hol = f"$K${L.first_data_row}:$K"
    cells: list[dict] = []
    if L.key == "fault_log":
        cells.append({"numberValue": o.number})
    else:
        cells.append({"stringValue": f"#{o.number}"})
    cells.append({"numberValue": date_to_serial(o.order_date)})
    cells.append({"formulaValue": f"=WORKDAY(B{n},2,{hol})"})
    cells.append({"numberValue": date_to_serial(o.dispatched)} if o.dispatched else None)
    cells.append({"formulaValue": f'=IF(D{n}="","",NETWORKDAYS(B{n}+1,D{n},{hol}))'})
    cells.append({"formulaValue": f'=IF(D{n}="","",E{n}-2)'})
    if L.key == "fault_log":
        cells.append({"stringValue": o.tpl or ""})
        cells.append({"stringValue": FL_LATE if o.dispatched else FL_OPEN})
        cells.append(None)     # I Notes / follow-up: left for people
    else:
        cells.append({"stringValue": BTK_LATE if o.dispatched else BTK_OPEN})
        cells.append(None)     # H BTK comment: left for BTK
    return cells


def _cell(v) -> dict:
    return {"userEnteredValue": v} if v is not None else {}


def _old_value(v) -> dict | None:
    if v in ("", None):
        return None
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, (int, float)):
        return {"numberValue": v}
    s = str(v)
    return {"formulaValue": s} if s.startswith("=") else {"stringValue": s}


def build_requests(state: SheetState, plan: Plan) -> tuple[list[dict], list[dict]]:
    """Returns (forward requests, rollback requests) for one spreadsheet."""
    L, sid = state.layout, state.sheet_id
    fwd: list[dict] = []
    back: list[dict] = []
    width = L.last_col + 1

    if plan.appends:
        start = state.last_data_row + 1               # 1-based sheet row
        end = start + len(plan.appends) - 1
        if end > state.row_count:
            extra = end - state.row_count
            fwd.append({"appendDimension": {"sheetId": sid, "dimension": "ROWS", "length": extra}})
            back.append({"deleteDimension": {"range": {"sheetId": sid, "dimension": "ROWS",
                                                       "startIndex": state.row_count,
                                                       "endIndex": state.row_count + extra}}})
        dest = {"sheetId": sid, "startRowIndex": start - 1, "endRowIndex": end,
                "startColumnIndex": 0, "endColumnIndex": width}
        if state.last_data_row >= L.first_data_row:
            src = {"sheetId": sid, "startRowIndex": state.last_data_row - 1,
                   "endRowIndex": state.last_data_row, "startColumnIndex": 0, "endColumnIndex": width}
            for pt in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION"):
                fwd.append({"copyPaste": {"source": src, "destination": dest, "pasteType": pt}})
        else:
            fwd.append({"repeatCell": {
                "range": {**dest, "startColumnIndex": 1, "endColumnIndex": 4},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "d mmm yyyy"}}},
                "fields": "userEnteredFormat.numberFormat"}})
        rows = [{"values": [_cell(v) for v in new_row_values(L, start + i, o)]}
                for i, o in enumerate(plan.appends)]
        fwd.append({"updateCells": {"range": dest, "rows": rows, "fields": "userEnteredValue"}})
        back.append({"updateCells": {"range": dest, "rows": [{"values": [{}] * width}] * len(rows),
                                     "fields": "userEnteredValue"}})

    for ch in plan.changes:
        rng = {"sheetId": sid, "startRowIndex": ch.row - 1, "endRowIndex": ch.row,
               "startColumnIndex": ch.col, "endColumnIndex": ch.col + 1}
        fields = "userEnteredValue"
        if ch.kind == "date":
            cell = {"userEnteredValue": {"numberValue": date_to_serial(ch.new)}}
            dfmt = state.d_formats.get(ch.row)
            if not dfmt or dfmt.get("type") not in ("DATE", "DATE_TIME"):
                cell["userEnteredFormat"] = {"numberFormat": state.b_formats.get(ch.row)
                                             or {"type": "DATE", "pattern": "d mmm yyyy"}}
                fields += ",userEnteredFormat.numberFormat"
        else:
            cell = {"userEnteredValue": {"stringValue": ch.new}}
        fwd.append({"updateCells": {"range": rng, "rows": [{"values": [cell]}], "fields": fields}})
        back.append({"updateCells": {"range": rng, "rows": [{"values": [_cell(_old_value(ch.old))]}],
                                     "fields": "userEnteredValue"}})
    back.reverse()
    return fwd, back


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def check_sheet(label: str, formatted: list[list], first_row: int = 1) -> list[str]:
    problems = []
    for i, r in enumerate(formatted):
        for j, v in enumerate(r):
            if isinstance(v, str) and v.strip() in ERROR_MARKERS:
                problems.append(f"{label}: {col_letter(j)}{first_row + i} shows {v.strip()}")
    return problems


def check_btk_counts(formatted: list[list], first_data_row: int) -> list[str]:
    """B4 = Dispatched late, B5 = Not yet dispatched, B6 = Total rows."""
    def cell(r, c):
        try:
            return formatted[r - 1][c]
        except IndexError:
            return ""
    data = formatted[first_data_row - 1:]
    late = sum(1 for r in data if len(r) > 6 and r[6] == BTK_LATE)
    open_ = sum(1 for r in data if len(r) > 6 and r[6] == BTK_OPEN)
    total = sum(1 for r in data if r and str(r[0]).strip() != "")
    problems = []
    for row, label, want in ((4, "Dispatched late", late), (5, "Not yet dispatched", open_), (6, "Total", total)):
        got = str(cell(row, 1)).strip()
        if got != str(want):
            problems.append(f"BTK Service Register: B{row} ({label}) shows {got!r}, rows say {want}")
    return problems


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def describe(plan: Plan, state: SheetState) -> list[str]:
    L = plan.layout
    out = [f"== {L.label} / {L.tab} =="]
    if plan.appends:
        start = state.last_data_row + 1
        out.append(f"Add {len(plan.appends)} row(s) from row {start}:")
        for i, o in enumerate(plan.appends):
            disp = short_date(o.dispatched) if o.dispatched else "-"
            over = (networkdays(o.order_date + dt.timedelta(days=1), o.dispatched, state.holidays) - 2
                    if o.dispatched else None)
            status = ((FL_LATE if o.dispatched else FL_OPEN) if L.key == "fault_log"
                      else (BTK_LATE if o.dispatched else BTK_OPEN))
            tpl = f" | 3PL {o.tpl}" if L.key == "fault_log" else ""
            over_s = f" | +{over} bd" if over is not None else ""
            out.append(f"  row {start + i}: #{o.number} | ordered {short_date(o.order_date)} | "
                       f"due {short_date(o.due)} | dispatched {disp}{over_s}{tpl} | {status}")
    else:
        out.append("Add: none")
    if plan.changes:
        out.append(f"Change {len(plan.changes)} cell(s):")
        for ch in plan.changes:
            old = ch.old if ch.old not in ("", None) else "(blank)"
            new = short_date(ch.new) if isinstance(ch.new, dt.date) else ch.new
            if ch.row == 2 and ch.col == 0:
                old = "…" + str(old)[-40:]
                new = "…" + str(new)[-40:]
            out.append(f"  {col_letter(ch.col)}{ch.row}: {old!s} -> {new!s}   ({ch.why})")
    else:
        out.append("Change: none")
    for n in plan.notes:
        out.append(f"  note: {n}")
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_config() -> dict:
    with open(HERE / "config.json") as f:
        return json.load(f)


def should_run_scheduled(cfg: dict, cron_utc: str, now_utc: dt.datetime) -> tuple[bool, str]:
    """GitHub cron is UTC-only. The workflow fires at two UTC times so one of
    them is 07:00 Melbourne in both AEST and AEDT; this keeps only that one,
    and only on every second day counted from the anchor date."""
    tz = ZoneInfo(cfg["timezone"])
    sc = cfg["schedule"]
    minute, hour = (int(x) for x in cron_utc.split()[:2])
    fired = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fired > now_utc:
        fired -= dt.timedelta(days=1)
    local = fired.astimezone(tz)
    if local.hour != sc["local_hour"]:
        return False, f"cron {cron_utc!r} is {local:%H:%M} Melbourne, not {sc['local_hour']:02d}:00"
    gap = (local.date() - dt.date.fromisoformat(sc["anchor_date"])).days
    if gap % sc["every_n_days"] != 0:
        return False, f"{local.date()} is not a run day (every {sc['every_n_days']} days from {sc['anchor_date']})"
    return True, f"run day {local.date()}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    ap.add_argument("--whoami", action="store_true", help="print the service account email and exit")
    ap.add_argument("--scheduled-cron", metavar="CRON",
                    help="(GitHub Actions) the UTC cron that fired; skip unless it is a 07:00 run day")
    ap.add_argument("--today", help="override today's Melbourne date (YYYY-MM-DD), for testing")
    args = ap.parse_args(argv)

    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    now_utc = dt.datetime.now(dt.timezone.utc)

    if args.scheduled_cron:
        ok, why = should_run_scheduled(cfg, args.scheduled_cron, now_utc)
        print(f"Schedule check: {why}")
        if not ok:
            return 0

    today = dt.date.fromisoformat(args.today) if args.today else now_utc.astimezone(tz).date()
    log: list[str] = [f"Late dispatch sweep {'DRY RUN ' if args.dry_run else ''}"
                      f"{now_utc.astimezone(tz):%Y-%m-%d %H:%M} Melbourne (today = {today})"]
    written: list[tuple[Sheets, str, list[dict], str]] = []
    sheets = None
    try:
        key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not key:
            raise SweepError("GOOGLE_APPLICATION_CREDENTIALS is not set")
        sheets = Sheets(key)
        if args.whoami:
            print(sheets.email)
            return 0
        token = os.environ.get("SHOPIFY_ADMIN_TOKEN")
        if not token:
            raise SweepError("SHOPIFY_ADMIN_TOKEN is not set")

        # ---- Read both sheets (nothing is written until everything is planned)
        states: list[SheetState] = []
        for layout in (fault_log_layout(cfg["fault_log"]), btk_layout(cfg["btk_register"])):
            props = sheets.sheet_props(layout.spreadsheet_id, layout.tab)
            tab = f"'{layout.tab}'"
            first = layout.first_data_row
            formula = sheets.values(layout.spreadsheet_id, f"{tab}!A{first}:K", "FORMULA")
            a2 = None
            if layout.key == "btk_register":
                top = sheets.values(layout.spreadsheet_id, f"{tab}!A2", "FORMATTED_VALUE")
                a2 = top[0][0] if top and top[0] else ""
            st = build_state(layout, props["sheetId"], props["gridProperties"]["rowCount"], formula, a2)
            fmts = sheets.number_formats(layout.spreadsheet_id, f"{tab}!B{first}:D{max(st.last_data_row, first)}")
            for i, r in enumerate(fmts):
                st.b_formats[first + i] = r[0] if len(r) > 0 else None
                st.d_formats[first + i] = r[2] if len(r) > 2 else None
            fv = sheets.values(layout.spreadsheet_id, f"{tab}!A1:K", "FORMATTED_VALUE")
            pre = check_sheet(layout.label, fv)
            if layout.key == "btk_register":
                pre += check_btk_counts(fv, first)
            if pre:
                raise SweepError("Sheet problems found before writing (nothing changed):\n  " + "\n  ".join(pre))
            states.append(st)

        fl_hol, btk_hol = states[0].holidays, states[1].holidays
        if fl_hol != btk_hol:
            log.append(f"WARNING: holiday lists differ. Fault Log: {sorted(map(str, fl_hol))}; "
                       f"BTK: {sorted(map(str, btk_hol))}. Each sheet uses its own list.")
        if not any(h >= today for h in fl_hol | btk_hol):
            log.append("WARNING: no public holidays on or after today are listed in column K.")

        # ---- Shopify
        sc = cfg["shopify"]
        shop = Shopify(sc["store"], sc["api_version"], token)
        since = (now_utc - dt.timedelta(days=sc["lookback_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = shop.orders(f"created_at:>='{since}'", sc["page_size"])
        fetched = {order_number(r["name"]): r for r in raw}
        # Open rows whose order is older than the window still need their Dispatched date.
        stale = sorted({num for st in states for num, row in st.by_order.items()
                        if st.rows[row][st.layout.dispatched_col] in ("", None) and num not in fetched})
        for i in range(0, len(stale), 25):
            q = " OR ".join(f"name:#{n}" for n in stale[i:i + 25])
            for r in shop.orders(q, sc["page_size"]):
                fetched.setdefault(order_number(r["name"]), r)

        mapping = cfg["location_to_3pl"]
        per_sheet_orders = []
        unmapped: set[str] = set()
        for st in states:
            evald = {}
            for num, r in fetched.items():
                o = evaluate(r, st.holidays, today, tz, mapping, cfg["promise_business_days"])
                evald[num] = o
            per_sheet_orders.append(evald)
        for o in per_sheet_orders[0].values():
            if o.location and not o.excluded and o.late and not map_3pl(o.location, mapping)[1]:
                unmapped.add(o.location)

        window_nums = {order_number(r["name"]) for r in raw}
        in_window = [o for n, o in per_sheet_orders[0].items() if n in window_nums]
        excl: dict[str, int] = {}
        for o in in_window:
            if o.excluded:
                excl[o.excluded] = excl.get(o.excluded, 0) + 1
        late = [o for o in in_window if o.late and not o.excluded]

        plans = [plan_sheet(st, ords, today) for st, ords in zip(states, per_sheet_orders)]

        log.append(f"Orders checked: {len(raw)} in the last {sc['lookback_days']} days"
                   + (f" (+{len(fetched) - len(raw)} older open orders looked up)" if len(fetched) > len(raw) else ""))
        log.append("Excluded: " + (", ".join(f"{k} {v}" for k, v in sorted(excl.items())) or "none"))
        by_tpl: dict[str, int] = {}
        for o in late:
            by_tpl[o.tpl or "(no location)"] = by_tpl.get(o.tpl or "(no location)", 0) + 1
        log.append("Late orders in window by 3PL: " + (", ".join(f"{k} {v}" for k, v in sorted(by_tpl.items())) or "none"))
        new_by_tpl: dict[str, int] = {}
        for o in plans[0].appends:
            new_by_tpl[o.tpl or "(no location)"] = new_by_tpl.get(o.tpl or "(no location)", 0) + 1
        log.append("New late orders by 3PL (Fault Log): " + (", ".join(f"{k} {v}" for k, v in sorted(new_by_tpl.items())) or "none"))
        log.append(f"New rows: Fault Log {len(plans[0].appends)}, BTK {len(plans[1].appends)}")
        log.append(f"Cells updated: Fault Log {len(plans[0].changes)}, BTK {len(plans[1].changes)}")
        log.append("Unmapped locations: " + (", ".join(sorted(unmapped)) or "none"))
        log.append("")
        for st, p in zip(states, plans):
            log.extend(describe(p, st))
            log.append("")

        if args.dry_run:
            log.append("DRY RUN: nothing was written.")
            print("\n".join(log))
            return 0

        # ---- Write, then verify. Any failure rolls back what this run wrote.
        for st, p in zip(states, plans):
            fwd, back = build_requests(st, p)
            if fwd:
                sheets.batch_update(st.layout.spreadsheet_id, fwd)
                written.append((sheets, st.layout.spreadsheet_id, back, st.layout.label))
        problems = []
        for st in states:
            L = st.layout
            fv = sheets.values(L.spreadsheet_id, f"'{L.tab}'!A1:K", "FORMATTED_VALUE")
            problems += check_sheet(L.label, fv)
            if L.key == "btk_register":
                problems += check_btk_counts(fv, L.first_data_row)
        if problems:
            raise SweepError("Verification failed after writing:\n  " + "\n  ".join(problems))
        log.append("Verification: no formula errors; BTK summary counts match the rows.")
        log.append("Errors: none")
        write_log(log, today)
        print("\n".join(log))
        return 0

    except SweepError as e:
        rolled = rollback(written)
        log.append(f"ERROR: {e}")
        if rolled:
            log.append(rolled)
        print("\n".join(log), file=sys.stderr)
        if not args.dry_run and not args.whoami:
            write_log(log, today)
        return 1
    except Exception as e:   # unexpected: still roll back and fail loudly
        rolled = rollback(written)
        log.append(f"ERROR (unexpected): {e.__class__.__name__}: {e}")
        if rolled:
            log.append(rolled)
        print("\n".join(log), file=sys.stderr)
        if not args.dry_run and not args.whoami:
            write_log(log, today)
        return 1


def rollback(written) -> str:
    if not written:
        return ""
    msgs = []
    for sheets, sid, back, label in reversed(written):
        try:
            sheets.batch_update(sid, back)
            msgs.append(f"rolled back {label}")
        except Exception as e:  # noqa: BLE001
            msgs.append(f"ROLLBACK FAILED for {label}: {e} - check the sheet by hand")
    return "Rollback: " + "; ".join(msgs)


def write_log(lines: list[str], today: dt.date) -> None:
    d = HERE / "logs"
    d.mkdir(exist_ok=True)
    with open(d / f"{today:%Y-%m-%d}.txt", "a") as f:
        f.write("\n".join(lines) + "\n\n")


if __name__ == "__main__":
    sys.exit(main())
