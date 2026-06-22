"""Config + default planning parameters ต่อ material class.

Phase 1 (master-data) ทำแบบ graceful: ถ้า product มีฟิลด์ override
(leadTime_days, moq, packMultiple, scrapPct, shelfLife_days, vendorId)
จะใช้ค่านั้น ไม่มีก็ตกมาใช้ค่า default ตาม class ด้านล่าง โดยไม่ต้องแก้
ข้อมูลสินค้า 1588 รายการ.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

# agent/ และ mrp/ อยู่ใต้ BTP-ERP-Server/ — data อยู่ที่ ../data
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_FILE = DATA_DIR / "btp_erp.json"
STATE_FILE = DATA_DIR / "mrp_state.json"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# ช่องสต็อกที่ถือว่าเป็น "ของพร้อมใช้ในคลังวัตถุดิบ"
ONHAND_CHANNELS = ("W",)

# WO สถานะที่ยังสร้าง demand
OPEN_WO_STATUS = ("ร่าง", "กำลังผลิต")
# PO สถานะที่ยังเป็น scheduled receipt
OPEN_PO_STATUS = ("รอรับ",)

# ── พารามิเตอร์วางแผนต่อ material class ──
# lead_days     : lead time ผู้ขาย (วัน)
# safety_days   : วันสำรอง (ใช้คำนวณ safety stock = avg_daily × safety_days)
# scrap         : สัดส่วนสูญเสีย (เติมลงใน gross requirement)
# moq           : ขั้นต่ำสั่งซื้อ
# pack          : ทวีคูณการสั่ง (carton/pallet)
# jit           : True = lot-for-lot (สั่งเท่าที่ขาด) / False = min(max(nr,moq))
# block_partial : True = ห้ามผลิตบางส่วน (เช่น น้ำหอมเข้มข้น ต้องครบสูตร)
# shelf_life    : อายุเก็บ (วัน) — ใช้จำกัดไม่ให้สั่งเกินที่ใช้ทันก่อนหมดอายุ
CLASS_DEFAULTS = {
    "BULK":      dict(lead_days=30, safety_days=14, scrap=0.04, moq=1,   pack=1,   jit=False, block_partial=True,  shelf_life=540),
    "PRIMARY":   dict(lead_days=21, safety_days=10, scrap=0.02, moq=500, pack=100, jit=False, block_partial=False, shelf_life=None),
    "SECONDARY": dict(lead_days=7,  safety_days=3,  scrap=0.05, moq=200, pack=50,  jit=True,  block_partial=False, shelf_life=None),
    "OTHER":     dict(lead_days=14, safety_days=5,  scrap=0.02, moq=1,   pack=1,   jit=False, block_partial=False, shelf_life=None),
}


def classify(sku: str) -> str:
    """แยก material class จาก prefix ของ SKU (ตาม taxonomy ของ BTP)."""
    s = (sku or "").upper()
    if s.startswith("FG"):
        return "FG"            # สินค้าสำเร็จรูป — ไม่ซื้อ เป็นต้นทาง demand
    if s.startswith("BU"):
        return "BULK"          # น้ำหอมเข้มข้น / หัวเชื้อ
    if s.startswith(("PK04", "PK05", "PK06")):
        return "PRIMARY"       # ขวด/ฝา/หัวปั๊ม (primary packaging)
    if s.startswith("PK"):
        return "SECONDARY"     # กล่อง/ฉลาก/การ์ด (secondary packaging)
    return "OTHER"


@dataclass
class Config:
    usage_window_days: int = 90      # หน้าต่างคำนวณ avg daily usage จาก moves type=issue
    max_planned_orders: int = 200
    max_exceptions: int = 200
    # เปิด/ปิดนโยบาย
    enable_min_max: bool = True      # สั่งเติมเมื่อ on-hand ≤ ROP แม้ไม่มี WO demand
    enable_shelf_cap: bool = True    # จำกัดปริมาณสั่ง BULK ตามอายุเก็บ
    class_defaults: dict = field(default_factory=lambda: {k: dict(v) for k, v in CLASS_DEFAULTS.items()})
