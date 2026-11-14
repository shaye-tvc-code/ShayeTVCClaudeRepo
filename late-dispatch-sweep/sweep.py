#!/usr/bin/env python3
"""Offline helper for the late dispatch sweep routine (Stackers Australia).

The routine (see ROUTINE_PROMPT.md) reads Shopify and the two Google Sheets
through the Claude connectors and saves what it read into work/. This script
does the date maths and builds the exact sheet edits. It never talks to
Shopify or Google and needs no keys: standard library only.

Commands (run from the repo root):
  python3 late-dispatch-sweep/sweep.py should-run          RUN or SKIP (every second day)
  python3 late-dispatch-sweep/sweep.py stale               open sheet rows to look up in Shopify
  python3 late-dispatch-sweep/sweep.py plan                print the plan, write work/*_requests.json
  python3 late-dispatch-sweep/sweep.py verify              check work/*_after.json read-backs
  python3 late-dispatch-sweep/sweep.py finish --status ok  write logs/YYYY-MM-DD.txt and push it

Input files in late-dispatch-sweep/work/:
  orders.txt         one Shopify order per line (format: see parse_order_line)
  fault_log.json     get_values result of 'Late dispatch'!A1:N  (Order Fault Log)
  btk.json           get_values result of 'Late dispatch'!A1:L  (BTK Service Register)
  meta.json          {"fault_log": {"row_count": N, "revision_id": "..."},
                      "btk": {"row_count": N, "revision_id": "..."}}
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
SHEETS_EPOCH = dt.date(1899, 12, 30)
ERROR_MARKERS = ("#REF!", "#VALUE!", "#NAME?", "#N/A")

# Defaults; config.json "statuses" overrides them. The Fault Log dropdown is
# numbered ("1. Open – overdue" ... "5. Closed"); en dash as in the sheet.
FL_OPEN = "1. Open – overdue"
FL_LATE = "4. Dispatched late"
BTK_OPEN = "Not yet dispatched"
BTK_LATE = "Dispatched late"


class SweepError(Exception):
    """A failure that should stop the run with a clear message."""


# --------------------------------------------------------------------------
# Dates and business days
# --------------------------------------------------------------------------

def to_local(ts: str | None, tz: ZoneInfo) -> dt.datetime | None:
    if not ts or ts == "-":
        return None
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz)


def is_business_day(d: dt.date, holidays: set[dt.date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def workday(start: dt.date, days: int, holidays: set[dt.date]) -> dt.date:
    """=WORKDAY(start, days, holidays) for days >= 1."""
    d, left = start, days
    while left > 0:
        d += dt.timedelta(days=1)
        if is_business_day(d, holidays):
            left -= 1
    return d


def networkdays(start: dt.date, end: dt.date, holidays: set[dt.date]) -> int:
    """=NETWORKDAYS(start, end, holidays)."""
    if start > end:
        return -networkdays(end, start, holidays)
    n, d = 0, start
    while d <= end:
        n += is_business_day(d, holidays)
        d += dt.timedelta(days=1)
    return n


def received_by_3pl(order_dt: dt.datetime, cutoff: dt.time, holidays: set[dt.date]) -> dt.date:
    """Order date if it's a business day and the time is before the cut-off,
    otherwise the next business day. Matches the sheets' column formula."""
    d = order_dt.date()
    t = order_dt.time().replace(second=0, microsecond=0)
    if is_business_day(d, holidays) and t < cutoff:
        return d
    return workday(d, 1, holidays)


def date_to_serial(d: dt.date) -> int:
    return (d - SHEETS_EPOCH).days


def time_to_fraction(t: dt.time) -> float:
    return (t.hour * 60 + t.minute) / 1440


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%a %d %b %Y", "%d %b %Y", "%d %B %Y", "%a %d %B %Y")


def parse_sheet_date(v) -> dt.date | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, (int, float)):
        return SHEETS_EPOCH + dt.timedelta(days=int(v))
    s = str(v).strip()
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


def hhmm(t: dt.time) -> str:
    return t.strftime("%H:%M")


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------

