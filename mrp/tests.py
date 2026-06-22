"""Unit tests สำหรับ MRP engine — รันด้วย: python -m mrp.tests
ไม่พึ่ง pytest. ใช้ db จำลองล้วน ๆ (ไม่แตะข้อมูลจริง).
"""
from __future__ import annotations
from datetime import datetime, timedelta

from .config import Config, classify
from .engine import mrp_run, lot_size

NOW = datetime(2026, 6, 22, 9, 0, 0)
_fails = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


def prod(sku, stock_w=0, bom=None, **ov):
    p = {"sku": sku, "name": sku, "stock": {"W": stock_w}, "cost": ov.pop("cost", 1)}
    if bom:
        p["bom"] = bom
    p.update(ov)   # override fields เช่น scrapPct, moq, leadTime_days, ...
    return p


def wo(no, sku, qty, produced=0, bom=None, days_to_deliver=60, status="ร่าง"):
    return {"id": no, "no": no, "status": status,
            "date": NOW.date().isoformat(),
            "deliveryDate": (NOW.date() + timedelta(days=days_to_deliver)).isoformat(),
            "items": [{"sku": sku, "name": sku, "qty": qty, "producedQty": produced, "bom": bom or []}]}


def po(no, sku, qty, days_ago=0, status="รอรับ"):
    return {"no": no, "status": status,
            "date": (NOW.date() - timedelta(days=days_ago)).isoformat(),
            "items": [{"sku": sku, "qty": qty}]}


def find(orders, sku):
    return next((o for o in orders if o["sku"] == sku), None)


def kinds(exc):
    return [e["kind"] for e in exc]


# ─────────────────────────────────────────────────────────
def t_classify():
    print("classify (material class)")
    check("FG→FG", classify("FG0101-001") == "FG")
    check("BU→BULK", classify("BU13-001") == "BULK")
    check("PK05→PRIMARY", classify("PK05-001") == "PRIMARY")
    check("PK01→SECONDARY", classify("PK01-037") == "SECONDARY")


def t_lot_size():
    print("lot sizing")
    check("lot-for-lot", lot_size(150, 0, 1, False) == 150)
    check("moq floor", lot_size(150, 500, 100, False) == 500)
    check("pack round-up", lot_size(170, 0, 50, False) == 200)
    check("jit ignores moq", lot_size(150, 500, 100, True) == 200)
    check("zero", lot_size(0, 500, 100, False) == 0)


def t_basic_netting():
    print("basic netting (demand → net req → order)")
    db = {"products": [
        prod("FG-T1", bom=[{"sku": "RM-X", "qty": 2}]),
        prod("RM-X", stock_w=50, scrapPct=0, moq=0, packMultiple=1, leadTime_days=10, safety_days=0),
    ], "workOrders": [wo("WO1", "FG-T1", 100, days_to_deliver=60)], "pos": [], "moves": []}
    r = mrp_run(db, Config(), NOW)
    o = find(r["planned_orders"], "RM-X")
    check("order created", o is not None)
    check("gross = 200 (2×100)", o and o["gross_req"] == 200)
    check("net req = 150 (200−50)", o and o["net_req"] == 150)
    check("order qty = 150", o and o["order_qty"] == 150)
    check("status future (deliver +60)", o and o["status"] == "future")


def t_scrap():
    print("scrap factor in gross requirement")
    db = {"products": [
        prod("FG-T", bom=[{"sku": "RM-S", "qty": 2}]),
        prod("RM-S", stock_w=0, scrapPct=0.1, moq=0, packMultiple=1),
    ], "workOrders": [wo("WO", "FG-T", 100)], "pos": [], "moves": []}
    o = find(mrp_run(db, Config(), NOW)["planned_orders"], "RM-S")
    check("gross = 220 (2×100×1.1)", o and o["gross_req"] == 220)


def t_po_covers():
    print("scheduled receipt covers demand (on-time PO)")
    db = {"products": [
        prod("FG-T1", bom=[{"sku": "RM-X", "qty": 2}]),
        prod("RM-X", stock_w=50, scrapPct=0, moq=0, packMultiple=1, leadTime_days=10),
    ], "workOrders": [wo("WO1", "FG-T1", 100, days_to_deliver=60)],
        "pos": [po("PO1", "RM-X", 200, days_ago=0)], "moves": []}
    r = mrp_run(db, Config(), NOW)
    check("no order (covered)", find(r["planned_orders"], "RM-X") is None)
    check("no shortage", "shortage" not in kinds(r["exceptions"]))


def t_late_po():
    print("late PO → late_po + shortage exception")
    db = {"products": [
        prod("FG-T1", bom=[{"sku": "RM-X", "qty": 2}]),
        prod("RM-X", stock_w=50, scrapPct=0, moq=0, packMultiple=1, leadTime_days=10),
    ], "workOrders": [wo("WO1", "FG-T1", 100, days_to_deliver=5)],   # ต้องใช้เร็ว
        "pos": [po("PO1", "RM-X", 200, days_ago=0)], "moves": []}    # eta = now+10 > need now+5
    r = mrp_run(db, Config(), NOW)
    check("late_po flagged", "late_po" in kinds(r["exceptions"]))
    check("shortage flagged", "shortage" in kinds(r["exceptions"]))


