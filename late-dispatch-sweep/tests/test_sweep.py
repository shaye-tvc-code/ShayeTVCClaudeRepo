import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sweep as s  # noqa: E402

TZ = ZoneInfo("Australia/Melbourne")
HOL = {dt.date(2026, 11, 3)}           # Melbourne Cup, as in both sheets
MAP = {"BTK": "BTK", "Precision": "Precision", "New3PL": "Precision"}
D = dt.date
CFG = json.loads((Path(s.__file__).parent / "config.json").read_text())
FL, BT = s.layouts(CFG)


def line(name, created, fulfilled="-", loc="BTK", assigned="BTK", cancelled="-", tags="-", pe="Customer", ship="Y"):
    ful = "-" if fulfilled == "-" else f"{fulfilled}@SUCCESS@{loc}"
    return f"{name} | {created} | {cancelled} | {tags} | {pe} | {ship} | {ful} | {assigned}"


def ev(ln, today=D(2026, 11, 8)):
    return s.evaluate(s.parse_order_line(ln), HOL, today, TZ, MAP, 2)


def orders(*lines, today=D(2026, 11, 8)):
    return {s.order_number(l.split(" | ")[0]): ev(l, today) for l in lines}


# ---- dates ---------------------------------------------------------------

def test_workday_matches_live_sheet():
    assert s.workday(D(2026, 10, 22), 2, HOL) == D(2026, 10, 26)
    assert s.workday(D(2026, 11, 1), 2, HOL) == D(2026, 11, 4)     # Sunday, skips Cup day
    assert s.workday(D(2026, 11, 3), 2, HOL) == D(2026, 11, 5)     # ordered on the holiday


def test_received_by_3pl_matches_live_sheet():
    noon, six = dt.time(12), dt.time(6)
    # #4935 Thu 22 Oct 3:39pm -> Fri 23 Oct; #4943 Fri 23 Oct 11:59am -> same day
    assert s.received_by_3pl(dt.datetime(2026, 10, 22, 15, 39), noon, HOL) == D(2026, 10, 23)
    assert s.received_by_3pl(dt.datetime(2026, 10, 23, 11, 59), noon, HOL) == D(2026, 10, 23)
    assert s.received_by_3pl(dt.datetime(2026, 10, 26, 12, 0), noon, HOL) == D(2026, 10, 27)   # at cut-off = next day
    assert s.received_by_3pl(dt.datetime(2026, 11, 1, 9, 0), noon, HOL) == D(2026, 11, 2)      # Sunday
    assert s.received_by_3pl(dt.datetime(2026, 11, 3, 9, 0), noon, HOL) == D(2026, 11, 4)      # holiday
    assert s.received_by_3pl(dt.datetime(2026, 11, 4, 5, 59), six, HOL) == D(2026, 11, 4)
    assert s.received_by_3pl(dt.datetime(2026, 11, 4, 6, 0), six, HOL) == D(2026, 11, 5)


def test_serials_and_time_fraction_match_sheet():
    assert s.date_to_serial(D(2026, 11, 4)) == 46330
    assert s.time_to_fraction(dt.time(12, 37)) == pytest.approx(0.5256944444444445)
    assert s.time_to_fraction(dt.time(18, 27)) == pytest.approx(0.76875)


def test_parse_sheet_dates():
    assert s.parse_sheet_date("Tue 3 Nov 2026") == D(2026, 11, 3)
    assert s.parse_sheet_date("3 Nov 2026") == D(2026, 11, 3)
    assert s.parse_sheet_date("") is None


# ---- orders --------------------------------------------------------------

def test_order_line_parsing_and_errors():
    o = s.parse_order_line("#5 | 2026-11-01T00:00:00Z | - | Loyalty,b2b | PurchasingCompany | N | "
                           "2026-11-02T00:00:00Z@SUCCESS@BTK Ravenhall;2026-11-03T00:00:00Z@CANCELLED@X | -")
    assert o["tags"] == ["Loyalty", "b2b"] and not o["shippable"]
    assert o["fulfillments"][0]["location"] == "BTK Ravenhall" and o["assigned"] is None
    with pytest.raises(s.SweepError):
        s.parse_order_line("#5 | 2026-11-01T00:00:00Z | -")


