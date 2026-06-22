"""
Import Rack Locations and Stock levels (including floor placements) to BTP ERP
Usage: python import_rack_stock.py <path_to_csv_or_excel>

This script will:
1. Read a CSV or Excel file containing SKU, Rack/Slot ID, stock counts, Floor Locations, and Floor Quantities.
2. Update the local BTP ERP database (data/btp_erp.json).
3. Back up the previous database state.
"""
import sys
import json
import shutil
import csv
from datetime import datetime
from pathlib import Path

# Paths
SERVER_DIR = Path(__file__).parent
DATA_DIR = SERVER_DIR / "data"
DB_FILE = DATA_DIR / "btp_erp.json"

def load_data():
    if not DB_FILE.exists():
        print(f"❌ Error: ไม่พบไฟล์ฐานข้อมูลที่ {DB_FILE}")
        sys.exit(1)
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    # Create backup first
    backup_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = DATA_DIR / f"backup_pre_import_{backup_ts}.json"
    shutil.copy2(DB_FILE, backup_file)
    print(f"💾 สำรองข้อมูลเดิมไว้ที่: {backup_file.name}")
    
    # Save new database
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("✅ บันทึกข้อมูลลงฐานข้อมูล (btp_erp.json) เรียบร้อยแล้ว!")

def read_input_file(file_path):
    suffix = file_path.suffix.lower()
    rows = []
    
    if suffix == ".csv":
        # Read as CSV
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    rows.append(dict(r))
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="tis-620") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    rows.append(dict(r))
    elif suffix in [".xlsx", ".xls"]:
        # Try to read as Excel using pandas if installed
        try:
            import pandas as pd
            df = pd.read_excel(file_path)
            df = df.fillna("")
            rows = df.to_dict(orient="records")
        except ImportError:
            print("❌ Error: หากต้องการใช้ไฟล์ Excel (.xlsx) กรุณาติดตั้ง pandas และ openpyxl ก่อน:")
            print("   pip install pandas openpyxl")
            print("   หรือแปลงไฟล์ Excel เป็น .csv (UTF-8) แล้วรันใหม่อีกครั้ง")
            sys.exit(1)
    else:
        print(f"❌ Error: ไม่รองรับไฟล์นามสกุล {suffix} (รองรับเฉพาะ .csv หรือ .xlsx)")
        sys.exit(1)
        
    return rows

def find_column(headers, matches):
    for h in headers:
        h_clean = str(h).strip().lower().replace("_", "").replace(" ", "")
        for m in matches:
            if m in h_clean:
                return h
    return None

def parse_rows(rows):
    if not rows:
        print("❌ Error: ไม่พบแถวข้อมูลในไฟล์")
        sys.exit(1)
        
    headers = list(rows[0].keys())
    
    # Map headers dynamically
    col_sku = find_column(headers, ["sku", "รหัสสินค้า", "รหัส"])
    col_slot = find_column(headers, ["slot", "location", "rack", "ตำแหน่ง", "แร็ค", "พิกัด"])
    col_s = find_column(headers, ["stocks", "shop", "s", "หน้าร้าน", "หน้าร้านs"])
    col_d = find_column(headers, ["stockd", "dealer", "d", "ดีลเลอร์", "ดีลเลอร์d"])
    col_o = find_column(headers, ["stocko", "online", "o", "ออนไลน์", "ออนไลน์o"])
    col_floor_loc = find_column(headers, ["floorlocation", "floorslot", "floorloc", "ตำแหน่งพื้น", "ที่พื้น", "พื้น"])
    col_floor_qty = find_column(headers, ["floorqty", "floorquantity", "floorstock", "จำนวนพื้น", "สต็อกพื้น"])
    
    if not col_sku:
        print(f"❌ Error: ไม่พบคอลัมน์สำหรับ SKU ในหัวตาราง: {headers}")
        sys.exit(1)
        
    print("📊 คอลัมน์ที่ตรวจพบ:")
    print(f"   • SKU: {col_sku}")
    print(f"   • Slot/Rack: {col_slot or 'ไม่พบ'}")
    print(f"   • Stock Shop (S): {col_s or 'ไม่พบ'}")
    print(f"   • Stock Dealer (D): {col_d or 'ไม่พบ'}")
    print(f"   • Stock Online (O): {col_o or 'ไม่พบ'}")
    print(f"   • Floor Location: {col_floor_loc or 'ไม่พบ'}")
    print(f"   • Floor Qty: {col_floor_qty or 'ไม่พบ'}")
    
    parsed_data = []
    for r in rows:
        sku = str(r[col_sku]).strip()
        if not sku or sku.lower() == "nan":
            continue
            
        slot = str(r[col_slot]).strip() if col_slot and r[col_slot] else None
        
        # Read stock counts
        s_val = int(float(r[col_s])) if col_s and r[col_s] != "" else None
        d_val = int(float(r[col_d])) if col_d and r[col_d] != "" else None
        o_val = int(float(r[col_o])) if col_o and r[col_o] != "" else None
        
        # Read floor info
        floor_loc = str(r[col_floor_loc]).strip() if col_floor_loc and r[col_floor_loc] != "" else None
        floor_qty = int(float(r[col_floor_qty])) if col_floor_qty and r[col_floor_qty] != "" else None
        
        parsed_data.append({
            "sku": sku,
            "slot": slot,
            "stock_s": s_val,
            "stock_d": d_val,
            "stock_o": o_val,
            "floor_location": floor_loc,
            "floor_qty": floor_qty
        })
        
    return parsed_data