def t_expedite():
    print("expedite (need date < lead time)")
    db = {"products": [
        prod("FG-T1", bom=[{"sku": "RM-X", "qty": 2}]),
        prod("RM-X", stock_w=0, scrapPct=0, moq=0, packMultiple=1, leadTime_days=10),
    ], "workOrders": [wo("WO1", "FG-T1", 100, days_to_deliver=3)], "pos": [], "moves": []}
    r = mrp_run(db, Config(), NOW)
    o = find(r["planned_orders"], "RM-X")
    check("status expedite", o and o["status"] == "expedite")
    check("expedite exception", "expedite" in kinds(r["exceptions"]))


def t_partial_build():
    print("partial availability (max buildable)")
    db = {"products": [
        prod("FG-T", bom=[{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]),
        prod("A", stock_w=120, scrapPct=0), prod("B", stock_w=300, scrapPct=0),
    ], "workOrders": [wo("WO", "FG-T", 100)], "pos": [], "moves": []}
    r = mrp_run(db, Config(), NOW)
    pb = next((e for e in r["exceptions"] if e["kind"] == "partial_build"), None)
    check("partial_build flagged", pb is not None)
    check("buildable = 60 (120÷2)", pb and pb["data"]["buildable"] == 60)


def t_min_max():
    print("min-max reorder (no WO demand, on-hand ≤ ROP)")
    moves = [{"type": "issue", "sku": "RM-Y", "qty": 9000,
              "date": (NOW.date() - timedelta(days=10)).isoformat()}]  # 9000/90 = 100/วัน
    db = {"products": [
        prod("FG-Z", bom=[{"sku": "RM-Y", "qty": 1}]),                 # ทำให้ RM-Y เป็น component
        prod("RM-Y", stock_w=500, scrapPct=0, moq=0, packMultiple=1, leadTime_days=10, safety_days=0),
    ], "workOrders": [], "pos": [], "moves": moves}                    # ไม่มี WO → ไม่มี demand
    r = mrp_run(db, Config(), NOW)
    o = find(r["planned_orders"], "RM-Y")
    check("min-max order created", o is not None)
    check("reason = min_max", o and o["reason"] == "min_max")
    check("ROP = 1000 (100×10)", o and o["reorder_point"] == 1000)
    check("order ≈ 500 (ROP−onhand)", o and o["order_qty"] == 500)


def t_shelf_cap_minmax():
    print("BULK shelf-life cap — throttles SPECULATIVE min-max only")
    moves = [{"type": "issue", "sku": "BU-M", "qty": 900,
              "date": (NOW.date() - timedelta(days=10)).isoformat()}]  # 900/90 = 10/วัน
    db = {"products": [
        prod("FG-M", bom=[{"sku": "BU-M", "qty": 1}]),
        prod("BU-M", stock_w=200, scrapPct=0, moq=0, packMultiple=1,
             shelfLife_days=30, leadTime_days=30, safety_days=14),
    ], "workOrders": [], "pos": [], "moves": moves}      # ไม่มี WO → min-max
    o = find(mrp_run(db, Config(), NOW)["planned_orders"], "BU-M")
    # ROP=10×30+10×14=440; min-max nr=240; แต่ max_hold=10×30=300 → cap top-up = 300−200 = 100
    check("min-max capped to 100 by shelf life", o and o["order_qty"] == 100, o and o["order_qty"])


def t_shelf_cap_demand_bypass():
    print("BULK shelf-life cap — does NOT block firm WO demand")
    moves = [{"type": "issue", "sku": "BU-T", "qty": 900,
              "date": (NOW.date() - timedelta(days=10)).isoformat()}]  # 10/วัน
    db = {"products": [
        prod("FG-B", bom=[{"sku": "BU-T", "qty": 1}]),
        prod("BU-T", stock_w=200, scrapPct=0, moq=0, packMultiple=1,
             shelfLife_days=100, safety_days=14),
    ], "workOrders": [wo("WO", "FG-B", 5000, days_to_deliver=60)], "pos": [], "moves": moves}
    o = find(mrp_run(db, Config(), NOW)["planned_orders"], "BU-T")
    # demand nr = 5000 + (10×14 ss) − 200 = 4940 — ต้องไม่ถูก cap
    check("demand order NOT capped", o and o["order_qty"] == 4940, o and o["order_qty"])


def t_no_double_for_produced():
    print("in-progress WO: only remaining qty creates demand")
    db = {"products": [
        prod("FG-P", bom=[{"sku": "RM-X", "qty": 2}]),
        prod("RM-X", stock_w=0, scrapPct=0, moq=0, packMultiple=1),
    ], "workOrders": [wo("WO", "FG-P", 100, produced=80, status="กำลังผลิต")],  # เหลือ 20
        "pos": [], "moves": []}
    o = find(mrp_run(db, Config(), NOW)["planned_orders"], "RM-X")
    check("gross from remaining 20 → 40", o and o["gross_req"] == 40)


def main():
    print("=" * 56)
    print(" MRP ENGINE — UNIT TESTS")
    print("=" * 56)
    for t in [t_classify, t_lot_size, t_basic_netting, t_scrap, t_po_covers,
              t_late_po, t_expedite, t_partial_build, t_min_max,
              t_shelf_cap_minmax, t_shelf_cap_demand_bypass,
              t_no_double_for_produced]:
        t()
    print("=" * 56)
    if _fails:
        print(f"❌ FAILED {len(_fails)}: {', '.join(_fails)}")
        return 1
    print("✅ ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