def test_late_rules():
    o = ev(line("#1", "2026-11-01T22:00:00Z", "2026-11-05T00:00:00Z"))   # Mon 2 -> Thu 5 on time
    assert o.due == D(2026, 11, 5) and not o.late
    assert ev(line("#2", "2026-11-01T22:00:00Z", "2026-11-05T22:00:00Z")).late   # Fri 6
    r = line("#3", "2026-11-03T22:00:00Z")    # Wed 4, due Fri 6
    assert not ev(r, D(2026, 11, 6)).late and ev(r, D(2026, 11, 7)).late


def test_location_and_mapping():
    o = ev(line("#1", "2026-11-03T22:00:00Z", assigned="New3PL"))
    assert o.location == "New3PL" and o.tpl == "Precision"
    assert ev(line("#2", "2026-11-03T22:00:00Z", assigned="Warehouse")).tpl == "Warehouse"
    raw = s.parse_order_line("#3 | 2026-11-01T22:00:00Z | - | - | Customer | Y | "
                             "2026-11-07T00:00:00Z@SUCCESS@BTK;2026-11-06T00:00:00Z@SUCCESS@New3PL;"
                             "2026-11-02T00:00:00Z@CANCELLED@BTK | BTK")
    o = s.evaluate(raw, HOL, D(2026, 11, 8), TZ, MAP, 2)
    assert o.dispatched == D(2026, 11, 6) and o.tpl == "Precision"


def test_exclusions():
    assert ev(line("#1", "2026-11-01T00:00:00Z", cancelled="2026-11-02T00:00:00Z")).excluded == "cancelled"
    assert ev(line("#2", "2026-11-01T00:00:00Z", ship="N")).excluded == "no shippable items"
    assert ev(line("#3", "2026-11-01T00:00:00Z", tags="Test")).excluded == "test tag"
    assert ev(line("#4", "2026-11-01T00:00:00Z", tags="B2B")).excluded == "B2B"
    assert ev(line("#5", "2026-11-01T00:00:00Z", pe="PurchasingCompany")).excluded == "B2B"
    assert ev(line("#6", "2026-11-01T00:00:00Z", tags="testimonial")).excluded is None


# ---- sheets ----------------------------------------------------------------

def fl_values(rows):
    head = [["Order number", "Order date", "Order time", "Due by", "Dispatched", "F", "G", "3PL",
             "Received", "J", "Status", "Notes", "", "VIC public holidays"]]
    rows = [list(r) for r in rows]
    rows[0] = rows[0] + [""] * (13 - len(rows[0])) + ["Tue 3 Nov 2026"]
    return head + rows


A2 = ("Online orders placed 11 October to 7 November 2026, status as at 8 November 2026. "
      "BTK's scope assumptions set a 12:00pm cut-off.")


def btk_values(rows, a2=A2, counts=None):
    data = [list(r) for r in rows]
    data[0] = data[0] + [""] * (11 - len(data[0])) + ["3 Nov 2026"]
    late = sum(r[8] == "Dispatched late" for r in data)
    opn = sum(r[8] == "Not yet dispatched" for r in data)
    counts = counts or (late, opn, len(data))
    return ([["title"], [a2], [], ["Orders dispatched late", str(counts[0])],
             ["Not yet dispatched", str(counts[1])], ["Total", str(counts[2])], [], ["Order"]] + data)


def fl_row(num, disp="", status="Dispatched late", notes=""):
    return [str(num), "Thu 29 Oct 2026", "2:36 pm", "x", disp, "", "", "BTK", "x", "", status, notes]


def btk_row(num, disp="", status="Dispatched late", comment=""):
    return [f"#{num}", "29 Oct 2026", "2:36 pm", "x", "x", disp, "", "", status, comment]


