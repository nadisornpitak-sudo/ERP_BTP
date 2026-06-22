"""
Import Quarantine Stock channels from CSV to BTP ERP
"""
import json
import shutil
import csv
import sys
from datetime import datetime
from pathlib import Path

# Paths
SERVER_DIR = Path(__file__).parent
DATA_DIR = SERVER_DIR / "data"
DB_FILE = DATA_DIR / "btp_erp.json"
CSV_FILE = Path("c:/Users/User/Desktop/สรุป STOCK  17.06.2569.csv")

def main():
    if not CSV_FILE.exists():
        print(f"❌ Error: ไม่พบไฟล์ {CSV_FILE}")
        sys.exit(1)
        
    if not DB_FILE.exists():
        print(f"❌ Error: ไม่พบไฟล์ฐานข้อมูลที่ {DB_FILE}")
        sys.exit(1)
        
    print(f"📂 กำลังเปิดไฟล์: {CSV_FILE.name}")
    csv_rows = []
    with open(CSV_FILE, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        csv_rows = list(reader)
        
    def to_int(val):
        if not val or val.strip() in ["", "-", " -"]:
            return 0
        return int(val.replace(",", "").replace(" ", "").strip())
        
    with open(DB_FILE, "r", encoding="utf-8") as f:
        db = json.load(f)
        
    prod_map = {p["sku"]: p for p in db.get("products", [])}
    
    updated_count = 0
    for r in csv_rows[6:]:
        if len(r) < 7:
            continue
        sku = r[1].strip()
        if not sku.startswith("FG"):
            continue
            
        s_val = to_int(r[3])
        d_val = to_int(r[4])
        o_val = to_int(r[5])
        
        if sku in prod_map:
            prod = prod_map[sku]
            prod["stock"] = {
                "S": s_val,
                "D": d_val,
                "O": o_val,
                "W": 0,
                "Q": 0,
                "F": 0
            }
            updated_count += 1
            
    if updated_count > 0:
        # Create backup
        backup_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = DATA_DIR / f"backup_pre_quarantine_{backup_ts}.json"
        shutil.copy2(DB_FILE, backup_file)
        
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
        print(f"✅ ซิงค์ยอดสต็อกห้ามจำหน่ายสำเร็จ: {updated_count} SKUs")
    else:
        print("❌ ไม่พบข้อมูลที่ตรงกันเพื่ออัปเดต")

if __name__ == "__main__":
    main()
