"""Config สำหรับ agent loop — อ่านจาก env ได้ทั้งหมด มี default ที่ใช้ได้ทันที.

ตัวแปร env (ขึ้นต้นด้วย BTP_AGENT_):
  BTP_AGENT_MODE         local | http        (default: local)
  BTP_AGENT_BASE_URL     URL ของ ERP server  (default: http://localhost:8000)
  BTP_AGENT_TOKEN        API token (btp_...)  — ใช้เฉพาะ mode=http
  BTP_AGENT_INTERVAL     วินาที/รอบ           (default: 900 = 15 นาที)
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import os

# โฟลเดอร์ data/ ของ ERP (agent/ อยู่ใต้ BTP-ERP-Server/)
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# ช่องสต็อก
SELLABLE = ("S", "D", "O")   # หน้าขาย: Shop / Dealer / Online (ถูกตัดเมื่อขาย)
BACKSTOCK = ("W",)            # คลังสำรอง (Warehouse)
EXCLUDED = ("Q", "F")        # Quarantine / Reserved — ไม่นับเป็นของพร้อมใช้


def _envf(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _envi(key: str, default: int) -> int:
    return int(_envf(key, default))


def _envb(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    # ── การเชื่อมต่อ ──
    mode: str = field(default_factory=lambda: os.environ.get("BTP_AGENT_MODE", "local"))
    base_url: str = field(default_factory=lambda: os.environ.get("BTP_AGENT_BASE_URL", "http://localhost:8000").rstrip("/"))
    token: str = field(default_factory=lambda: os.environ.get("BTP_AGENT_TOKEN", ""))
    interval: int = field(default_factory=lambda: _envi("BTP_AGENT_INTERVAL", 900))

    # ── เปิด/ปิดกฎ ──
    rule_low_stock: bool = field(default_factory=lambda: _envb("BTP_AGENT_RULE_LOW_STOCK", True))
    rule_velocity: bool = field(default_factory=lambda: _envb("BTP_AGENT_RULE_VELOCITY", True))
    rule_anomaly: bool = field(default_factory=lambda: _envb("BTP_AGENT_RULE_ANOMALY", True))
    rule_mrp: bool = field(default_factory=lambda: _envb("BTP_AGENT_RULE_MRP", True))
    auto_sync: bool = field(default_factory=lambda: _envb("BTP_AGENT_AUTO_SYNC", False))

    # ── เกณฑ์: low stock ──
    # ถ้า reorder ของสินค้า = 0 ให้ใช้ค่านี้แทน (ตรงกับ settings.lowStock = 20)
    default_reorder: int = field(default_factory=lambda: _envi("BTP_AGENT_DEFAULT_REORDER", 20))

    # ── เกณฑ์: sales velocity ──
    velocity_window_days: int = field(default_factory=lambda: _envi("BTP_AGENT_VELOCITY_WINDOW", 30))
    cover_days: int = field(default_factory=lambda: _envi("BTP_AGENT_COVER_DAYS", 7))     # จะหมดใน N วัน = เร่งด่วน
    dead_window_days: int = field(default_factory=lambda: _envi("BTP_AGENT_DEAD_WINDOW", 90))
    dead_min_qty: int = field(default_factory=lambda: _envi("BTP_AGENT_DEAD_MIN_QTY", 50))  # นับเป็น dead stock เมื่อค้าง >= นี้

    # ── เกณฑ์: anomaly ──
    anomaly_qty: int = field(default_factory=lambda: _envi("BTP_AGENT_ANOMALY_QTY", 10000))  # move ใหญ่ผิดปกติ

    # ── ขอบเขตสินค้าที่ "ติดตามสต็อก" (low_stock + velocity) ──
    # ติดตามเฉพาะสินค้าสำเร็จรูป (FG*) — ไม่รวมวัตถุดิบ/บรรจุภัณฑ์ (BU*, PK*) ที่คุมผ่าน BOM
    # และข้ามสินค้าที่ตั้งใจไม่ขาย (ชื่อมีคำว่า "ห้ามจำหน่าย"/"Reserved")
    track_prefixes: tuple = field(default_factory=lambda: tuple(
        x.strip() for x in os.environ.get("BTP_AGENT_TRACK_PREFIXES", "FG").split(",") if x.strip()))
    skip_keywords: tuple = field(default_factory=lambda: tuple(
        x.strip() for x in os.environ.get("BTP_AGENT_SKIP_KEYWORDS", "ห้ามจำหน่าย,Reserved,Reser").split(",") if x.strip()))

    # ── รายงาน ──
    max_findings_per_rule: int = field(default_factory=lambda: _envi("BTP_AGENT_MAX_FINDINGS", 50))
    history_limit: int = field(default_factory=lambda: _envi("BTP_AGENT_HISTORY_LIMIT", 50))

    def summary(self) -> dict:
        return asdict(self)
