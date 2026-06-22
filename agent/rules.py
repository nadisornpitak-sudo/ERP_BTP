"""กฎ (rule-based analyzers) — รับ state ของ ERP คืน list ของ findings.

ทุกฟังก์ชันเป็น pure: ไม่แก้ข้อมูล ไม่ I/O — รับ db + cfg คืน findings.
finding = {
    "key":      str,   # คีย์ไม่ซ้ำ ใช้ทำ diff ระหว่างรอบ
    "rule":     str,   # low_stock | velocity | anomaly
    "kind":     str,   # ชนิดย่อย เช่น reorder / transfer / dead_stock
    "severity": str,   # critical | high | medium | low | info
    "sku":      str,
    "name":     str,
    "message":  str,   # ข้อความภาษาไทยพร้อมแสดง
    "data":     dict,  # ตัวเลขประกอบ
}
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .config import Config, SELLABLE, BACKSTOCK

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _f(key, rule, kind, severity, sku, name, message, **data):
    return {"key": key, "rule": rule, "kind": kind, "severity": severity,
            "sku": sku, "name": name, "message": message, "data": data}


def _sum(stock: dict, channels: Iterable[str]) -> int:
    return sum(int(stock.get(c, 0) or 0) for c in channels)


def _is_tracked(p: dict, cfg: Config) -> bool:
    """ติดตามสต็อกเฉพาะสินค้าสำเร็จรูปที่ตั้งใจขาย (ใช้กับ low_stock + velocity)."""
    sku = p.get("sku", "")
    if cfg.track_prefixes and not any(sku.startswith(pre) for pre in cfg.track_prefixes):
        return False
    name = p.get("name", "")
    if any(kw and kw in name for kw in cfg.skip_keywords):
        return False
    return True


def _reorder_point(p: dict, cfg: Config) -> int:
    r = int(p.get("reorder", 0) or 0)
    return r if r > 0 else cfg.default_reorder


def _parse_date(s: str):
    """รองรับ '2026-06-20', '2026-06-20T09:09:41.638Z' ฯลฯ คืน datetime (naive UTC)."""
    if not s:
        return None
    s = str(s).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None


# ════════════════════════════════════════════════════════════
#  กฎ 1 — Low stock / reorder / transfer
# ════════════════════════════════════════════════════════════
def check_low_stock(db: dict, cfg: Config) -> list[dict]:
    out = []
    for p in db.get("products", []):
        sku = p.get("sku", "")
        if not sku or not _is_tracked(p, cfg):
            continue
        stock = p.get("stock", {}) or {}
        sellable = _sum(stock, SELLABLE)
        backstock = _sum(stock, BACKSTOCK)
        on_hand = sellable + backstock
        rp = _reorder_point(p, cfg)
        name = p.get("name", sku)

        if sellable <= 0:
            # ขายไม่ได้เลย — วิกฤต
            out.append(_f(
                f"stockout:{sku}", "low_stock", "stockout", "critical", sku, name,
                f"⛔ สินค้าหมดหน้าขาย (S+D+O = 0) | สำรองคลัง W={backstock}",
                sellable=sellable, backstock=backstock, reorder=rp))
        elif on_hand <= rp:
            # ของรวมต่ำกว่าจุดสั่งซื้อ — ต้องสั่งผลิต/สั่งซื้อ
            suggest = max(rp * 2 - on_hand, rp)
            out.append(_f(
                f"reorder:{sku}", "low_stock", "reorder", "high", sku, name,
                f"📦 ควรสั่งเพิ่ม: คงเหลือรวม {on_hand} ≤ จุดสั่งซื้อ {rp} "
                f"(แนะนำสั่ง ~{suggest})",
                on_hand=on_hand, reorder=rp, suggest_qty=suggest,
                sellable=sellable, backstock=backstock))
        elif sellable <= rp and backstock > 0:
            # หน้าขายต่ำ แต่คลังสำรองยังมี — ย้ายภายในพอ
            move_qty = min(backstock, max(rp * 2 - sellable, rp))
            out.append(_f(
                f"transfer:{sku}", "low_stock", "transfer", "medium", sku, name,
                f"🔄 ควรย้ายจากคลัง W → หน้าขาย ~{move_qty} "
                f"(หน้าขาย {sellable} ≤ {rp}, สำรอง {backstock})",
                sellable=sellable, backstock=backstock, reorder=rp,
                suggest_qty=move_qty))
    out.sort(key=lambda x: (SEV_ORDER[x["severity"]], -x["data"].get("reorder", 0)))
    return out[: cfg.max_findings_per_rule]


# ════════════════════════════════════════════════════════════
#  กฎ 2 — Sales velocity / dead stock
# ════════════════════════════════════════════════════════════
def check_velocity(db: dict, cfg: Config, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    moves = db.get("moves", [])
    sale_moves = [m for m in moves if m.get("type") == "sale"]

    # ถ้าไม่มีประวัติการขายเลย → วิเคราะห์ความเร็วไม่ได้ แจ้งเป็น info ก้อนเดียว
    if not sale_moves:
        return [_f(
            "velocity:no-data", "velocity", "no_data", "info", "", "",
            "ℹ️ ยังไม่มี move ชนิด 'sale' — ข้ามการวิเคราะห์ความเร็วขาย/dead stock "
            "(จะเริ่มทำงานอัตโนมัติเมื่อมีการขายเข้าระบบ)",
            sale_moves=0)]

    win_start = now - timedelta(days=cfg.velocity_window_days)
    dead_start = now - timedelta(days=cfg.dead_window_days)

    sold_window = defaultdict(int)   # ขายในหน้าต่างวิเคราะห์
    last_sale = {}                   # ขายล่าสุดของแต่ละ sku
    for m in sale_moves:
        sku = m.get("sku", "")
        dt = _parse_date(m.get("date", ""))
        qty = int(m.get("qty", 0) or 0)
        if not sku or dt is None:
            continue
        if dt >= win_start:
            sold_window[sku] += qty
        if sku not in last_sale or dt > last_sale[sku]:
            last_sale[sku] = dt

    out = []
    for p in db.get("products", []):
        sku = p.get("sku", "")
        if not sku or not _is_tracked(p, cfg):
            continue
        stock = p.get("stock", {}) or {}
        sellable = _sum(stock, SELLABLE)
        on_hand = sellable + _sum(stock, BACKSTOCK)
        name = p.get("name", sku)

        units = sold_window.get(sku, 0)
        if units > 0:
            avg_daily = units / cfg.velocity_window_days
            cover = sellable / avg_daily if avg_daily > 0 else 999
            if cover < cfg.cover_days:
                out.append(_f(
                    f"fastmover:{sku}", "velocity", "fast_mover", "high", sku, name,
                    f"🔥 ขายเร็ว: เหลือพอขาย ~{cover:.1f} วัน "
                    f"(ขาย {units} ชิ้น/{cfg.velocity_window_days} วัน, หน้าขาย {sellable})",
                    days_of_cover=round(cover, 1), units_sold=units,
                    avg_daily=round(avg_daily, 2), sellable=sellable))
        else:
            # ไม่มีขายในหน้าต่าง dead_window และยังค้างของเยอะ → dead stock
            ls = last_sale.get(sku)
            never = ls is None
            stale = never or ls < dead_start
            if stale and on_hand >= cfg.dead_min_qty:
                days_idle = None if never else (now - ls).days
                idle_txt = "ไม่เคยขาย" if never else f"ไม่ขยับ {days_idle} วัน"
                out.append(_f(
                    f"dead:{sku}", "velocity", "dead_stock", "low", sku, name,
                    f"🪦 Dead stock: คงเหลือ {on_hand} ({idle_txt})",
                    on_hand=on_hand, days_idle=days_idle, never_sold=never))

    out.sort(key=lambda x: (SEV_ORDER[x["severity"]], -x["data"].get("on_hand", 0)))
    return out[: cfg.max_findings_per_rule]


# ════════════════════════════════════════════════════════════
#  กฎ 3 — Anomalies (ความผิดปกติของข้อมูล)
# ════════════════════════════════════════════════════════════
def check_anomaly(db: dict, cfg: Config) -> list[dict]:
    out = []
    products = db.get("products", [])
    seen = defaultdict(int)
    known_skus = set()

    for p in products:
        sku = p.get("sku", "")
        if not sku:
            out.append(_f("anom:blank-sku", "anomaly", "blank_sku", "medium", "",
                          p.get("name", ""), "⚠️ พบสินค้าไม่มี SKU"))
            continue
        known_skus.add(sku)
        seen[sku] += 1
        stock = p.get("stock", {}) or {}
        name = p.get("name", sku)

        neg = {c: v for c, v in stock.items() if isinstance(v, (int, float)) and v < 0}
        if neg:
            out.append(_f(
                f"neg:{sku}", "anomaly", "negative_stock", "critical", sku, name,
                f"❗ สต็อกติดลบ: {neg}", channels=neg))

    for sku, n in seen.items():
        if n > 1:
            out.append(_f(
                f"dup:{sku}", "anomaly", "duplicate_sku", "high", sku, "",
                f"⚠️ SKU ซ้ำ {n} รายการในตารางสินค้า", count=n))

    # move อ้างถึง sku ที่ไม่มีในตารางสินค้า / qty ใหญ่ผิดปกติ
    orphan = set()
    for m in db.get("moves", []):
        sku = m.get("sku", "")
        if sku and sku not in known_skus:
            orphan.add(sku)
        qty = int(m.get("qty", 0) or 0)
        if qty > cfg.anomaly_qty:
            out.append(_f(
                f"bigmove:{m.get('id','?')}", "anomaly", "huge_move", "info",
                sku, "",
                f"🔍 move ปริมาณสูงผิดปกติ: {qty} ({m.get('type','')})", qty=qty))
    for sku in sorted(orphan):
        out.append(_f(
            f"orphan:{sku}", "anomaly", "orphan_move", "medium", sku, "",
            "⚠️ มี move ของ SKU ที่ไม่มีในตารางสินค้า"))

    out.sort(key=lambda x: SEV_ORDER[x["severity"]])
    return out[: cfg.max_findings_per_rule]


# ════════════════════════════════════════════════════════════
#  กฎ 4 — MRP (วางแผนวัตถุดิบ) — ดึง exception จาก engine ของโมดูล mrp
# ════════════════════════════════════════════════════════════
_MRP_SEV = {"expedite": "critical", "shortage": "high", "late_po": "high",
            "partial_build": "medium", "orphan_component": "medium", "error": "info"}


def check_mrp(db: dict, cfg: Config) -> list[dict]:
    try:
        from mrp.engine import mrp_run            # ใช้ engine เดียวกับหน้า MRP
        from mrp.config import Config as MrpConfig
    except Exception:
        return []   # ไม่มีโมดูล mrp ก็ข้ามเงียบ ๆ
    try:
        plan = mrp_run(db, MrpConfig())
    except Exception as e:  # noqa: BLE001
        return [_f("mrp:error", "mrp", "error", "info", "", "",
                   f"🏭 รัน MRP ไม่ได้: {e}")]

    name_of = {p.get("sku"): p.get("name", "") for p in db.get("products", [])}
    out = []
    for e in plan.get("exceptions", []):
        kind = e.get("kind", "")
        sku = e.get("sku") or e.get("wo") or ""
        out.append(_f(f"mrp:{kind}:{sku}", "mrp", kind, _MRP_SEV.get(kind, "medium"),
                      sku, name_of.get(sku, ""), "🏭 " + e.get("message", ""),
                      **(e.get("data") or {})))

    n = plan.get("summary", {}).get("planned_orders", 0)
    if n:
        cost = plan.get("summary", {}).get("est_total_cost", 0)
        out.append(_f("mrp:plan", "mrp", "plan_summary", "info", "", "",
                      f"🏭 MRP แนะนำสั่งซื้อ {n} รายการ (~฿{cost:,.0f}) "
                      f"— ไปที่เมนู วางแผนวัตถุดิบ (MRP) เพื่อสร้างใบขอซื้อ",
                      planned=n))
    out.sort(key=lambda x: SEV_ORDER[x["severity"]])
    return out[: cfg.max_findings_per_rule]


# ════════════════════════════════════════════════════════════
def run_all(db: dict, cfg: Config, now: datetime | None = None) -> list[dict]:
    findings = []
    if cfg.rule_low_stock:
        findings += check_low_stock(db, cfg)
    if cfg.rule_velocity:
        findings += check_velocity(db, cfg, now)
    if cfg.rule_anomaly:
        findings += check_anomaly(db, cfg)
    if getattr(cfg, "rule_mrp", True):
        findings += check_mrp(db, cfg)
    return findings
