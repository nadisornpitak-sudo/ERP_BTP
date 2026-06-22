"""สร้างรายงาน MRP — markdown + JSON ลง mrp/reports/."""
from __future__ import annotations
from datetime import datetime
import json

from .config import Config, REPORTS_DIR

STATUS_TH = {"expedite": "🔴 เร่งด่วน (สั่งไม่ทัน)", "release_now": "🟠 สั่งเลย", "future": "🔵 สั่งภายหลัง"}
KIND_TH = {"shortage": "ขาดวัตถุดิบ", "late_po": "PO มาช้า", "expedite": "ต้องเร่ง",
           "partial_build": "ผลิตได้บางส่วน", "orphan_component": "BOM อ้าง SKU ไม่มีจริง"}
SEV_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}


def render_markdown(result: dict) -> str:
    s = result["summary"]
    L = ["# 🏭 BTP ERP — MRP Plan",
         f"_{result['generated_at'][:19]} • horizon {result['horizon_today']}_\n",
         "## สรุป",
         f"- คำสั่งซื้อที่แนะนำ (Planned Orders): **{s['planned_orders']}** "
         f"• มูลค่าประมาณ ฿{s['est_total_cost']:,.0f}",
         f"- ตามสถานะ: {s['by_status'] or '—'}",
         f"- ข้อยกเว้น (Exceptions): **{s['exceptions']}** → {s['exceptions_by_kind'] or '—'}\n"]

    if result["exceptions"]:
        L.append("## ⚠️ ข้อยกเว้นที่ต้องจัดการ")
        L.append("| ระดับ | ชนิด | SKU/WO | รายละเอียด |")
        L.append("|---|---|---|---|")
        for e in result["exceptions"]:
            em = SEV_EMOJI.get(e["severity"], "")
            who = e.get("sku") or e.get("wo") or "—"
            L.append(f"| {em} | {KIND_TH.get(e['kind'], e['kind'])} | {who} | {e['message']} |")
        L.append("")

    if result["planned_orders"]:
        L.append("## 📦 คำสั่งซื้อที่แนะนำ (Auto Purchase Requisition)")
        L.append("| สถานะ | SKU | ชื่อ | คลาส | ต้องใช้ | คงเหลือ | PO ค้าง | สั่ง | ใช้ภายใน | มูลค่า฿ |")
        L.append("|---|---|---|---|--:|--:|--:|--:|---|--:|")
        for o in result["planned_orders"]:
            L.append(f"| {STATUS_TH.get(o['status'], o['status'])} | {o['sku']} | "
                     f"{(o['name'] or '')[:24]} | {o['class']} | {o['gross_req']:g} | "
                     f"{o['on_hand']} | {o['scheduled_receipts']} | **{o['order_qty']}** | "
                     f"{o['need_date']} | {o['est_cost']:,.0f} |")
        L.append("")
    else:
        L.append("## 📦 คำสั่งซื้อที่แนะนำ\n_— ไม่มี: วัตถุดิบเพียงพอตามแผนผลิตปัจจุบัน —_\n")

    return "\n".join(L)


def write_reports(result: dict) -> dict:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md = render_markdown(result)
    (REPORTS_DIR / f"mrp_{stamp}.md").write_text(md, encoding="utf-8")
    (REPORTS_DIR / "latest.md").write_text(md, encoding="utf-8")
    (REPORTS_DIR / f"mrp_{stamp}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"latest": str(REPORTS_DIR / "latest.md")}