def test_build_state():
    st = s.build_state(FL, fl_values([fl_row(5008, status="Open – overdue")]), 498)
    assert st.holidays == HOL and st.by_order == {5008: 2} and st.last_data_row == 2
    bt = s.build_state(BT, btk_values([btk_row(5008, status="Not yet dispatched")]), 500)
    assert bt.by_order == {5008: 9} and bt.a2 == A2 and bt.holidays == HOL


def test_duplicate_rows_stop_the_run():
    with pytest.raises(s.SweepError):
        s.build_state(FL, fl_values([fl_row(5008), fl_row(5008)]), 498)


def test_fault_log_fill_dispatched_and_protected_status():
    st = s.build_state(FL, fl_values([fl_row(5008, status="Open – overdue"),
                                      fl_row(5033, status="Raised with 3PL"),
                                      fl_row(5034, disp="Fri 6 Nov 2026", notes="keep")]), 498)
    o = orders(line("#5008", "2026-10-29T01:00:00Z", "2026-11-06T01:00:00Z"),
               line("#5033", "2026-10-31T23:00:00Z", "2026-11-06T01:00:00Z"),
               line("#5034", "2026-10-31T23:00:00Z", "2026-11-06T01:00:00Z"))
    p = s.plan_sheet(st, o, D(2026, 11, 8))
    assert {(c.row, c.col): c.new for c in p.changes} == {
        (2, 4): D(2026, 11, 6), (2, 10): "4. Dispatched late", (3, 4): D(2026, 11, 6)}
    assert p.appends == []


def test_cancelled_notes():
    st = s.build_state(FL, fl_values([fl_row(5008, status="Open – overdue"),
                                      fl_row(5009, status="Open – overdue", notes="keep me")]), 498)
    o = orders(line("#5008", "2026-10-29T01:00:00Z", cancelled="2026-11-05T01:00:00Z"),
               line("#5009", "2026-10-29T01:00:00Z", cancelled="2026-11-05T01:00:00Z"))
    p = s.plan_sheet(st, o, D(2026, 11, 8))
    assert [(c.row, c.col, c.new) for c in p.changes] == [(2, 11, "Cancelled in Shopify 5 Nov 2026")]
    bt = s.build_state(BT, btk_values([btk_row(5008, status="Not yet dispatched")]), 500)
    pb = s.plan_sheet(bt, o, D(2026, 11, 8))
    assert all(c.col != 9 for c in pb.changes) and any("cancelled" in n for n in pb.notes)


def test_appends_all_3pls_vs_btk_only():
    o = orders(line("#5200", "2026-11-01T22:00:00Z", "2026-11-06T01:00:00Z"),
               line("#5201", "2026-11-01T22:00:00Z", "2026-11-06T01:00:00Z", loc="New3PL"),
               line("#5202", "2026-11-01T22:00:00Z", "2026-11-03T01:00:00Z"),            # on time
               line("#5203", "2026-11-01T22:00:00Z", "2026-11-06T01:00:00Z", tags="test"))
    fl = s.plan_sheet(s.build_state(FL, fl_values([fl_row(5008, disp="x")]), 498), o, D(2026, 11, 8))
    assert [a.number for a in fl.appends] == [5200, 5201]
    bt = s.plan_sheet(s.build_state(BT, btk_values([btk_row(5008, disp="x")]), 500), o, D(2026, 11, 8))
    assert [a.number for a in bt.appends] == [5200]


def test_btk_status_a2_and_comment():
    st = s.build_state(BT, btk_values([btk_row(5080, status="Not yet dispatched", comment="BTK: Monday")]), 500)
    o = orders(line("#5080", "2026-11-03T23:00:00Z", "2026-11-08T23:00:00Z"), today=D(2026, 11, 10))
    p = s.plan_sheet(st, o, D(2026, 11, 10))
    got = {(c.row, c.col): c.new for c in p.changes}
    assert got[(9, 5)] == D(2026, 11, 9) and got[(9, 8)] == "Dispatched late"
    assert "status as at 10 November 2026. BTK's" in got[(2, 0)]
    assert got[(2, 0)].startswith("Online orders placed 11 October to 7 November 2026, ")
    assert all(c.col != 9 for c in p.changes)


