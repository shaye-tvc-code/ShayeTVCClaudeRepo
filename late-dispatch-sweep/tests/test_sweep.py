import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sweep as s  # noqa: E402

TZ = ZoneInfo("Australia/Melbourne")
HOL = {dt.date(2026, 11, 3)}           # Melbourne Cup, as in both sheets
MAP = {"BTK": "BTK", "Precision": "Precision", "New3PL": "Precision"}
D = dt.date


def raw(name, created, fulfilled=None, loc="BTK", assigned="BTK", **kw):
    r = {"name": name, "createdAt": created, "cancelledAt": kw.get("cancelledAt"),
         "tags": kw.get("tags", []), "displayFulfillmentStatus": "FULFILLED" if fulfilled else "UNFULFILLED",
         "purchasingEntity": {"__typename": kw.get("pe", "Customer")},
         "fulfillments": [{"createdAt": fulfilled, "status": "SUCCESS", "location": {"name": loc}}] if fulfilled else [],
         "fulfillmentOrders": {"nodes": [{"assignedLocation": {"name": assigned}}]},
         "lineItems": {"nodes": [{"requiresShipping": kw.get("ship", True)}]}}
    return r


def ev(r, today=D(2026, 11, 8)):
    return s.evaluate(r, HOL, today, TZ, MAP, 2)


# ---- dates -------------------------------------------------------------

def test_serials_match_sheet():
    assert s.serial_to_date(46329) == D(2026, 11, 3)
    assert s.date_to_serial(D(2026, 11, 6)) == 46332


def test_workday_matches_existing_sheet_rows():
    # Values copied from the live sheets' Due by column.
    assert s.workday(D(2026, 10, 22), 2, HOL) == D(2026, 10, 26)   # Thu -> Mon
    assert s.workday(D(2026, 11, 1), 2, HOL) == D(2026, 11, 4)     # Sun, skips Cup day
    assert s.workday(D(2026, 11, 2), 2, HOL) == D(2026, 11, 5)
    assert s.workday(D(2026, 11, 3), 2, HOL) == D(2026, 11, 5)     # ordered on the holiday
    assert s.workday(D(2026, 11, 4), 2, HOL) == D(2026, 11, 6)


def test_networkdays_matches_sheet():
    # #4943: ordered Fri 23 Oct, dispatched Fri 6 Nov -> 9 business days, 7 over
    assert s.networkdays(D(2026, 10, 24), D(2026, 11, 6), HOL) == 9


def test_utc_to_melbourne_date():
    # 19:05 UTC on 8 Oct is 06:05 on 9 Oct in Melbourne (AEDT)
    assert s.to_local_date("2026-10-08T19:05:23Z", TZ) == D(2026, 10, 9)


def test_parse_sheet_date_variants():
    assert s.parse_sheet_date(46329) == D(2026, 11, 3)
    assert s.parse_sheet_date("Tue 3 Nov 2026") == D(2026, 11, 3)
    assert s.parse_sheet_date("3 Nov 2026") == D(2026, 11, 3)
    assert s.parse_sheet_date("2026-11-03") == D(2026, 11, 3)
    assert s.parse_sheet_date("") is None


# ---- lateness and exclusions ------------------------------------------

def test_late_when_dispatched_after_due():
    o = ev(raw("#5100", "2026-11-01T22:00:00Z", fulfilled="2026-11-05T00:00:00Z"))  # Mon 2 -> Thu 5
    assert o.order_date == D(2026, 11, 2) and o.due == D(2026, 11, 5)
    assert o.dispatched == D(2026, 11, 5) and not o.late
    o = ev(raw("#5101", "2026-11-01T22:00:00Z", fulfilled="2026-11-05T22:00:00Z"))  # Fri 6
    assert o.late


def test_unfulfilled_late_only_after_due():
    r = raw("#5102", "2026-11-03T22:00:00Z")   # Wed 4 Nov, due Fri 6
    assert not ev(r, today=D(2026, 11, 6)).late
    assert ev(r, today=D(2026, 11, 7)).late


