# 🤖 BTP ERP — Agent Feedback Loop

Agent แบบ **rule-based** ที่คอยเฝ้าคลังสินค้าเป็นรอบ ๆ:

```
observe → analyze → diff (เทียบรอบก่อน) → act → report → sleep → วนใหม่
```

"feedback" คือ **ทุกรอบจะจำผลรอบก่อน** แล้วบอกว่าอะไร *ใหม่ / คลี่คลายแล้ว / ยังค้าง*
ไม่เตือนซ้ำของเดิมรัว ๆ

## เฝ้าอะไรบ้าง (3 กฎ)

| กฎ | ตรวจ |
|---|---|
| **low_stock** | หน้าขาย (S+D+O) หมด → ⛔ `stockout` • ของรวม ≤ จุดสั่งซื้อ → 📦 `reorder` • หน้าขายต่ำแต่คลัง W ยังมี → 🔄 `transfer` |
| **velocity** | ขายเร็วจนจะหมดใน N วัน → 🔥 `fast_mover` • ค้างนานไม่ขยับ → 🪦 `dead_stock` (อิงจาก `moves` ชนิด `sale`) |
| **anomaly** | สต็อกติดลบ • SKU ซ้ำ • move อ้าง SKU ที่ไม่มีจริง • move ปริมาณสูงผิดปกติ |

## ผลลัพธ์ไป 2 ที่ (Both)

1. **ไฟล์รายงาน** → `agent/reports/` (`report_<เวลา>.md` + `.json` และ `latest.md`)
2. **เขียนกลับ ERP** → `data/agent_state.json` (mode local) หรือ `POST /api/agent/state` (mode http)

## วิธีรัน

### โหมด local (ง่ายสุด — รันได้เลย ไม่ต้องมี token)
อ่านไฟล์ `data/btp_erp.json` ตรง ๆ บนเครื่องเดียวกับ server

```bash
# จากในโฟลเดอร์ BTP-ERP-Server/
python -m agent.loop --once            # ตรวจ 1 รอบ
python -m agent.loop --interval 900    # วนทุก 15 นาที (Ctrl+C เพื่อหยุด)
```
หรือ **ดับเบิลคลิก `agent/run_agent.bat`** (Windows) → ดูผลที่ `agent/reports/latest.md`

### โหมด http (ระยะไกล / บน Railway / เปิด auto-sync ได้)
1. สร้าง API token: เข้า ERP → ระบบ → API Tokens (หรือ `POST /api/tokens`) ได้ค่า `btp_...`
2. รัน:
```bash
python -m agent.loop --mode http --base-url http://localhost:8000 --token btp_xxx --once
python -m agent.loop --mode http --base-url https://btp-erp-server.up.railway.app --token btp_xxx --auto-sync
```

## ตั้งค่าเกณฑ์ (ผ่าน env หรือแก้ `config.py`)

| env | default | ความหมาย |
|---|---|---|
| `BTP_AGENT_INTERVAL` | 900 | วินาที/รอบ |
| `BTP_AGENT_DEFAULT_REORDER` | 20 | จุดสั่งซื้อ เมื่อสินค้าไม่ได้ตั้ง `reorder` |
| `BTP_AGENT_VELOCITY_WINDOW` | 30 | วันที่ใช้คำนวณยอดขายเฉลี่ย |
| `BTP_AGENT_COVER_DAYS` | 7 | เหลือพอขาย < N วัน = เร่งด่วน |
| `BTP_AGENT_DEAD_WINDOW` | 90 | ไม่ขยับเกิน N วัน = dead stock |
| `BTP_AGENT_DEAD_MIN_QTY` | 50 | ค้าง ≥ เท่านี้ ถึงนับ dead stock |
| `BTP_AGENT_AUTO_SYNC` | false | sync TikTok ทุกรอบ (เฉพาะ mode=http) |
| `BTP_AGENT_RULE_*` | true | เปิด/ปิดกฎ: `LOW_STOCK` / `VELOCITY` / `ANOMALY` |
| `BTP_AGENT_TRACK_PREFIXES` | `FG` | ติดตามสต็อกเฉพาะ prefix นี้ (คั่นด้วย `,`) — กัน BU*/PK* (วัตถุดิบ/บรรจุภัณฑ์) |
| `BTP_AGENT_SKIP_KEYWORDS` | `ห้ามจำหน่าย,Reserved,Reser` | ข้ามสินค้าที่ชื่อมีคำเหล่านี้ |

> **ขอบเขตการติดตาม:** กฎ `low_stock` และ `velocity` ดูเฉพาะ **สินค้าสำเร็จรูป (FG\*)**
> ที่ตั้งใจขาย (กรอง `ห้ามจำหน่าย/Reserved` ออก) — กันสัญญาณรบกวนจากวัตถุดิบ/บรรจุภัณฑ์
> ที่คุมผ่าน BOM อยู่แล้ว ส่วนกฎ `anomaly` ตรวจ **ทุก SKU** (สต็อกติดลบ/ซ้ำสำคัญทุกที่)
> ปรับขอบเขตได้ที่ `BTP_AGENT_TRACK_PREFIXES`

## โครงสร้าง

```
agent/
├── loop.py        ← entry point (วงจร observe→act)
├── rules.py       ← 3 กฎ (pure functions)
├── erp_client.py  ← อ่าน/เขียน ERP (local + http)
├── report.py      ← สร้าง markdown + JSON + diff รอบก่อน
├── config.py      ← เกณฑ์ทั้งหมด
├── run_agent.bat  ← ดับเบิลคลิกรัน 1 รอบ
└── reports/       ← ผลลัพธ์ (gitignored)
```

> ℹ️ `sales` ในระบบยังว่าง และ `moves` ยังไม่มีชนิด `sale` → กฎ velocity จะแจ้ง
> "ยังไม่มีข้อมูลการขาย" และเริ่มทำงานเองเมื่อมีการขายเข้าระบบ