def test_new_row_formulas_match_live_sheets():
    o = orders(line("#5087", "2026-11-04T08:17:07Z", assigned="New3PL"))[5087]
    f = s.new_row_cells(FL, 55, o)
    assert f[0] == {"numberValue": 5087} and f[1] == {"numberValue": 46330}
    assert f[2]["numberValue"] == pytest.approx(s.time_to_fraction(dt.time(19, 17)))
    assert f[3] == {"formulaValue": "=WORKDAY(B55,2,$N$2:$N)"}
    assert f[4] is None and f[11] is None
    assert f[5] == {"formulaValue": '=IF(E55="","",NETWORKDAYS(B55+1,E55,$N$2:$N))'}
    assert f[6] == {"formulaValue": '=IF(E55="","",F55-2)'}
    assert f[7] == {"stringValue": "Precision"}
    assert f[8] == {"formulaValue": '=IF(AND(WORKDAY(B55-1,1,$N$2:$N)=B55,C55<IF(H55="Precision",'
                                    'TIME(6,0,0),TIME(12,0,0))),B55,WORKDAY(B55,1,$N$2:$N))'}
    assert f[9] == {"formulaValue": '=IF(E55="","",NETWORKDAYS(I55,E55,$N$2:$N)-1)'}
    assert f[10] == {"stringValue": "1. Open – overdue"}
    b = s.new_row_cells(BT, 61, o)
    assert b[0] == {"stringValue": "#5087"}
    assert b[3] == {"formulaValue": "=IF(AND(WORKDAY(B61-1,1,$L$9:$L)=B61,C61<TIME(12,0,0)),B61,WORKDAY(B61,1,$L$9:$L))"}
    assert b[4] == {"formulaValue": "=WORKDAY(B61,2,$L$9:$L)"}
    assert b[6] == {"formulaValue": '=IF(F61="","",NETWORKDAYS(D61,F61,$L$9:$L)-1)'}
    assert b[7] == {"formulaValue": '=IF(F61="","",NETWORKDAYS(B61+1,F61,$L$9:$L)-2)'}
    assert b[8] == {"stringValue": "Not yet dispatched"} and b[5] is None and b[9] is None


def test_requests_and_rollback():
    st = s.build_state(FL, fl_values([fl_row(5008, status="Open – overdue")]), 498)
    o = orders(line("#5200", "2026-11-01T22:00:00Z", "2026-11-06T01:00:00Z"),
               line("#5008", "2026-10-29T01:00:00Z", "2026-11-06T01:00:00Z"))
    fwd, back = s.build_requests(st, s.plan_sheet(st, o, D(2026, 11, 8)))
    cps = [r["copyPaste"] for r in fwd if "copyPaste" in r]
    assert {c["pasteType"] for c in cps} == {"PASTE_FORMAT", "PASTE_DATA_VALIDATION"}
    assert cps[0]["source"]["startRowIndex"] == 1 and cps[0]["destination"]["endColumnIndex"] == 12
    assert len(back) == 3
    assert back[0]["updateCells"]["rows"][0]["values"][0] == {"userEnteredValue": {"stringValue": "Open – overdue"}}


def test_append_grows_grid_when_full():
    st = s.build_state(FL, fl_values([fl_row(5008, disp="x")]), 2)
    o = orders(line("#5200", "2026-11-01T22:00:00Z", "2026-11-06T01:00:00Z"))
    fwd, back = s.build_requests(st, s.plan_sheet(st, o, D(2026, 11, 8)))
    assert fwd[0]["appendDimension"]["length"] == 1 and "deleteDimension" in back[-1]


def test_checks():
    good = btk_values([btk_row(1), btk_row(2, status="Not yet dispatched")])
    assert s.sheet_problems(BT, good) == []
    bad = btk_values([btk_row(1), btk_row(2, status="Not yet dispatched")], counts=(1, 1, 3))
    assert len(s.sheet_problems(BT, bad)) == 1
    err = fl_values([fl_row(1)])
    err[1][3] = "#REF!"
    assert s.sheet_problems(FL, err) == ["Order Fault Log: D2 shows #REF!"]