def test_earliest_fulfilment_and_location():
    r = raw("#5103", "2026-11-01T22:00:00Z")
    r["fulfillments"] = [
        {"createdAt": "2026-11-07T00:00:00Z", "status": "SUCCESS", "location": {"name": "BTK"}},
        {"createdAt": "2026-11-06T00:00:00Z", "status": "SUCCESS", "location": {"name": "New3PL"}},
        {"createdAt": "2026-11-02T00:00:00Z", "status": "CANCELLED", "location": {"name": "BTK"}},
    ]
    o = ev(r)
    assert o.dispatched == D(2026, 11, 6) and o.location == "New3PL" and o.tpl == "Precision"


def test_assigned_location_when_unfulfilled_and_unmapped():
    o = ev(raw("#5104", "2026-11-01T22:00:00Z", assigned="Warehouse"))
    assert o.tpl == "Warehouse"
    assert s.map_3pl("Warehouse", MAP) == ("Warehouse", False)


def test_exclusions():
    assert ev(raw("#1", "2026-11-01T00:00:00Z", cancelledAt="2026-11-02T00:00:00Z")).excluded == "cancelled"
    assert ev(raw("#2", "2026-11-01T00:00:00Z", ship=False)).excluded == "no shippable items"
    assert ev(raw("#3", "2026-11-01T00:00:00Z", tags=["Test"])).excluded == "test tag"
    assert ev(raw("#4", "2026-11-01T00:00:00Z", tags=["B2B"])).excluded == "B2B"
    assert ev(raw("#5", "2026-11-01T00:00:00Z", pe="PurchasingCompany")).excluded == "B2B"
    assert ev(raw("#6", "2026-11-01T00:00:00Z", tags=["testimonial"])).excluded is None


# ---- planning ----------------------------------------------------------

def fl_state(rows):
    L = s.fault_log_layout({"spreadsheet_id": "x", "tab": "Late dispatch", "first_data_row": 2})
    rows = [rows[0] + [""] * (10 - len(rows[0])) + [46329]] + rows[1:]
    return s.build_state(L, 1, 498, rows)


def btk_state(rows, a2="Blah. Status as at 6 November 2026."):
    L = s.btk_layout({"spreadsheet_id": "y", "tab": "Late dispatch", "first_data_row": 9})
    rows = [rows[0] + [""] * (10 - len(rows[0])) + [46329]] + rows[1:]
    return s.build_state(L, 2, 500, rows, a2)


def orders(*raws, today=D(2026, 11, 8)):
    return {s.order_number(r["name"]): s.evaluate(r, HOL, today, TZ, MAP, 2) for r in raws}


def test_build_state_reads_holidays_and_rows():
    st = fl_state([[5008, 46324, "=WORKDAY(B2,2,$K$2:$K)", "", "", "", "BTK", "Open – overdue"]])
    assert st.holidays == HOL and st.by_order == {5008: 2} and st.last_data_row == 2


def test_fault_log_fill_dispatched_and_status():
    st = fl_state([
        [5008, 46324, "=f", "", "", "", "BTK", "Open – overdue"],
        [5033, 46327, "=f", "", "", "", "BTK", "Raised with 3PL"],
        [5080, 46330, "=f", "", "", "", "BTK", "Open – overdue", "chasing BTK"],
    ])
    o = orders(raw("#5008", "2026-10-29T01:00:00Z", fulfilled="2026-11-06T01:00:00Z"),
               raw("#5033", "2026-10-31T23:00:00Z", fulfilled="2026-11-06T01:00:00Z"),
               raw("#5080", "2026-11-03T23:00:00Z"))
    p = s.plan_sheet(st, o, D(2026, 11, 8))
    got = {(c.row, c.col): c.new for c in p.changes}
    assert got == {(2, 3): D(2026, 11, 6), (2, 7): "Dispatched late",
                   (3, 3): D(2026, 11, 6)}           # Raised with 3PL kept
    assert p.appends == []


