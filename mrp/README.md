# 🏭 BTP ERP — MRP Engine (Material Requirements Planning)

Planning layer (Phase 1–2 ของบลูพรินต์ MRP) — วางแผนสั่งซื้อวัตถุดิบจาก
**BOM + สต็อก W + PO ค้าง + แผนผลิต (WO)** เป็น pure function เทสต์ได้
ขนานกับโมดูล `agent/`

```
BOM explosion → netting → lot sizing → lead-time offset
             → planned orders (Auto-PR) + exceptions
```

## รัน

```bash
# จากโฟลเดอร์ BTP-ERP-Server/
python -m mrp.run            # รัน 1 รอบ → mrp/reports/latest.md + data/mrp_state.json
python -m mrp.run --json     # พิมพ์ผล JSON
python -m mrp.run --no-min-max   # วางแผนเฉพาะ demand จริง (ปิด Min-Max)
python -m mrp.tests          # ชุดทดสอบ (28 เคส)
```

> อ่าน `data/btp_erp.json` อย่างเดียว — **ไม่แก้ข้อมูล** ผลลัพธ์เป็นข้อเสนอ ยังไม่สร้าง PO/PR จริง

## ตรรกะหลัก (engine.py)

| ขั้น | สูตร / กฎ |
|---|---|
| Gross Requirement | `Σ BOM_qty × (WO.qty − producedQty) × (1 + scrap%)` จาก WO สถานะ ร่าง/กำลังผลิต |
| Scheduled Receipts | PO สถานะ `รอรับ`; ETA = `po.date + leadTime` → ทันใช้ ถ้า ETA ≤ need date |
| Net Requirement | `max(0, SafetyStock + GR − OnHand(W) − SR_ontime)` |
| Safety Stock / ROP | `SS = avgDailyUsage × safetyDays` · `ROP = avgDailyUsage × leadDays + SS` |
| Lot sizing | JIT = lot-for-lot · อื่น ๆ = `max(NR, MOQ)` ปัดขึ้นตาม pack multiple |
| Lead-time offset | `release = needDate − (leadDays + safetyDays)`; ถ้า `needDate−วันนี้ < leadDays` → **expedite** |

**Exceptions:** `shortage` (ขาดแม้รวม PO) · `late_po` (PO มาช้ากว่ากำหนดใช้) ·
`expedite` (สั่งปกติไม่ทัน) · `partial_build` (ผลิตได้บางส่วน = max buildable) ·
`orphan_component` (BOM อ้าง SKU ไม่มีจริง)

## Material class & default planning params (config.py)

แยกอัตโนมัติจาก prefix ของ SKU; ปรับรายตัวได้ด้วยฟิลด์ override บน product
(`leadTime_days`, `moq`, `packMultiple`, `scrapPct`, `shelfLife_days`, `safety_days`, `vendorId`)

| Class | SKU | lead | safety | scrap | MOQ | pack | นโยบาย |
|---|---|--:|--:|--:|--:|--:|---|
| BULK (น้ำหอมเข้มข้น) | `BU*` | 30 | 14 | 4% | 1 | 1 | block partial; **shelf-life cap (เฉพาะ min-max)** |
| PRIMARY (ขวด/ฝา) | `PK04/05/06*` | 21 | 10 | 2% | 500 | 100 | EOQ/min-max |
| SECONDARY (กล่อง/ฉลาก) | `PK00/01/02/07*` | 7 | 3 | 5% | 200 | 50 | **JIT** lot-for-lot |

> **Shelf-life cap** จำกัดเฉพาะการเติมแบบ min-max (speculative) เท่านั้น —
> ไม่บล็อกการสั่งเพื่อ demand จริงของ WO ที่ยืนยันแล้ว

## โครงสร้าง

```
mrp/
├── engine.py    ← netting engine (pure: mrp_run(db,cfg,now))
├── config.py    ← material classes + default planning params
├── master.py    ← รวม override สินค้า + class default
├── report.py    ← markdown + JSON
├── run.py       ← entry point (อ่าน btp_erp.json)
├── tests.py     ← 28 unit tests (ไม่พึ่ง pytest)
└── reports/     ← ผลลัพธ์ (gitignored)
```

## สถานะ
- ✅ **Phase 1** — master-data fields (graceful override + class defaults)
- ✅ **Phase 2** — netting engine + 28 unit tests
- ✅ **Phase 3** — สร้าง `db.materialReqs[]` (status รอดำเนินการ, `source:'MRP'`, กันซ้ำ) ผ่านปุ่มในหน้า MRP
- ✅ **Phase 4 (บางส่วน)** — หน้า "🏭 วางแผนวัตถุดิบ (MRP)" บน ERP + endpoint `GET /api/mrp/plan` (รัน engine ฝั่ง server)

### ถัดไป (ยังไม่ทำ)
- เพิ่มกฎ `shortage` / `late_po` ให้ **Agent monitor** (โผล่บนหน้า 🤖 ผู้ช่วย Agent)
- เติมฟิลด์ master-data planning ลงสินค้าวัตถุดิบจริง (แทนค่า default ตาม class)
- ตั้งเวลา MRP อัตโนมัติ (cron/loop) + แจ้งเตือนเมื่อมี expedite

## หน้าใช้งานบน ERP
เมนู **การจัดซื้อ → 🏭 วางแผนวัตถุดิบ (MRP)**: ดู Planned Orders + Exceptions →
เลือกรายการ → **➕ สร้างใบขอซื้อ** → ไปต่อที่ ใบขอซื้อ (MR) เพื่ออนุมัติ → สร้าง PO