def parse_order_line(line: str) -> dict:
    """One order per line, fields separated by ' | ':

      name | createdAt | cancelledAt | tags | purchasingEntity | shippable | fulfilments | assigned | outstanding

      name             #5086
      createdAt        2026-11-04T07:27:03Z (UTC, as Shopify returns it)
      cancelledAt      timestamp or -
      tags             comma-separated, or -
      purchasingEntity Customer or PurchasingCompany (the __typename), or -
      shippable        Y if any line item has requiresShipping true, else N
      fulfilments      createdAt@status@location;createdAt@status@location... or -
      assigned         first fulfillmentOrders assignedLocation name, or -
      outstanding      units still to ship and where, as <units>@<location name> (the
                       location of the first open fulfillment order with items left), or -
                       Optional: older 8-field lines are read as outstanding = -.
    """
    p = [x.strip() for x in line.split(" | ")]
    if len(p) == 8:
        p.append("-")
    if len(p) != 9:
        raise SweepError(f"orders.txt line has {len(p)} fields, expected 9: {line!r}")
    name, created, cancelled, tags, pe, ship, fuls, assigned, outs = p
    out_units, out_loc = 0, None
    if outs != "-":
        u, sep, loc = outs.partition("@")
        if not sep or not u.strip().isdigit() or not loc.strip():
            raise SweepError(f"outstanding must be <units>@<location> or - in line {line!r}")
        out_units, out_loc = int(u), loc.strip()
    fl = []
    if fuls != "-":
        for f in fuls.split(";"):
            parts = f.split("@", 2)
            if len(parts) != 3:
                raise SweepError(f"Bad fulfilment {f!r} in line {line!r}")
            fl.append({"createdAt": parts[0], "status": parts[1], "location": parts[2]})
    if ship not in ("Y", "N"):
        raise SweepError(f"shippable must be Y or N in line {line!r}")
    return {"name": name, "createdAt": created, "cancelledAt": None if cancelled == "-" else cancelled,
            "tags": [] if tags == "-" else [t.strip() for t in tags.split(",")],
            "purchasingEntity": None if pe == "-" else pe, "shippable": ship == "Y",
            "fulfillments": fl, "assigned": None if assigned == "-" else assigned,
            "outstanding": out_units, "outstanding_location": out_loc}


def load_orders(path: Path) -> dict[int, dict]:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#!"):
            o = parse_order_line(line)
            out[order_number(o["name"])] = o
    return out


@dataclass
class Order:
    number: int
    placed: dt.datetime          # Melbourne
    due: dt.date
    dispatched: dt.date | None
    cancelled: dt.date | None
    location: str | None
    tpl: str | None
    late: bool
    excluded: str | None = None
    completed: dt.date | None = None   # last parcel's date, once nothing is left to ship
    outstanding: int = 0               # units still to ship (part-dispatched orders)

    @property
    def order_date(self) -> dt.date:
        return self.placed.date()


def order_number(v) -> int | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    digits = re.sub(r"\D", "", str(v))
    return int(digits) if digits else None


def map_3pl(location: str | None, mapping: dict[str, str]) -> tuple[str | None, bool]:
    if not location:
        return None, False
    for k, v in mapping.items():
        if k.lower() == location.lower():
            return v, True
    return location, False


def evaluate(raw: dict, holidays: set[dt.date], today: dt.date, tz: ZoneInfo,
             mapping: dict[str, str], promise_days: int) -> Order:
    placed = to_local(raw["createdAt"], tz)
    due = workday(placed.date(), promise_days, holidays)
    good = sorted((f for f in raw["fulfillments"]
                   if f["status"].upper() not in ("CANCELLED", "ERROR", "FAILURE")),
                  key=lambda f: f["createdAt"])
    dispatched = to_local(good[0]["createdAt"], tz).date() if good else None
    location = (good[0]["location"] if good and good[0]["location"] not in ("", "-") else None) \
        or raw.get("assigned")
    outstanding = raw.get("outstanding", 0)
    completed = to_local(good[-1]["createdAt"], tz).date() if good and not outstanding else None
    if outstanding and good:
        # Part dispatched: the customer is still waiting for the rest, so the
        # order counts as not yet dispatched, against whoever holds the rest.
        dispatched = None
        location = raw.get("outstanding_location") or location
    tpl, _ = map_3pl(location, mapping)
    late = dispatched > due if dispatched else today > due

    tags = {t.lower() for t in raw["tags"]}
    excluded = None
    if raw["cancelledAt"]:
        excluded = "cancelled"
    elif not raw["shippable"]:
        excluded = "no shippable items"
    elif "test" in tags:
        excluded = "test tag"
    elif raw["purchasingEntity"] == "PurchasingCompany" or "b2b" in tags:
        excluded = "B2B"

    # TODO(precision-sla): Precision's own SLA (orders received by 6:00am are
    # dispatched the same business day) is a second, stricter late test. When
    # it is in scope, add a `precision_late` flag here using received_by_3pl()
    # with the Precision cut-off; do not change `late`, which is the customer
    # promise test.

    c = to_local(raw["cancelledAt"], tz)
    return Order(order_number(raw["name"]), placed, due, dispatched, c.date() if c else None,
                 location, tpl, late, excluded, completed, outstanding if good else 0)