def test_fault_log_never_overwrites_dispatched_or_notes():
    st = fl_state([[5008, 46324, "=f", 46331, "", "", "BTK", "Dispatched late", "x"]])
    o = orders(raw("#5008", "2026-10-29T01:00:00Z", fulfilled="2026-11-06T01:00:00Z"))
    assert s.plan_sheet(st, o, D(2026, 11, 8)).changes == []


def test_cancelled_existing_row_gets_note_only_if_blank():
    st = fl_state([[5008, 46324, "=f", "", "", "", "BTK", "Open – overdue"],
                   [5009, 46324, "=f", "", "", "", "BTK", "Open – overdue", "keep me"]])
    o = orders(raw("#5008", "2026-10-29T01:00:00Z", cancelledAt="2026-11-05T01:00:00Z"),
               raw("#5009", "2026-10-29T01:00:00Z", cancelledAt="2026-11-05T01:00:00Z"))
    p = s.plan_sheet(st, o, D(2026, 11, 8))
    assert [(c.row, c.col, c.new) for c in p.changes] == [(2, 8, "Cancelled in Shopify 5 Nov 2026")]


def test_new_late_orders_all_3pls_in_fault_log_btk_only_in_register():
    o = orders(raw("#5200", "2026-11-01T22:00:00Z", fulfilled="2026-11-06T01:00:00Z", loc="BTK"),
               raw("#5201", "2026-11-01T22:00:00Z", fulfilled="2026-11-06T01:00:00Z", loc="New3PL"),
               raw("#5202", "2026-11-01T22:00:00Z", fulfilled="2026-11-03T01:00:00Z", loc="BTK"),  # on time
               raw("#5203", "2026-11-01T22:00:00Z", fulfilled="2026-11-06T01:00:00Z", tags=["test"]))
    fl = s.plan_sheet(fl_state([[5008, 46324, "=f", 46331, "", "", "BTK", "Dispatched late"]]), o, D(2026, 11, 8))
    assert [a.number for a in fl.appends] == [5200, 5201]
    assert fl.appends[1].tpl == "Precision"
    bt = s.plan_sheet(btk_state([["#5008", 46324, "=f", 46331, "", "", "Dispatched late"]]), o, D(2026, 11, 8))
    assert [a.number for a in bt.appends] == [5200]


def test_btk_status_and_a2_and_comment_untouched():
    st = btk_state([["#5080", 46330, "=f", "", "", "", "Not yet dispatched", "BTK says Monday"]])
    o = orders(raw("#5080", "2026-11-03T23:00:00Z", fulfilled="2026-11-08T23:00:00Z"), today=D(2026, 11, 10))
    p = s.plan_sheet(st, o, D(2026, 11, 10))
    got = {(c.row, c.col): c.new for c in p.changes}
    assert got[(9, 3)] == D(2026, 11, 9)
    assert got[(9, 6)] == "Dispatched late"
    assert got[(2, 0)] == "Blah. Status as at 10 November 2026."
    assert all(c.col != 7 for c in p.changes)   # H BTK comment never written


def test_new_row_formulas():
    L = s.btk_layout({"spreadsheet_id": "y", "tab": "t", "first_data_row": 9})
    o = orders(raw("#5300", "2026-11-03T23:00:00Z"))[5300]
    v = s.new_row_values(L, 61, o)
    assert v[0] == {"stringValue": "#5300"}
    assert v[2] == {"formulaValue": "=WORKDAY(B61,2,$K$9:$K)"}
    assert v[4] == {"formulaValue": '=IF(D61="","",NETWORKDAYS(B61+1,D61,$K$9:$K))'}
    assert v[5] == {"formulaValue": '=IF(D61="","",E61-2)'}
    assert v[6] == {"stringValue": "Not yet dispatched"} and v[3] is None and v[7] is None
    Lf = s.fault_log_layout({"spreadsheet_id": "x", "tab": "t", "first_data_row": 2})
    v = s.new_row_values(Lf, 54, o)
    assert v[0] == {"numberValue": 5300} and v[2] == {"formulaValue": "=WORKDAY(B54,2,$K$2:$K)"}
    assert v[7] == {"stringValue": "Open – overdue"} and v[8] is None