def test_should_run(capsys):
    s.cmd_should_run(CFG, type("A", (), {"today": "2026-11-10"}))
    s.cmd_should_run(CFG, type("A", (), {"today": "2026-11-11"}))
    s.cmd_should_run(CFG, type("A", (), {"today": "2026-12-01"}))   # 21 days after anchor
    out = capsys.readouterr().out.split("\n")
    assert out[0].startswith("RUN") and out[1].startswith("SKIP") and out[2].startswith("SKIP")


# ---- part-dispatched orders ------------------------------------------------

def test_outstanding_field_parsing():
    o = s.parse_order_line("#7 | 2026-11-01T00:00:00Z | - | - | Customer | Y | - | BTK | 2@New3PL")
    assert o["outstanding"] == 2 and o["outstanding_location"] == "New3PL"
    o = s.parse_order_line("#7 | 2026-11-01T00:00:00Z | - | - | Customer | Y | - | BTK")   # old 8-field line
    assert o["outstanding"] == 0 and o["outstanding_location"] is None
    with pytest.raises(s.SweepError):
        s.parse_order_line("#7 | 2026-11-01T00:00:00Z | - | - | Customer | Y | - | BTK | two@BTK")


def test_part_dispatched_order_counts_as_not_dispatched():
    # Like #5001: placed Wed 28 Oct, one parcel from BTK on Fri 30 Oct (on time),
    # one item moved to New3PL and still waiting on Sun 8 Nov.
    raw = s.parse_order_line("#5001 | 2026-10-27T14:03:48Z | - | - | Customer | Y | "
                             "2026-10-30T01:26:44Z@SUCCESS@BTK | BTK | 1@New3PL")
    o = s.evaluate(raw, HOL, D(2026, 11, 8), TZ, MAP, 2)
    assert o.dispatched is None and o.late and o.outstanding == 1
    assert o.location == "New3PL" and o.tpl == "Precision"
    p = s.plan_sheet(s.build_state(FL, [["h"]], 100), {5001: o}, D(2026, 11, 8))
    assert [a.number for a in p.appends] == [5001]
    assert "part dispatched, 1 unit(s) still to ship" in "\n".join(s.describe(p, s.build_state(FL, [["h"]], 100),
                                                                              {"BTK": dt.time(12), "Precision": dt.time(6)}))


def test_part_dispatched_not_late_yet_is_left_alone():
    raw = s.parse_order_line("#9 | 2026-11-03T22:00:00Z | - | - | Customer | Y | "
                             "2026-11-04T01:00:00Z@SUCCESS@BTK | BTK | 1@BTK")
    o = s.evaluate(raw, HOL, D(2026, 11, 5), TZ, MAP, 2)     # due Fri 6
    assert o.dispatched is None and not o.late


def test_open_row_filled_with_last_parcel_date_when_complete():
    # Row added while part dispatched; now both parcels are out (30 Oct and 10 Nov).
    raw = s.parse_order_line("#5001 | 2026-10-27T14:03:48Z | - | - | Customer | Y | "
                             "2026-10-30T01:26:44Z@SUCCESS@BTK;2026-11-10T01:00:00Z@SUCCESS@New3PL | BTK | -")
    o = s.evaluate(raw, HOL, D(2026, 11, 12), TZ, MAP, 2)
    assert o.dispatched == D(2026, 10, 30) and o.completed == D(2026, 11, 10)
    values = [["h"]] + [[""]] * 0 + [["5001", "", "", "", "", "", "", "Precision", "", "", s.FL_OPEN]]
    st = s.build_state(FL, values, 100)
    p = s.plan_sheet(st, {5001: o}, D(2026, 11, 12))
    filled = [c for c in p.changes if c.col == FL.dispatched_col]
    assert filled and filled[0].new == D(2026, 11, 10)
    assert any(c.col == FL.status_col and c.new == s.FL_LATE for c in p.changes)