# --------------------------------------------------------------------------
# Sheet layouts
# --------------------------------------------------------------------------

@dataclass
class Layout:
    key: str
    label: str
    spreadsheet_id: str
    tab: str
    sheet_id: int
    first_data_row: int
    last_col: int            # last column we may write (0-based)
    dispatched_col: int
    status_col: int
    notes_col: int | None    # None: no notes column we may write
    holiday_col: int
    open_status: str
    late_status: str
    date_pattern: str


def layouts(cfg: dict) -> tuple[Layout, Layout]:
    f, b = cfg["fault_log"], cfg["btk_register"]
    fs, bs = f.get("statuses", {}), b.get("statuses", {})
    fl = Layout("fault_log", "Order Fault Log", f["spreadsheet_id"], f["tab"], f["sheet_id"],
                f["first_data_row"], last_col=11, dispatched_col=4, status_col=10, notes_col=11,
                holiday_col=13, open_status=fs.get("open", FL_OPEN), late_status=fs.get("late", FL_LATE),
                date_pattern="ddd d mmm yyyy")
    # BTK column J is "BTK comment": never written, so there is no notes column.
    bt = Layout("btk", "BTK Service Register", b["spreadsheet_id"], b["tab"], b["sheet_id"],
                b["first_data_row"], last_col=9, dispatched_col=5, status_col=8, notes_col=None,
                holiday_col=11, open_status=bs.get("open", BTK_OPEN), late_status=bs.get("late", BTK_LATE),
                date_pattern="d mmm yyyy")
    return fl, bt


def new_row_cells(L: Layout, n: int, o: Order) -> list[dict | None]:
    """userEnteredValue for columns A..last_col of new row n (None = leave blank)."""
    date = {"numberValue": date_to_serial(o.order_date)}
    time = {"numberValue": time_to_fraction(o.placed.time())}
    disp = {"numberValue": date_to_serial(o.dispatched)} if o.dispatched else None
    if L.key == "fault_log":
        h = "$N$2:$N"
        return [
            {"numberValue": o.number},                                            # A
            date,                                                                 # B
            time,                                                                 # C
            {"formulaValue": f"=WORKDAY(B{n},2,{h})"},                            # D
            disp,                                                                 # E
            {"formulaValue": f'=IF(E{n}="","",NETWORKDAYS(B{n}+1,E{n},{h}))'},    # F
            {"formulaValue": f'=IF(E{n}="","",F{n}-2)'},                          # G
            {"stringValue": o.tpl or ""},                                         # H
            {"formulaValue": f'=IF(AND(WORKDAY(B{n}-1,1,{h})=B{n},C{n}<IF(H{n}="Precision",'
                             f'TIME(6,0,0),TIME(12,0,0))),B{n},WORKDAY(B{n},1,{h}))'},  # I
            {"formulaValue": f'=IF(E{n}="","",NETWORKDAYS(I{n},E{n},{h})-1)'},     # J
            {"stringValue": L.late_status if o.dispatched else L.open_status},    # K
            None,                                                                 # L notes: people only
        ]
    h = "$L$9:$L"
    return [
        {"stringValue": f"#{o.number}"},                                          # A
        date,                                                                     # B
        time,                                                                     # C
        {"formulaValue": f"=IF(AND(WORKDAY(B{n}-1,1,{h})=B{n},C{n}<TIME(12,0,0)),"
                         f"B{n},WORKDAY(B{n},1,{h}))"},                           # D
        {"formulaValue": f"=WORKDAY(B{n},2,{h})"},                                # E
        disp,                                                                     # F
        {"formulaValue": f'=IF(F{n}="","",NETWORKDAYS(D{n},F{n},{h})-1)'},        # G
        {"formulaValue": f'=IF(F{n}="","",NETWORKDAYS(B{n}+1,F{n},{h})-2)'},      # H
        {"stringValue": L.late_status if o.dispatched else L.open_status},      # I
        None,                                                                     # J BTK comment
    ]