def test_requests_copy_format_from_row_above_and_rollback():
    st = fl_state([[5008, 46324, "=f", "", "", "", "BTK", "Open – overdue"]])
    o = orders(raw("#5200", "2026-11-01T22:00:00Z", fulfilled="2026-11-06T01:00:00Z"),
               raw("#5008", "2026-10-29T01:00:00Z", fulfilled="2026-11-06T01:00:00Z"))
    st.d_formats[2] = {"type": "DATE", "pattern": "ddd d mmm yyyy"}
    p = s.plan_sheet(st, o, D(2026, 11, 8))
    fwd, back = s.build_requests(st, p)
    cps = [r["copyPaste"] for r in fwd if "copyPaste" in r]
    assert {c["pasteType"] for c in cps} == {"PASTE_FORMAT", "PASTE_DATA_VALIDATION"}
    assert cps[0]["source"]["startRowIndex"] == 1 and cps[0]["destination"]["startRowIndex"] == 2
    assert not any("appendDimension" in r for r in fwd)
    assert len(back) == 3     # clear new row, restore D2, restore H2


def test_append_grows_grid_when_full():
    st = fl_state([[5008, 46324, "=f", 46331, "", "", "BTK", "Dispatched late"]])
    st.row_count = 2
    o = orders(raw("#5200", "2026-11-01T22:00:00Z", fulfilled="2026-11-06T01:00:00Z"))
    fwd, back = s.build_requests(st, s.plan_sheet(st, o, D(2026, 11, 8)))
    assert fwd[0] == {"appendDimension": {"sheetId": 1, "dimension": "ROWS", "length": 1}}
    assert "deleteDimension" in back[-1]


def test_duplicate_order_rows_stop_the_run():
    import pytest
    with pytest.raises(s.SweepError):
        fl_state([[5008, 1], [5008, 2]])


# ---- verification ------------------------------------------------------

def test_error_and_count_checks():
    fv = [["t"], ["d"], [], ["Orders dispatched late", "1"], ["Not yet dispatched", "1"], ["Total", "2"],
          [], ["Order"], ["#1", "", "", "", "", "", "Dispatched late"], ["#2", "", "#REF!", "", "", "", "Not yet dispatched"]]
    assert s.check_btk_counts(fv, 9) == []
    assert s.check_sheet("X", fv) == ["X: C10 shows #REF!"]
    fv[5][1] = "3"
    assert len(s.check_btk_counts(fv, 9)) == 1


# ---- schedule ----------------------------------------------------------

CFG = {"timezone": "Australia/Melbourne",
       "schedule": {"local_hour": 7, "every_n_days": 2, "anchor_date": "2026-11-10"}}


def test_schedule_aedt_summer():
    # AEDT (UTC+11): 20:00 UTC = 07:00 next day Melbourne.
    now = dt.datetime(2026, 11, 9, 20, 7, tzinfo=dt.timezone.utc)      # Tue 10 Nov 07:07
    assert s.should_run_scheduled(CFG, "0 20 * * *", now)[0]
    assert not s.should_run_scheduled(CFG, "0 21 * * *", now.replace(hour=21))[0]   # 08:00
    assert not s.should_run_scheduled(CFG, "0 20 * * *", now + dt.timedelta(days=1))[0]  # odd day
    assert s.should_run_scheduled(CFG, "0 20 * * *", now + dt.timedelta(days=2))[0]


def test_schedule_aest_winter_and_late_start():
    # AEST (UTC+10): 21:00 UTC = 07:00. A runner that starts 40 min late still counts.
    now = dt.datetime(2027, 6, 1, 21, 40, tzinfo=dt.timezone.utc)      # Wed 2 Jun 07:40
    ok_21 = s.should_run_scheduled(CFG, "0 21 * * *", now)
    ok_20 = s.should_run_scheduled(CFG, "0 20 * * *", now)
    assert not ok_20[0]
    gap = (D(2027, 6, 2) - D(2026, 11, 10)).days
    assert ok_21[0] == (gap % 2 == 0)
