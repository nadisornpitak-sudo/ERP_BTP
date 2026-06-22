"""MRP netting engine — pure function. ไม่มี I/O, ไม่แก้ db.

    mrp_run(db, cfg, now) -> {
        generated_at, summary,
        planned_orders: [...],   # PR ที่แนะนำ
        exceptions:     [...],   # shortage / late_po / expedite / partial_build
    }

ลำดับ: BOM explosion → on-hand/scheduled receipts → net requirement
        → lot sizing → lead-time offset → planned orders + exceptions.
"""
from __future__ import annotations
import math
from collections import defaultdict
from datetime import datetime, timedelta, date

from .config import (Config, ONHAND_CHANNELS, OPEN_WO_STATUS, OPEN_PO_STATUS)
from .master import attrs


# ── helpers ────────────────────────────────────────────────
def _onhand(p: dict) -> int:
    st = p.get("stock", {}) or {}
    return sum(int(st.get(c, 0) or 0) for c in ONHAND_CHANNELS)


def _parse_date(s, default: date) -> date:
    if not s:
        return default
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except ValueError:
        return default


def _wo_items(wo: dict) -> list:
    return wo.get("items") or [{
        "sku": wo.get("sku"), "qty": wo.get("qty", 0),
        "producedQty": wo.get("producedQty", 0), "bom": wo.get("bom", []),
    }]


def lot_size(nr: float, moq: int, pack: int, jit: bool) -> int:
    """Lot sizing: JIT = lot-for-lot; ไม่ใช่ JIT = max(nr, moq); ปัดขึ้นตาม pack."""
    if nr <= 0:
        return 0
    q = nr if jit else max(nr, moq)
    if pack and pack > 1:
        q = math.ceil(q / pack) * pack
    return int(math.ceil(q))