# --------------------------------------------------------------------------
# Sheet state and planning
# --------------------------------------------------------------------------

@dataclass
class SheetState:
    layout: Layout
    row_count: int
    rows: dict[int, list]        # sheet row -> displayed values
    by_order: dict[int, int]
    last_data_row: int
    holidays: set[dt.date]
    a2: str | None


def build_state(L: Layout, values: list[list], row_count: int) -> SheetState:
    """values: get_values result for the whole tab starting at A1."""
    rows, by_order, holidays = {}, {}, set()
    last = L.first_data_row - 1
    width = max(L.holiday_col + 1, L.last_col + 1)
    for i in range(L.first_data_row - 1, len(values)):
        n = i + 1
        r = [("" if v is None else v) for v in values[i]] + [""] * width
        r = r[:width]
        rows[n] = r
        if str(r[L.holiday_col]).strip():
            try:
                holidays.add(parse_sheet_date(r[L.holiday_col]))
            except SweepError as e:
                raise SweepError(f"{L.label}: holiday cell {col_letter(L.holiday_col)}{n}: {e}") from None
        if any(str(v).strip() for v in r[: L.last_col + 1]):
            last = n
        num = order_number(r[0])
        if num is not None:
            if num in by_order:
                raise SweepError(f"{L.label}: order {num} is in rows {by_order[num]} and {n}; "
                                 "remove the duplicate before the next run")
            by_order[num] = n
    a2 = None
    if L.key == "btk" and len(values) > 1 and values[1]:
        a2 = str(values[1][0])
    return SheetState(L, row_count, rows, by_order, last, holidays, a2)


@dataclass
class CellChange:
    row: int
    col: int
    old: object
    new: object       # date or str
    why: str


@dataclass
class Plan:
    layout: Layout
    appends: list[Order] = field(default_factory=list)
    changes: list[CellChange] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def _norm_dash(s: str) -> str:
    return re.sub(r"\s*[-–—]\s*", " - ", str(s).strip())


def status_key(s) -> str:
    """Status text for comparing: ignores a leading dropdown number such as
    "1. " or "4) ", the kind of dash, spacing and case. So "Open – overdue"
    and "1. Open - overdue" match, but "2. Raised with 3PL" never matches open."""
    return _norm_dash(re.sub(r"^\s*\d+\s*[.)]\s*", "", str(s))).casefold()


def plan_sheet(st: SheetState, orders: dict[int, Order], today: dt.date) -> Plan:
    L, p = st.layout, Plan(st.layout)
    for num, row in sorted(st.by_order.items(), key=lambda kv: kv[1]):
        o = orders.get(num)
        if o is None:
            continue
        r = st.rows[row]
        if o.cancelled:
            if L.notes_col is not None and _blank(r[L.notes_col]):
                p.changes.append(CellChange(row, L.notes_col, "", f"Cancelled in Shopify {short_date(o.cancelled)}",
                                            f"#{num} cancelled"))
            elif L.notes_col is None:
                p.notes.append(f"#{num} (row {row}) was cancelled in Shopify on {short_date(o.cancelled)}; "
                               "left unchanged (this tab has no notes column we may write).")
            continue
        done = o.completed or o.dispatched
        if _blank(r[L.dispatched_col]) and o.dispatched:
            # The row was open last time, so record when the whole order went
            # out (the last parcel), not the first parcel.
            p.changes.append(CellChange(row, L.dispatched_col, "", done, f"#{num} now fulfilled"))
            if done <= o.due:
                p.notes.append(f"#{num} (row {row}) dispatched {short_date(done)}, on time by our "
                               "calculation; Status not changed.")
            elif status_key(r[L.status_col]) == status_key(L.open_status):
                p.changes.append(CellChange(row, L.status_col, r[L.status_col], L.late_status,
                                            f"#{num} now dispatched"))
    for num in sorted(orders):
        o = orders[num]
        if num in st.by_order or o.excluded or not o.late:
            continue
        if L.key == "btk" and o.tpl != "BTK":
            continue
        p.appends.append(o)
    if L.key == "btk" and st.a2 is not None:
        pat = re.compile(r"(status as at )\d{1,2} [A-Za-z]+ \d{4}", re.IGNORECASE)
        if not pat.search(st.a2):
            p.notes.append("A2 has no 'status as at <date>' text; left unchanged.")
        else:
            new = pat.sub(lambda m: m.group(1) + long_date(today), st.a2, count=1)
            if new != st.a2:
                p.changes.append(CellChange(2, 0, st.a2, new, "status date"))
    return p