def test_fully_dispatched_split_order_unchanged():
    # Existing rule kept for orders with nothing left: first parcel counts.
    raw = s.parse_order_line("#3 | 2026-11-01T22:00:00Z | - | - | Customer | Y | "
                             "2026-11-07T00:00:00Z@SUCCESS@BTK;2026-11-06T00:00:00Z@SUCCESS@New3PL | BTK | -")
    o = s.evaluate(raw, HOL, D(2026, 11, 8), TZ, MAP, 2)
    assert o.dispatched == D(2026, 11, 6) and o.tpl == "Precision" and o.outstanding == 0


# ---- the BTK sheet never names another 3PL --------------------------------

def test_btk_never_mention_guard():
    never = CFG["btk_register"]["never_mention"]
    assert "Precision" in never and "New3PL" in never
    assert s.check_never_mention("BTK", [["#5001", "moved to precision"]], never)
    assert s.check_never_mention("BTK", {"requests": [{"x": "NEW3PL"}]}, never)
    assert not s.check_never_mention("BTK", [["#5001", "Dispatched late", "BTK email 9 Nov"]], never)


def test_btk_rows_never_carry_another_3pl():
    # A Precision order is never added to the BTK sheet, and BTK rows have no 3PL column.
    o = orders(line("#1", "2026-11-01T22:00:00Z", assigned="New3PL"),
               line("#2", "2026-11-01T22:00:00Z", assigned="BTK"))
    st = s.build_state(BT, [["t"], ["status as at 1 Nov 2026"]] + [[]] * 6 + [["h"]], 100)
    p = s.plan_sheet(st, o, D(2026, 11, 8))
    assert [a.number for a in p.appends] == [2]
    fwd, _ = s.build_requests(st, p)
    assert not s.check_never_mention("BTK", fwd, CFG["btk_register"]["never_mention"])


# ---- numbered Fault Log statuses ("1. Open – overdue" ... "5. Closed") ------

def test_config_uses_numbered_fault_log_statuses():
    assert FL.open_status == "1. Open – overdue" and FL.late_status == "4. Dispatched late"
    assert BT.open_status == "Not yet dispatched" and BT.late_status == "Dispatched late"


def test_status_key():
    k = s.status_key
    assert k("1. Open – overdue") == k("Open – overdue") == k("Open - overdue") == k(" 1) open — Overdue ")
    for other in ("2. Raised with 3PL", "3. Customer contacted", "4. Dispatched late", "5. Closed"):
        assert k(other) != k("Open – overdue")


def test_numbered_and_old_open_rows_flip_but_manual_statuses_stay():
    st = s.build_state(FL, fl_values([fl_row(5008, status="1. Open – overdue"),
                                      fl_row(5009, status="Open – overdue"),
                                      fl_row(5010, status="2. Raised with 3PL"),
                                      fl_row(5011, status="3. Customer contacted"),
                                      fl_row(5012, status="5. Closed")]), 498)
    o = orders(*[line(f"#{n}", "2026-10-29T01:00:00Z", "2026-11-06T01:00:00Z") for n in range(5008, 5013)])
    p = s.plan_sheet(st, o, D(2026, 11, 8))
    status = {c.row: c.new for c in p.changes if c.col == FL.status_col}
    assert status == {2: "4. Dispatched late", 3: "4. Dispatched late"}
    assert {c.row for c in p.changes if c.col == FL.dispatched_col} == {2, 3, 4, 5, 6}


def test_new_fault_log_rows_use_dropdown_values():
    o = orders(line("#5300", "2026-11-01T22:00:00Z", "2026-11-06T01:00:00Z"),
               line("#5301", "2026-11-01T22:00:00Z"))
    assert s.new_row_cells(FL, 70, o[5300])[10] == {"stringValue": "4. Dispatched late"}
    assert s.new_row_cells(FL, 71, o[5301])[10] == {"stringValue": "1. Open – overdue"}
    assert s.new_row_cells(BT, 70, o[5301])[8] == {"stringValue": "Not yet dispatched"}