def main():
    if len(sys.argv) < 2:
        print("วิธีใช้: python import_rack_stock.py <path_to_csv_or_excel>")
        sys.exit(1)
        
    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"❌ Error: ไม่พบไฟล์ข้อมูล {file_path}")
        sys.exit(1)
        
    print(f"📂 อ่านไฟล์ข้อมูล: {file_path.name}")
    raw_rows = read_input_file(file_path)
    parsed_items = parse_rows(raw_rows)
    print(f"   ตรวจพบรายการในไฟล์ทั้งหมด {len(parsed_items)} SKU\n")
    
    db = load_data()
    db.setdefault("locations", {})
    
    # Index products by SKU for quick lookup
    prod_map = {p["sku"]: p for p in db.get("products", [])}
    
    updated_stock_count = 0
    updated_slot_count = 0
    updated_floor_count = 0
    sku_not_found = []
    
    for item in parsed_items:
        sku = item["sku"]
        if sku not in prod_map:
            sku_not_found.append(sku)
            continue
            
        prod = prod_map[sku]
        
        # Update stock counts if provided
        prod_stock = prod.setdefault("stock", {"S": 0, "D": 0, "O": 0, "W": 0, "Q": 0, "F": 0})
        stock_updated = False
        
        if item["stock_s"] is not None:
            prod_stock["S"] = item["stock_s"]
            stock_updated = True
        if item["stock_d"] is not None:
            prod_stock["D"] = item["stock_d"]
            stock_updated = True
        if item["stock_o"] is not None:
            prod_stock["O"] = item["stock_o"]
            stock_updated = True
            
        if stock_updated:
            updated_stock_count += 1
            
        # Update rack slot if provided
        if item["slot"]:
            db["locations"][sku] = item["slot"]
            updated_slot_count += 1
            
        # Update floor stock/location if provided
        floor_updated = False
        if item["floor_location"] is not None:
            prod["floor_location"] = item["floor_location"]
            floor_updated = True
        if item["floor_qty"] is not None:
            prod["floor_qty"] = item["floor_qty"]
            floor_updated = True
            
        if floor_updated:
            updated_floor_count += 1
            
    print(f"📈 ผลการดำเนินงาน:")
    print(f"   • อัปเดตยอดสต็อกบนแร็คสำเร็จ: {updated_stock_count} SKUs")
    print(f"   • อัปเดตตำแหน่งแร็คสำเร็จ: {updated_slot_count} SKUs")
    print(f"   • อัปเดตตำแหน่งและสต็อกบนพื้นสำเร็จ: {updated_floor_count} SKUs")
    
    if sku_not_found:
        print(f"   ⚠️ ไม่พบรหัสสินค้าใน ERP ({len(sku_not_found)} SKUs): {', '.join(sku_not_found[:10])}...")
        
    # Save the updated database
    save_data(db)
    
    print("\n💡 ขั้นตอนถัดไป:")
    print("1. หากรันในเครื่องตัวเอง (Local): เปิดเบราว์เซอร์ไปที่ http://localhost:8000 ได้ทันที")
    print("2. หากต้องการนำขึ้นเซิร์ฟเวอร์ (Railway):")
    print("   รันคำสั่ง: python upload_data_to_railway.py เพื่อโอนข้อมูลขึ้นคลาวด์")

if __name__ == "__main__":
    main()