def col_letter(i: int) -> str:
    return chr(ord("A") + i)


def build_requests(st: SheetState, plan: Plan) -> tuple[list[dict], list[dict]]:
    """(forward, rollback) spreadsheets.batchUpdate requests for one spreadsheet."""
    L, sid = st.layout, st.layout.sheet_id
    width = L.last_col + 1
    fwd, back = [], []
    if plan.appends:
        start = st.last_data_row + 1
        end = start + len(plan.appends) - 1
        if end > st.row_count:
            extra = end - st.row_count
            fwd.append({"appendDimension": {"sheetId": sid, "dimension": "ROWS", "length": extra}})
            back.append({"deleteDimension": {"range": {"sheetId": sid, "dimension": "ROWS",
                                                       "startIndex": st.row_count,
                                                       "endIndex": st.row_count + extra}}})
        dest = {"sheetId": sid, "startRowIndex": start - 1, "endRowIndex": end,
                "startColumnIndex": 0, "endColumnIndex": width}
        if st.last_data_row >= L.first_data_row:
            src = {"sheetId": sid, "startRowIndex": st.last_data_row - 1, "endRowIndex": st.last_data_row,
                   "startColumnIndex": 0, "endColumnIndex": width}
            for pt in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION"):
                fwd.append({"copyPaste": {"source": src, "destination": dest, "pasteType": pt}})
        rows = [{"values": [({"userEnteredValue": v} if v else {}) for v in new_row_cells(L, start + i, o)]}
                for i, o in enumerate(plan.appends)]
        fwd.append({"updateCells": {"range": dest, "rows": rows, "fields": "userEnteredValue"}})
        back.append({"updateCells": {"range": dest, "rows": [{"values": [{}] * width}] * len(rows),
                                     "fields": "userEnteredValue"}})
    for ch in plan.changes:
        rng = {"sheetId": sid, "startRowIndex": ch.row - 1, "endRowIndex": ch.row,
               "startColumnIndex": ch.col, "endColumnIndex": ch.col + 1}
        if isinstance(ch.new, dt.date):
            cell = {"userEnteredValue": {"numberValue": date_to_serial(ch.new)},
                    "userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": L.date_pattern}}}
            fields = "userEnteredValue,userEnteredFormat.numberFormat"
        else:
            cell = {"userEnteredValue": {"stringValue": ch.new}}
            fields = "userEnteredValue"
        fwd.append({"updateCells": {"range": rng, "rows": [{"values": [cell]}], "fields": fields}})
        old = {"userEnteredValue": {"stringValue": str(ch.old)}} if not _blank(ch.old) else {}
        back.append({"updateCells": {"range": rng, "rows": [{"values": [old]}], "fields": "userEnteredValue"}})
    back.reverse()
    return fwd, back


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check_errors(label: str, values: list[list]) -> list[str]:
    out = []
    for i, r in enumerate(values):
        for j, v in enumerate(r):
            if isinstance(v, str) and v.strip() in ERROR_MARKERS:
                out.append(f"{label}: {col_letter(j)}{i + 1} shows {v.strip()}")
    return out


def check_btk_counts(values: list[list], L: Layout) -> list[str]:
    data = values[L.first_data_row - 1:]
    st = lambda r: r[L.status_col] if len(r) > L.status_col else ""  # noqa: E731
    want = {4: sum(st(r) == BTK_LATE for r in data),
            5: sum(st(r) == BTK_OPEN for r in data),
            6: sum(1 for r in data if r and str(r[0]).strip())}
    names = {4: "Orders dispatched late", 5: "Not yet dispatched", 6: "Total"}
    out = []
    for row, n in want.items():
        got = values[row - 1][1] if len(values) >= row and len(values[row - 1]) > 1 else ""
        if str(got).strip() != str(n):
            out.append(f"BTK Service Register: B{row} ({names[row]}) shows {got!r} but the rows give {n}")
    return out


