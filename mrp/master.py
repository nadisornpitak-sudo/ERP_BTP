"""Master-data resolver — รวม override ระดับสินค้า (ถ้ามี) กับ default ตาม class.

Phase 1 fields (optional) บน product:
  leadTime_days, moq, packMultiple, scrapPct, shelfLife_days, vendorId
ถ้าไม่มีฟิลด์เหล่านี้ จะใช้ค่าจาก CLASS_DEFAULTS ตาม classify(sku).
"""
from __future__ import annotations
from .config import Config, classify


def attrs(product: dict, cfg: Config) -> dict:
    """คืน planning attributes ของ component หนึ่งตัว (override > class default)."""
    sku = product.get("sku", "")
    cls = classify(sku)
    base = cfg.class_defaults.get(cls, cfg.class_defaults["OTHER"])

    def pick(field_name, default):
        v = product.get(field_name)
        return v if v is not None else default

    return {
        "sku": sku,
        "name": product.get("name", sku),
        "class": cls,
        "lead_days":   int(pick("leadTime_days", base["lead_days"])),
        "safety_days": int(pick("safety_days",   base["safety_days"])),
        "scrap":       float(pick("scrapPct",     base["scrap"])),
        "moq":         int(pick("moq",            base["moq"])),
        "pack":        int(pick("packMultiple",   base["pack"])),
        "jit":         bool(pick("jit",           base["jit"])),
        "block_partial": bool(pick("blockPartial", base["block_partial"])),
        "shelf_life":  pick("shelfLife_days",     base["shelf_life"]),
        "vendor":      product.get("vendorId") or product.get("vendor") or "",
        "cost":        float(product.get("cost", 0) or 0),
    }