# ── core ───────────────────────────────────────────────────
def mrp_run(db: dict, cfg: Config | None = None, now: datetime | None = None) -> dict:
    cfg = cfg or Config()
    today = (now or datetime.now()).date()

    products = db.get("products", [])
    by_sku = {p.get("sku"): p for p in products if p.get("sku")}
    # component = sku ที่ปรากฏใน BOM ของสินค้าใด ๆ (= วัตถุดิบจริง)
    components = set()
    for p in products:
        for c in (p.get("bom") or []):
            if c.get("sku"):
                components.add(c["sku"])

    # 1) avg daily usage จาก moves type=issue (ย้อนหลัง usage_window_days)
    win_start = today - timedelta(days=cfg.usage_window_days)
    used = defaultdict(int)
    for m in db.get("moves", []):
        if m.get("type") != "issue":
            continue
        d = _parse_date(m.get("date"), today)
        if d >= win_start:
            used[m.get("sku")] += int(m.get("qty", 0) or 0)
    avg_daily = {sku: used[sku] / cfg.usage_window_days for sku in used}

    # 2) BOM explosion → gross requirement + earliest need date ต่อ component
    gross = defaultdict(float)
    need_date = {}
    partial_builds = []
    for wo in db.get("workOrders", []):
        if wo.get("status") not in OPEN_WO_STATUS:
            continue
        wo_need = _parse_date(wo.get("deliveryDate") or wo.get("date"), today)
        # ตรวจ partial availability ระดับ WO (max buildable)
        item_limits = []
        for it in _wo_items(wo):
            remaining = max(0, int(it.get("qty", 0)) - int(it.get("producedQty", 0)))
            if remaining <= 0:
                continue
            # ใช้ BOM ของ item ถ้ามี ไม่งั้น fallback ไป BOM ต้นแบบของสินค้า
            item_bom = it.get("bom") or (by_sku.get(it.get("sku"), {}).get("bom") or [])
            limit = None
            for c in item_bom:
                csku, cq = c.get("sku"), c.get("qty", 0)
                if not csku or cq <= 0:
                    continue
                a = attrs(by_sku.get(csku, {"sku": csku}), cfg)
                req = cq * remaining * (1 + a["scrap"])
                gross[csku] += req
                if csku not in need_date or wo_need < need_date[csku]:
                    need_date[csku] = wo_need
                cap = int((_onhand(by_sku[csku]) // cq)) if csku in by_sku and cq else 0
                limit = cap if limit is None else min(limit, cap)
            if limit is not None and limit < remaining:
                item_limits.append((it.get("sku"), remaining, limit))
        for sku, remaining, buildable in item_limits:
            partial_builds.append({
                "kind": "partial_build", "severity": "high",
                "sku": sku, "wo": wo.get("no"),
                "message": f"WO {wo.get('no')}: {sku} ผลิตได้สูงสุด {buildable}/{remaining} "
                           f"(วัตถุดิบไม่พอ) — ทยอยเบิกได้เท่าที่มี",
                "data": {"remaining": remaining, "buildable": buildable},
            })

    # 3) scheduled receipts จาก open PO (eta = po.date + lead_days ของ component)
    sched = defaultdict(int)
    sched_late = defaultdict(int)
    po_eta = defaultdict(list)
    for po in db.get("pos", []):
        if po.get("status") not in OPEN_PO_STATUS:
            continue
        po_date = _parse_date(po.get("date"), today)
        for it in po.get("items", []):
            csku, q = it.get("sku"), int(it.get("qty", 0) or 0)
            if not csku or q <= 0:
                continue
            a = attrs(by_sku.get(csku, {"sku": csku}), cfg)
            eta = po_date + timedelta(days=a["lead_days"])
            nd = need_date.get(csku, today)
            if eta <= nd:
                sched[csku] += q          # มาทันใช้
            else:
                sched_late[csku] += q     # มาช้า
            po_eta[csku].append((po.get("no"), eta, q, eta <= nd))

    # 4) netting + lot sizing + lead-time offset ต่อ component
    planned, exceptions = [], list(partial_builds)
    plan_skus = set(gross) | (components if cfg.enable_min_max else set())

    for csku in sorted(plan_skus):
        p = by_sku.get(csku)
        if not p:
            exceptions.append({
                "kind": "orphan_component", "severity": "medium", "sku": csku, "wo": "",
                "message": f"BOM อ้างถึง {csku} ที่ไม่มีในตารางสินค้า", "data": {}})
            continue
        a = attrs(p, cfg)
        poh = _onhand(p)
        gr = gross.get(csku, 0)
        sr = sched.get(csku, 0)
        ad = avg_daily.get(csku, 0)
        ss = ad * a["safety_days"]
        rop = ad * a["lead_days"] + ss
        nr = max(0.0, ss + gr - poh - sr)

        nd = need_date.get(csku, today + timedelta(days=a["lead_days"]))

        # ── exceptions เรื่องความครอบคลุม demand (ไม่ขึ้นกับว่าจะสั่งหรือไม่) ──
        if gr > 0 and (poh + sr) < gr:
            exceptions.append({
                "kind": "shortage", "severity": "high", "sku": csku, "wo": "",
                "message": f"{csku}: ขาด {round(gr - poh - sr, 1)} "
                           f"(ต้องใช้ {round(gr,1)}, มี {poh}, PO ทันใช้ {sr})",
                "data": {"gross": round(gr, 1), "on_hand": poh, "sched": sr}})
            if sched_late.get(csku, 0) > 0:
                exceptions.append({
                    "kind": "late_po", "severity": "high", "sku": csku, "wo": "",
                    "message": f"{csku}: PO ที่ค้างมาช้ากว่ากำหนดใช้ ({nd.isoformat()}) "
                               f"— เสี่ยงผลิตไม่ทัน",
                    "data": {"need_date": nd.isoformat(), "late_qty": sched_late.get(csku, 0)}})

        # ── ตัดสินใจสั่ง ──
        reason = None
        if gr > 0 and nr > 0:
            reason = "demand"
        elif cfg.enable_min_max and gr == 0 and rop > 0 and poh <= rop:
            nr = max(nr, lot_size_target(rop, poh))   # เติมกลับถึง ROP
            reason = "min_max"
        if not reason or nr <= 0:
            continue

        order_qty = lot_size(nr, a["moq"], a["pack"], a["jit"])

        # จำกัดปริมาณ BULK ตามอายุเก็บ — เฉพาะการเติมแบบ min-max (speculative)
        # ห้ามใช้กับ demand จริง (WO ที่ยืนยันแล้วต้องสั่งให้พอผลิต แม้เสี่ยงหมดอายุ)
        if (reason == "min_max" and cfg.enable_shelf_cap and a["class"] == "BULK"
                and a["shelf_life"] and ad > 0):
            max_hold = ad * a["shelf_life"]
            order_qty = max(0, min(order_qty, int(max_hold - poh)))
            if order_qty == 0:
                continue

        # lead-time offset → สถานะการปล่อยคำสั่ง
        days_to_need = (nd - today).days
        lt = a["lead_days"] + a["safety_days"]
        release_date = nd - timedelta(days=lt)
        if days_to_need < a["lead_days"]:
            status, sev = "expedite", "critical"
        elif days_to_need <= lt:
            status, sev = "release_now", "high"
        else:
            status, sev = "future", "low"

        planned.append({
            "sku": csku, "name": a["name"], "class": a["class"], "vendor": a["vendor"],
            "reason": reason,
            "gross_req": round(gr, 1), "on_hand": poh, "scheduled_receipts": sr,
            "safety_stock": round(ss, 1), "reorder_point": round(rop, 1),
            "net_req": round(nr, 1), "order_qty": order_qty,
            "lead_days": a["lead_days"], "need_date": nd.isoformat(),
            "release_date": release_date.isoformat(), "status": status,
            "est_cost": round(order_qty * a["cost"], 2),
        })
        if status == "expedite":
            exceptions.append({
                "kind": "expedite", "severity": sev, "sku": csku, "wo": "",
                "message": f"{csku}: ต้องใช้ {nd.isoformat()} แต่ lead time {a['lead_days']} วัน "
                           f"— สั่งปกติไม่ทัน ต้องเร่ง",
                "data": {"need_date": nd.isoformat(), "lead_days": a["lead_days"]}})

    _sev = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    planned.sort(key=lambda x: (_sev.get({"expedite": "critical", "release_now": "high",
                                           "future": "low"}.get(x["status"], "low"), 3),
                                -x["est_cost"]))
    exceptions.sort(key=lambda x: _sev.get(x["severity"], 4))

    return {
        "generated_at": (now or datetime.now()).isoformat(),
        "horizon_today": today.isoformat(),
        "summary": _summary(planned, exceptions),
        "planned_orders": planned[: cfg.max_planned_orders],
        "exceptions": exceptions[: cfg.max_exceptions],
    }


def lot_size_target(rop: float, poh: int) -> float:
    """min-max: เติมกลับให้ถึง ROP."""
    return max(0.0, rop - poh)


def _summary(planned, exceptions):
    from collections import Counter
    return {
        "planned_orders": len(planned),
        "by_status": dict(Counter(p["status"] for p in planned)),
        "by_class": dict(Counter(p["class"] for p in planned)),
        "exceptions": len(exceptions),
        "exceptions_by_kind": dict(Counter(e["kind"] for e in exceptions)),
        "est_total_cost": round(sum(p["est_cost"] for p in planned), 2),
    }