def check_never_mention(label: str, payload, terms: list[str]) -> list[str]:
    """The BTK Service Register is shared with BTK: it must never name another
    3PL. Scans any cell values or request JSON for the configured terms."""
    text = json.dumps(payload, ensure_ascii=False).lower()
    return [f"{label}: contains '{t}', which must never appear in the sheet shared with BTK"
            for t in terms if t.lower() in text]


def sheet_problems(L: Layout, values: list[list], never: list[str] | None = None) -> list[str]:
    p = check_errors(L.label, values)
    if L.key == "btk":
        p += check_btk_counts(values, L)
        p += check_never_mention(L.label, values, never or [])
    return p


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def describe(plan: Plan, st: SheetState, cutoffs: dict[str, dt.time]) -> list[str]:
    L = plan.layout
    out = [f"== {L.label} / {L.tab} =="]
    if plan.appends:
        start = st.last_data_row + 1
        out.append(f"Add {len(plan.appends)} row(s) from row {start}:")
        for i, o in enumerate(plan.appends):
            tpl_for_cutoff = "BTK" if L.key == "btk" else o.tpl
            cut = cutoffs.get(tpl_for_cutoff or "", cutoffs["BTK"])
            rec = received_by_3pl(o.placed, cut, st.holidays)
            if o.dispatched:
                disp = (f"dispatched {short_date(o.dispatched)} "
                        f"(+{networkdays(o.order_date + dt.timedelta(days=1), o.dispatched, st.holidays) - 2} bd late, "
                        f"{networkdays(rec, o.dispatched, st.holidays) - 1} bd after cut-off)")
            elif o.outstanding:
                disp = f"part dispatched, {o.outstanding} unit(s) still to ship"
            else:
                disp = "not dispatched"
            tpl = f" | {o.tpl}" if L.key == "fault_log" else ""
            status = L.late_status if o.dispatched else L.open_status
            out.append(f"  row {start + i}: #{o.number} | {short_date(o.order_date)} {hhmm(o.placed.time())}"
                       f" | received {short_date(rec)} | due {short_date(o.due)} | {disp}{tpl} | {status}")
    else:
        out.append("Add: none")
    if plan.changes:
        out.append(f"Change {len(plan.changes)} cell(s):")
        for ch in plan.changes:
            old = "(blank)" if _blank(ch.old) else ch.old
            new = short_date(ch.new) if isinstance(ch.new, dt.date) else ch.new
            if ch.row == 2 and ch.col == 0:
                m_old = re.search(r"status as at [^.,]*", str(old), re.I)
                m_new = re.search(r"status as at [^.,]*", str(new), re.I)
                old, new = (m_old.group(0) if m_old else old), (m_new.group(0) if m_new else new)
            out.append(f"  {col_letter(ch.col)}{ch.row}: {old} -> {new}   ({ch.why})")
    else:
        out.append("Change: none")
    out += [f"  note: {n}" for n in plan.notes]
    return out


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def load_cfg() -> dict:
    return json.loads((HERE / "config.json").read_text())


def melb_today(cfg: dict, override: str | None) -> dt.date:
    if override:
        return dt.date.fromisoformat(override)
    return dt.datetime.now(ZoneInfo(cfg["timezone"])).date()


def read_values(name: str) -> list[list]:
    path = WORK / name
    if not path.exists():
        raise SweepError(f"Missing {path.relative_to(HERE.parent)}")
    data = json.loads(path.read_text())
    return data["values"] if isinstance(data, dict) else data


def cmd_should_run(cfg, args) -> int:
    today = melb_today(cfg, args.today)
    sc = cfg["schedule"]
    gap = (today - dt.date.fromisoformat(sc["anchor_date"])).days
    print("RUN" if gap >= 0 and gap % sc["every_n_days"] == 0 else "SKIP", today)
    return 0


def _states(cfg) -> tuple[SheetState, SheetState]:
    meta = json.loads((WORK / "meta.json").read_text()) if (WORK / "meta.json").exists() else {}
    fl, bt = layouts(cfg)
    return (build_state(fl, read_values("fault_log.json"), meta.get("fault_log", {}).get("row_count", 10**6)),
            build_state(bt, read_values("btk.json"), meta.get("btk", {}).get("row_count", 10**6)))


