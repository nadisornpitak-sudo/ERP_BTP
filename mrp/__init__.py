"""BTP ERP — MRP (Material Requirements Planning) engine.

Planning layer: BOM explosion → netting → lot-sizing → lead-time offset
→ planned orders + exceptions. Pure functions, testable (see tests.py).

อ่าน db เดียวกับ agent (data/btp_erp.json). ไม่แก้ข้อมูล — แค่วางแผน.
"""

__version__ = "1.0.0"