def cmd_stale(cfg, args) -> int:
    orders = load_orders(WORK / "orders.txt")
    missing = set()
    for st in _states(cfg):
        for num, row in st.by_order.items():
            if _blank(st.rows[row][st.layout.dispatched_col]) and num not in orders:
                missing.add(num)
    if missing:
        print(" OR ".join(f"name:#{n}" for n in sorted(missing)))
    else:
        print("NONE")
    return 0


def cmd_plan(cfg, args) -> int:
    tz = ZoneInfo(cfg["timezone"])
    today = melb_today(cfg, args.today)
    states = _states(cfg)
    pre = []
    never = cfg["btk_register"].get("never_mention", [])
    for st, f in zip(states, ("fault_log.json", "btk.json")):
        pre += sheet_problems(st.layout, read_values(f), never)
    if pre:
        raise SweepError("The sheets already show problems, so nothing will be written:\n  " + "\n  ".join(pre))

    raw = load_orders(WORK / "orders.txt")
    mapping = cfg["location_to_3pl"]
    cutoffs = {k: dt.time.fromisoformat(v) for k, v in cfg["cutoffs"].items()}
    per_sheet = [{n: evaluate(r, st.holidays, today, tz, mapping, cfg["promise_business_days"])
                  for n, r in raw.items()} for st in states]
    plans = [plan_sheet(st, o, today) for st, o in zip(states, per_sheet)]

    ev = per_sheet[0]
    window_start = today - dt.timedelta(days=cfg["lookback_days"] + 1)
    in_window = [o for o in ev.values() if o.order_date >= window_start]
    excl: dict[str, int] = {}
    for o in in_window:
        if o.excluded:
            excl[o.excluded] = excl.get(o.excluded, 0) + 1
    late_by: dict[str, int] = {}
    for o in in_window:
        if o.late and not o.excluded:
            late_by[o.tpl or "(no location)"] = late_by.get(o.tpl or "(no location)", 0) + 1
    new_by: dict[str, int] = {}
    for o in plans[0].appends:
        new_by[o.tpl or "(no location)"] = new_by.get(o.tpl or "(no location)", 0) + 1
    unmapped = sorted({o.location for o in ev.values()
                       if o.location and not o.excluded and o.late and not map_3pl(o.location, mapping)[1]})
    fmt = lambda d: ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "none"  # noqa: E731

    lines = [f"Late dispatch sweep, {today:%a %d %b %Y} (Melbourne), mode: {cfg['mode']}"]
    if states[0].holidays != states[1].holidays:
        lines.append("WARNING: the two sheets list different holidays; each sheet uses its own.")
    if not any(h >= today for h in states[0].holidays | states[1].holidays):
        lines.append("WARNING: no public holidays on or after today are listed in the sheets.")
    lines += [
        f"Orders checked: {len(in_window)} placed in the last {cfg['lookback_days']} days"
        + (f" (+{len(ev) - len(in_window)} older orders looked up for open rows)" if len(ev) > len(in_window) else ""),
        f"Excluded: {fmt(excl)}",
        f"Late orders in the window by 3PL: {fmt(late_by)}",
        f"New late orders by 3PL: {fmt(new_by)}",
        f"New rows: Fault Log {len(plans[0].appends)}, BTK {len(plans[1].appends)}",
        f"Cells to update: Fault Log {len(plans[0].changes)}, BTK {len(plans[1].changes)}",
        f"Unmapped locations: {', '.join(unmapped) or 'none'}",
        "",
    ]
    for st, p in zip(states, plans):
        lines += describe(p, st, cutoffs) + [""]

    WORK.mkdir(exist_ok=True)
    meta = json.loads((WORK / "meta.json").read_text()) if (WORK / "meta.json").exists() else {}
    for st, p, key in zip(states, plans, ("fault_log", "btk")):
        fwd, back = build_requests(st, p)
        if key == "btk":
            bad = check_never_mention("BTK Service Register writes", fwd, never)
            if bad:
                raise SweepError("Refusing to write: " + "; ".join(bad))
        body = {"spreadsheetId": st.layout.spreadsheet_id, "requests": fwd}
        rev = meta.get(key, {}).get("revision_id")
        if rev:
            body["writeControl"] = {"requiredRevisionId": rev}
        (WORK / f"{key}_requests.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
        (WORK / f"{key}_rollback.json").write_text(json.dumps(
            {"spreadsheetId": st.layout.spreadsheet_id, "requests": back}, ensure_ascii=False, indent=1))
    lines.append("Writes needed: " + (", ".join(
        k for k, p in (("fault_log", plans[0]), ("btk", plans[1])) if p.appends or p.changes) or "none"))
    text = "\n".join(lines)
    (WORK / "summary.txt").write_text(text + "\n")
    print(text)
    return 0


def cmd_verify(cfg, args) -> int:
    fl, bt = layouts(cfg)
    problems = []
    never = cfg["btk_register"].get("never_mention", [])
    for L, f in ((fl, "fault_log_after.json"), (bt, "btk_after.json")):
        problems += sheet_problems(L, read_values(f), never)
    text = ("Verification: FAILED\n  " + "\n  ".join(problems)) if problems else \
        "Verification: no #REF!/#VALUE!/#NAME?/#N/A; BTK B4:B6 match the rows."
    (WORK / "verify.txt").write_text(text + "\n")
    print(text)
    return 1 if problems else 0


def cmd_finish(cfg, args) -> int:
    today = melb_today(cfg, args.today)
    parts = [f"=== Run at {dt.datetime.now(ZoneInfo(cfg['timezone'])):%Y-%m-%d %H:%M} Melbourne: {args.status.upper()} ==="]
    for f in ("summary.txt", "verify.txt"):
        if (WORK / f).exists():
            parts.append((WORK / f).read_text().rstrip())
    parts.append(f"Errors: {args.note or 'none'}")
    entry = "\n".join(parts) + "\n\n"
    logs = HERE / "logs"
    logs.mkdir(exist_ok=True)
    name = f"{today:%Y-%m-%d}.txt"
    with open(logs / name, "a") as fh:
        fh.write(entry)
    print(entry)
    if args.push:
        print(push_log(cfg["logs_branch"], name, entry))
    return 0


def push_log(branch: str, name: str, entry: str) -> str:
    """Append the entry to late-dispatch-sweep/logs/<name> on the logs branch and push.
    Uses a temporary worktree so the checked-out branch is left alone."""
    repo = HERE.parent
    tmp = Path(tempfile.mkdtemp())
    wt = tmp / "wt"
    git = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)  # noqa: E731
    try:
        if git("fetch", "origin", branch).returncode == 0:
            r = git("worktree", "add", "-B", branch, str(wt), f"origin/{branch}")
        else:
            r = git("worktree", "add", "--detach", str(wt))
            if r.returncode == 0:
                git("checkout", "--orphan", branch, cwd=wt)
                git("rm", "-rfq", ".", cwd=wt)
        if r.returncode != 0:
            return f"Log not pushed (worktree): {r.stderr.strip()}"
        dest = wt / "late-dispatch-sweep" / "logs" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "a") as fh:
            fh.write(entry)
        git("add", str(dest.relative_to(wt)), cwd=wt)
        c = git("commit", "-qm", f"Late dispatch sweep log {name[:-4]}", cwd=wt)
        if c.returncode != 0:
            return f"Log not pushed (commit): {c.stderr.strip() or c.stdout.strip()}"
        for wait in (0, 2, 4, 8, 16):
            if wait:
                import time
                time.sleep(wait)
            p = git("push", "-u", "origin", branch, cwd=wt)
            if p.returncode == 0:
                return f"Log pushed to branch {branch}"
        return f"Log not pushed (push): {p.stderr.strip()}"
    finally:
        git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["should-run", "stale", "plan", "verify", "finish"])
    ap.add_argument("--today", help="override today's Melbourne date (YYYY-MM-DD)")
    ap.add_argument("--status", default="ok", help="finish: ok, dry-run or failed")
    ap.add_argument("--note", default="", help="finish: error text to record")
    ap.add_argument("--push", action="store_true", help="finish: push the log to the logs branch")
    args = ap.parse_args(argv)
    cfg = load_cfg()
    try:
        return {"should-run": cmd_should_run, "stale": cmd_stale, "plan": cmd_plan,
                "verify": cmd_verify, "finish": cmd_finish}[args.command](cfg, args)
    except SweepError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
